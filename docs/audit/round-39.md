# 第 39 轮：并发与锁的持有边界

## 一、方法与范围

本轮只看一件事：**锁在什么范围内被持有，临界区里做了什么**。判断标准不是「有没有加锁」，而是「加锁之后有没有在锁里等 I/O、等子进程、等回调」。一个 2 秒的写盘放在全局锁里，和没加锁一样会拖垮整站——只是把数据竞争换成了排队。

四个只读域并行审查（只读文件与检索，禁改文件、禁跑测试、禁起服务）：

| 域 | 范围 | 结论 |
| --- | --- | --- |
| T1 索引与数据库 | `library_index.py`（`_sync_lock`、`_rescan_requested`、`replace_snapshot`）、`database.py`（事务与 `busy_timeout`）、`library.py` 扫描、`web/library.py`、`web/search.py` | P0 0 / P1 2 / P2 2 / P3 1 |
| T2 下载与队列 | `downloader.py`（`_lock`、`_persist_locked`、worker、取消与终止）、`url_list.py`、`download_sources.py`、`tiktok_downloader.py`、`web/downloads.py` | P0 0 / P1 1 / P2 6 / P3 2 |
| T3 存储与文件 | `json_storage.py`、`favorites/collections/metadata/media_state/media_sources_store/author_profiles/radio/playback_resume`、`trash.py`、`thumbnail.py`、`auth.py`、`pairing.py` | P0 0 / P1 0 / P2 5 / P3 5 |
| T4 前端并发 | 各页控制器与 `base.js`/`ui_feedback.js`/`i18n.js`：重复点击、请求重入、旧响应覆盖、轮询与操作竞争 | P0 0 / P1 0 / P2 3 / P3 4 |

基线：第 38 轮收尾 `646 passed / 8 skipped`。本轮起点 tag `audit-round-39-start`（`fb6c96b`）。

## 二、问题总览

| 域 | P0 | P1 | P2 | P3 | 合计 | 当轮修复 |
| --- | --- | --- | --- | --- | --- | --- |
| T1 | 0 | 2 | 2 | 1 | 5 | 4 |
| T2 | 0 | 1 | 6 | 2 | 9 | 3 |
| T3 | 0 | 0 | 5 | 5 | 10 | 0 |
| T4 | 0 | 0 | 3 | 4 | 7 | 2 |
| 合计 | **0** | **3** | **16** | **12** | **31** | **9** |

三条 P1 有同一个形状：**把「不该在锁里做的事」放进了锁里**——整轮扫描、整库写入、整份落盘。修复方向也一致：锁内只留「读快照 / 改内存」，锁外做 I/O。

## 三、P0 详述

无。

## 四、P1 详述

### T1-1 保存媒体源时把整轮扫描关在列表锁里

`tiklocal/web/settings.py:176` 的 `_apply_change()` 把 `_apply_change_locked()` 整个包在 `_SOURCES_LOCK` 里，而后者末尾调用 `index.sync()`——一次冷扫描在 19k 文件上要几分钟。这段时间里：另一个标签页保存目录、`/library` 首屏读取源列表、`/settings` 渲染，全部排在同一把锁后面；浏览器先超时，用户看到的是「保存失败」。

修复：锁内只做「读列表 → 校验 → 变换 → 落盘」，返回 `outcome`；`sync()` 移到锁外，由 `_respond_after_change(outcome)` 执行（`settings.py:243-246` 有注释说明原因）。这与第 33 轮 N4-1 的「读-改-写要原子」并不冲突：原子性只需要覆盖读改写，扫描是它的**后果**，不是它的一部分。

### T1-3 整库快照用 3.8 万条单行语句写

`tiklocal/services/library_index.py:44-151` 的 `replace_snapshot()` 对每条记录执行两条 `conn.execute`：一条 23 列的 UPSERT，一条 `media_index_seen` 的 INSERT OR IGNORE。19k 文件 = 3.8 万次 Python↔SQLite 往返，全部在**同一个写事务**里；`media_items` 上还有 12 个索引要维护。写事务一直开着，读连接（每 2.2 s 一次的网格轮询）拿不到锁。

修复：循环只做校验（UTF-8、尾部空格）并累积参数，最后两条 `executemany` 写完（`library_index.py:87`）。语句数从 3.8 万降到 2，写事务的持有时间随之降到一次批量插入的量级。

### T2-1 下载进度落盘在全局任务锁内

`tiklocal/services/downloader.py` 的 `_persist_locked()` 在 `with self._lock:` 内调用 `history_store.save()`，而 `write_json_atomic()` 在 Windows 上撞到文件占用时会做 3 次写重试 + 8 次 `os.replace` 重试，合计睡眠 1.4–2.1 s。调用点全部在锁内：`enqueue`、`cancel`、`retry`、`delete_job`、每次状态迁移、以及每秒一次的进度节流。k 个并发任务 = 每秒 k 次全量重写，每次都把锁按住；下载页每 2.2 s 轮询 `list_jobs`，waitress 只有 8 个线程——一次慢写盘就能让整个队列 API 停摆。

修复：新增 `self._persist_lock`（只串行化写者）与 `_persist()`——在 `_lock` 内只复制快照，出锁后才落盘；`_update_progress()` 改为在锁内记「该写了」，出锁后 `_persist()` 并保留原有的 `except OSError` 兜底（进度写失败不能让下载线程死掉）。其余调用点仍用 `_persist_locked()`：它们是每次任务一次，不是每秒一次。

## 五、P2 一览

### 当轮修复（6 条）

| 编号 | 位置 | 问题 | 修复 |
| --- | --- | --- | --- |
| T1-2 | `library_index.py:1215-1232` | `_rescan_requested` 在锁外置位、只在 `while` 内清；置位时若没有扫描在跑，标志残留 → 下一次 `sync()` 白扫两轮 | 取到 `_sync_lock` 后**先清标志再扫**（注释写明「残留标志会让下一次 sync 走两遍」） |
| T1-4 | `library_index.py`（`delete`/`replace_snapshot`） | 扫描先走完树、再写快照；这期间删掉的文件会被写回去，网格里出现 404 卡片直到下次扫描 | 新增 `_deleted_uris`（`delete()`/`forget_uri()` 记录，`replace_snapshot()` 在插入后与提交前两次剔除） |
| T2-5 | `downloader.py:1556-1570` | 私有 cookie 副本复制失败时 `return cookie_path`，把用户原始 jar 交给 yt-dlp；两个任务共用会被交错写坏（坏的是所有站点的 cookie） | 复制失败直接抛错判任务失败，绝不回退到原始 jar |
| T2-6 | `downloader.py`（`_dispatch_locked`） | `max_concurrent=0` 被当作「不限制」→ 200 条批量一次起 200 个线程 + 200 个子进程 | 保留「0 = 不限」的契约，但夹到 `DOWNLOAD_MAX_CONCURRENT_LIMIT`（16） |
| T4-1 | `download_page_controller.js:502-503,607-647` | 批量删除进行中，勾一下任意复选框（或 2.2 s 重建）就会把删除按钮重新启用，第二次点击对同一批 id 再发一次 POST | 模块级 `deleteSelectionInFlight`：入口挡重入，`updateSelectionUI()` 把它算进 `disabled`，`finally` 复位 |
| T4-3 | `download_page_controller.js:833-865` | 点「删除」后弹确认框，期间列表被轮询重建；确认后 `button.disabled = true` 写进已脱离文档的节点，页面上的新按钮仍可点 → 同一任务删两次 | 按 `jobId` 的 `jobActionBusy` 集合：入口挡重入、`renderHistoryPage()` 重建后按集合恢复 disabled、`finally` 释放 |

### 转 backlog（10 条 P2）

| 编号 | 位置 | 为什么暂不修 |
| --- | --- | --- |
| T2-2 | `downloader.py:828-833` | `retry_job` 的 `index_only` 分支在锁内逐个 `resolve()` + `is_file()`；要改成锁内取副本、锁外 stat，涉及重试路径的状态机，单独一轮做 |
| T2-3 | `downloader.py:973-997` | `cancel()` 在请求线程里同步 `taskkill /T /F`（最坏 22 s），且与 worker 的终止重复；改成「只置事件、由 worker 终止」需要重排取消状态机 |
| T2-4 | `web/downloads.py:253-264`、`tiktok_downloader.py:1092-1118` | 批量入队逐条 `enqueue`，每次一次全量落盘（O(N²)）；需要新增 `enqueue_many()` 并统一两条调用链 |
| T2-8 | `downloader.py:1879-1886` | 只读 GET 在 `set_many` 里整份读写来源映射；改成「只读不落盘 + 后台 flush」会改变 `/api/source` 的可见语义 |
| T3-1 | `thumbnail.py:457`、`:410-413`、`:308` | 临时文件 `.thumb-<pid>-<n>.jpg` 与正式缓存同目录同后缀，`clear_cache()` 的 `glob('*.jpg')` 会删掉在途临时文件 → 生成失败 → 写 `.fail` 让该瓦片吃 1 小时灰块 | 改临时后缀即可，但要同时改 `_replace_with_retry` 与失败标记的判据，单独一轮做 |
| T3-2 | `json_storage.py:55,69` | `write_text` 后直接 `os.replace`，没有 fsync：断电后目标可为零字节，随后被隔离成默认值再覆盖 | 加 fsync 会让每次保存多一次磁盘等待，需要先量测对下载进度落盘的影响 |
| T3-3 | `services/auth.py`（`_save:75-77`、`_load:50`） | 全类无锁；两个并发改密可把 `(旧数据, 新 mtime)` 写进缓存并永久命中 → 旧密码仍有效、新密码被拒 | 加 `RLock` 是一行，但改密路径同时被 `web/settings.py` 与 `run.py` 调用，要连带审一遍调用方 |
| T3-4 | `trash.py:78-80`、`:61` | 二次就地 `write_text('entry.json')` 非原子：截断后条目永久不可见、不过期、`purge` 抛 `KeyError` | 改用 `write_json_atomic` 需要重排 manifest 重写时机，涉及回收站的恢复路径 |
| T3-5 | `author_profiles.py:76-82`、`85-95` | `load_profile` 绕过 `read_json_file`，损坏静默变 `{}` 再被 `save_profile` 覆盖，原 `douyin_id`/`sec_uid` 丢失且无 `.corrupt` 副本 | 与第 34 轮 O3-2 的「版本/损坏」处理方式要统一，单独一轮 |
| T4-2 | `radio_controller.js:563-577`、`:282-307` | `tune()` 无代际号，连点两个电台会让队列混入上一个台的曲目 | 改动落在播放状态机核心，需与 `playNext` 的并发（T4-4）一起改 |


P3 共 12 条（T1 1、T2 2、T3 5、T4 4），全部记入 backlog：`replace_snapshot` 的 `COUNT(*)` 落在写事务内（T1-5）；`url_list.push()` 在模块锁内写网络盘、放弃进程后临时目录残留（T2-9、T2-7）；缩略图 8 桶粗锁跨越整个 ffmpeg、电台元数据 LRU 无锁、`apply_sources` 无锁重建（读者可见「新 sources + 旧 roots」）、`device_auth` 固定 `.tmp`、`collections._quarantine` 只留一份 `.corrupt`（T3-6…T3-10）；`playNext` 空队列并发、`collection_picker.open()` 无重入守卫、`loadAuthorSources` 无代际、合集移除不参与 `organizeBusy`（T4-4…T4-7）。

## 六、实现要点

1. **锁内只做快照，锁外做 I/O**。三处 P1 都是同一形状：`_persist()`、`_apply_change()`、`replace_snapshot()` 现在都遵循这条线——锁保护的是内存状态的一致性，不是磁盘的可用性。
2. **删除要留「意图」**。扫描是「先收集、后写入」，任何在收集之后发生的删除都会被写回。`_deleted_uris` 记的是意图而不是结果，且在插入后与提交前各剔除一次，才能覆盖两种交错顺序。
3. **前端的 busy 要按 id，不按节点**。列表每 2.2 s 整块重建，任何绑在元素上的 `disabled` 都会随节点一起消失；`jobActionBusy` 与 `deleteSelectionInFlight` 都挂在模块作用域。
4. **契约变更要留住旧语义**。`max_concurrent=0` 的「不限制」是既有契约（设置页只发 1/2/4，但配置文件里可能有 0），所以不改成「必须 ≥1」，而是夹到文档写明的上限 16——把无界变成有界，而不是把合法值变成错误。

## 七、验证

- 新增 `tests/test_round39_locking.py`（3 条，全绿 1.44 s）：
  - `test_a_snapshot_collected_before_a_delete_does_not_resurrect_the_row`——快照先收集、删除后写入，行不能复活；且意图被消费掉之后，下一次真实扫描仍能正常索引；
  - `test_replace_snapshot_writes_every_row_in_one_transaction`——250 行批量写入不丢不乱；
  - `test_a_delete_racing_a_scan_from_another_thread_stays_deleted`——用事件栅栏构造真实线程交错。
- 聚焦：`tests/test_download.py`、`test_downloader_lifecycle.py`、`test_round33_idempotency.py`、`test_round12_regressions.py`、`test_round34_contracts.py` = **47 passed / 2 skipped**；`test_library_upgrade.py` 等索引相关 = **69 passed / 1 skipped**。
- 全量：**649 passed / 8 skipped / 0 failed in 283.31 s**（基线 646，新增 3 条）。
- 前端改动做了 `node --check` 语法校验。

## 八、覆盖率缺口

1. 并发缺陷没有压力测试：线程交错靠 `Event` 栅栏模拟，覆盖的是「已识别的两种顺序」，不是所有顺序。
2. T3 域 10 条全部只做了静态判定，没有在真实 Windows 文件占用（杀毒/备份）下复现 `os.replace` 重试。
3. 前端两条修复（T4-1/T4-3）没有自动化测试，只做了语法校验与逐行核对；`disabled` 在真实浏览器里的行为需要手工点。
4. 下载取消路径（T2-3）没有在「子进程卡死 + 用户连点取消」下实测，本轮也没修。

## 九、下轮建议

第 40 轮建议换到**资源释放与生命周期**：谁创建了句柄/线程/定时器/子进程，谁负责关掉；长会话里哪些东西只增不减（事件监听器、缓存条目、临时目录、连接）。本轮的 `_deleted_uris`、`jobActionBusy` 都是「只增不减」的小集合，正好可以作为下一轮的起点样例。
