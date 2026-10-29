# 第 33 轮：幂等与重复执行

- 轮次：33
- 日期：2026-10-29
- 起点：`5478fd7`（第 32 轮），tag `audit-round-33-start`
- 本轮视角：**幂等与重复执行**——任何可能被执行两次的操作（重试、双击、重复提交、并发重复请求、崩溃后重跑、扫描重叠）会不会产生重复状态或重复副作用。

## 一、方法与范围

五个域（前四域为只读子代理，第五域由主会话自查）：

| 域 | 范围 |
|----|------|
| N1 | 下载与入队链路（`downloader.py`、`tiktok_downloader.py`、`download_tools.py`、`download_sources.py`、`web/downloads.py`、下载页控制器） |
| N2 | 索引登记、删除与回收站（`library_index.py`、`trash.py`、`database.py` 写入部分、`web/media.py` 删除/恢复/purge） |
| N3 | 前端提交与乐观更新（设置页、合集页、库页、feed、详情页、`ui_feedback.js`、`base.js`） |
| N4 | 持久化与配置写入（收藏、合集、元数据、设置保存、迁移、向量写入、用户/会话写入） |
| N5 | 主会话自查：并发写入与幂等（实例锁、回收站目录、缩略图缓存、任务去重） |

## 二、问题总览

| 域 | P0 | P1 | P2 | P3 |
|----|----|----|----|----|
| N1 下载与入队 | 0 | 5 | 3 | 3 |
| N2 索引/删除/回收站 | 0 | 0 | 4 | 5 |
| N3 前端提交 | 0 | 3 | 8 | 5 |
| N4 持久化与配置 | 0 | 0 | 1 | 6 |
| N5 自查 | 0 | 0 | 3 | 1 |
| 合计 | 0 | 8 | 19 | 20 |

去重后 45 条（N3-3 与 N4-1 是同一缺陷的两种视角，按一条计；N2-4/N5-3 同为回收站排序，按一条计）。本轮修复 **15 条**（P1 8 + P2 7），其余 28 条转 backlog，2 条驳回。

## 三、P1 详述（全部已修）

### N1-1 同一个 URL 重复入队生成两个 job

`downloader.py:541-639` 的 `enqueue` 在锁内无条件 `uuid4` 造新 job，全程不比对已有 job 的 URL；路由层（`web/downloads.py:287-294`）与批量队列（`:246-269`）都是直通。两个标签页各点一次、浏览器重发 POST、或 API 客户端超时重试（请求其实已成功）都会入两次。

引擎相关：yt-dlp 因模板确定性 + `--continue` 只多一条 job 记录（并让 `_record_job_sources_on_success` 用新 job id 覆盖来源映射）；`tiktok-downloader`/direct 引擎会真的产生 `name (1).mp4`。

修复：`enqueue` 在锁内、生成 `job_id` 之前按 `(url.strip(), engine, download_payload.work_id)` 匹配 `queued`/`running` 的既有 job，命中直接返回该 job 并带 `deduplicated: True`。锁内完成「查重 + 造 job」两步，否则两个并发请求会同时查空。

### N1-2 重试不复位原 job，重试按钮可无限点

`downloader.py:759-818` 的非 index 分支只读原 job 然后 `enqueue`，从不改原 job 的状态；`retry_of` 只写不读。原卡片仍是 `failed`，前端继续渲染重试按钮，再点一次就再建一个 job。

修复：锁内先把原 job 写成 `retried_by='pending'` 并持久化（写失败则回滚并返回存储错误），`enqueue` 成功后再改成新 job id；已有 `retried_by` 时直接返回 `download.error.retry_state`。这样「并发两次 retry」也只有一次能穿过 `pending` 这道闸。

### N1-3 批量入队部分失败后重试，已成功的被再入一次

`web/downloads.py:246-269` 逐行 enqueue，部分失败时返回 `queued>0 & failed>0`；前端 `download_page_controller.js:972-983` 只在 `queued && !failed` 时清空列表，用户为重试失败项再点一次就把整张列表（含已成功项）全部重提。

修复：路由把 `job.deduplicated` 的条目计入 `skipped` 并在响应里回传；前端按 `items[].url` 从输入框里只移除已入队（含被跳过）的行，失败的留在原地。

### N1-4 账号/合集批量：N 个 job 共用同一个账号 URL

`tiktok_downloader.py:1086-1111` 一次请求 for 循环建 N 个 job，每个 job 的 `url` 都是原始粘贴链接；重复发同一次请求（网关 502 重试、两个标签页）就再建 N 个，且 pending 集合按 `job["url"]` 解析 aweme_id，账号页 URL 匹配不到 → 作者页「同步最新」会把已入队作品再排一次。

修复：入队时把 aweme_id 写进 `download_payload.work_id`，`job.url` 改成作品直链 `https://www.douyin.com/video/<id>`；`_pending_work_ids` 优先读 `download_payload.work_id`，回退才解析 URL。N1-6/N1-8（pending 快照不可见账号 job、并发双同步）随之一起解决。

### N1-5 gallery-dl 归档已记、产物被删，重试永远取不回

`downloader.py:1263-1277`：`--download-archive` 是全局文件且默认开启，gallery-dl 把成功条目写进归档后若整体退出码非 0，代码提前 return 而不搬移 temp 里的成品，`finally` 又 `rmtree` 掉 temp；重试时归档命中 → 什么都不下 → 报 "did not download any usable files"。已下载成功的文件被永久丢弃。

修复：退出码非 0 时也先把 `gallery_media_outputs` 的成品搬进库再返回失败，失败信息里保留原因。

### N3-1 合集创建的回车路径没有防重

`collections_page_controller.js`：`nameInput` 的 Enter 直接调 `createCollection()`，唯一防重是提交按钮的 `disabled`，键盘路径不经过按钮 → 连按两次 Enter 发两个 POST，`nameInput.value` 要到请求返回后才清空，于是同名重复合集。

修复：加模块级 `_createInFlight` 守卫（进入即置位、`finally` 清除）+ `event.repeat` 检查。

### N3-2 选择器在列表未加载完成时创建并写回空列表

`collection_picker.js` 的 `create()` 用 `state.collections` 拼写回数据；若 `GET /api/collections` 还没返回，`state.collections` 是空数组，创建成功后写回 → 服务端有的合集在 UI 上被清空，再点一次就重复创建。

修复：`create()` 在 `!state.loaded` 时先 `await load()`，用服务端最新列表重建 chips，而不是用本地空数组。

### N3-3 / N4-1 设置页「媒体源保存」的读-改-写竞态

`web/settings.py:168-191` 的 `_apply_change` 在 `_SOURCES_LOCK` 内算 `removed_ids`，但 `add/update/remove` 三个路由在锁外先调 `_media_sources()` 读列表 → 两个标签页同时保存时，后写的一方用旧快照算出的 `removed_ids` 会把对方刚加的目录记成「已删除」，而 `removed_ids` 会跨重启生效（索引按它清理），用户没删过的目录就此消失。

修复：三个路由把「读列表 → 校验 → 保存」整体放进 `_SOURCES_LOCK`（新增 `_apply_change_locked`，DELETE 也进锁），读与写看到同一份快照。

## 四、P2 已修（7 条）

| 编号 | 位置 | 症状 | 修法 |
|------|------|------|------|
| N1-6/N1-8 | `tiktok_downloader.py:933-963`、`:1002-1039` | pending 集合看不到账号 job；两次同步并发各取同一份旧快照 | `work_id` 入 job；`_pending_work_ids` 优先读它；循环内每轮重算 |
| N2-2 | `trash.py:_delete_folder` | 只读/被占用的文件让 `rmtree` 半途抛错，manifest 先被删 → 目录不可见、不可恢复、永不过期 | 先清只读位、逐文件删、manifest 最后删、`rmtree(ignore_errors=True)` |
| N2-3 | `trash.py:purge`、`web/media.py:purge` | 重复 purge 返回 404，UI 报失败并保留行 | 目录已消失视为幂等成功（返回空 uri，跳过派生清理） |
| N3-4 | `download_page_controller.js:972-983` | 部分失败时整表重提 | 只保留失败行（与 N1-3 的服务端 `skipped` 配合） |
| N3-5 | `collections_page_controller.js` | 名字为空时按 Enter 也发请求 | 提交前先校验名字 |
| N3-6 | `collections_page_controller.js` | 重命名输入框 Enter 无 repeat 守卫 | 加 `event.repeat` 检查 |
| N3-7 | `collections_page_controller.js` | 自动封面 Enter 重复触发 | 加 `event.repeat` 检查 |

## 五、未修（转 backlog，28 条摘要）

- **N2-1（最重的一条 P2）** 删除只清索引，不清收藏/合集成员/元数据；`purge` 的 `reused` 分支（同名新文件已存在）永久跳过清理 → 同 URI 的新文件继承旧文件的收藏与归属。修法需要「删除时把派生状态快照进 `entry.json`、恢复时写回」，涉及数据格式，单独立项。
- **N1-7** 取消后重试：已搬进库的半成品被保留在 `output_files_rel`，重试从头再下 → `(1)` 副本。需要让 `retry_job` 复用 `partial` 的产物清单。
- **N1-9** 崩溃窗口：多文件任务搬完第 1 个文件、还没写 `output_files_rel` 时进程被杀 → 孤儿文件 + 重试副本。
- **N1-10** `output_token` 只存不用，API 没有幂等键；N1-11 同一 URL 的两个并发 job 会写同一最终路径与 `.info.json`（入口去重后已大幅降低）。
- **N2-4** `restore` 目标已存在时 409 而非幂等成功（不删任何一边，行为诚实，暂不改）。
- **N2-5~N2-9** 回收站恢复后 mtime 变化、`purge_expired` 与手动 purge 的锁粒度、`register_uris` 与 `replace_snapshot` 重叠、`media_affinity` upsert 竞态、删除与重扫竞态。
- **N3-8~N3-12** 设置页保存按钮、上传 cookie、作者同步的重复提交（有 `disabled` 但无请求代际号）；乐观更新回滚窗口；`fetch` 无重试语义。
- **N4-2~N4-7** 合集创建的服务端同名唯一性策略（本轮只做了前端防重）、元数据 set/delete 的并发窗口、迁移重复执行、向量 upsert 的 UNIQUE 兜底、用户创建重复、`write_json_atomic` 临时名冲突。
- **N5-1~N5-4** 实例锁对 CLI 子命令的覆盖（`dedupe --delete` 可在服务运行时删文件）、缩略图缓存目录用本地时间命名、回收站 `entries()` 按字符串比较 `deleted_at`、后台探测失败的重复重试。

驳回 2 条：`_persist_locked` 的整表重写（写入量小且已有原子替换，改增量写风险大于收益）、`retry_of` 只写不读（本轮已用它做「重试链」的展示，不再算死字段）。

## 六、实现要点

1. **查重与造 job 必须在同一把锁内**：分开做的话两个并发请求都查空，N1-1 的修复就只对串行请求有效。
2. **幂等要靠状态位而不是前端按钮**：N1-2 用 `retried_by='pending'` 占位，比「前端 disabled」可靠——两个标签页、两次网络重发都能挡住；持久化失败要回滚占位，否则卡片永久不可重试。
3. **删除目录的顺序是数据安全的一部分**：manifest 先删会让条目从所有列表里消失，此后既不能恢复也不能过期；必须先删 payload、最后删 manifest，并容忍个别文件被占用。
4. **重复 purge 是重试而不是错误**：客户端拿不到响应时会重发，404 会让 UI 显示失败并保留一行幽灵。
5. **选择器写回要用服务端快照**：本地空数组写回会把别人的数据抹掉；`!loaded` 时先 `await load()`。
6. **`event.repeat` 与在飞标志要同时存在**：按住 Enter 会连发 keydown，`disabled` 拦不住键盘路径。

## 七、验证

- 新增 `tests/test_round33_idempotency.py`：同一 URL 两次入队得到同一个 job、重试第二次返回 400 且原 job 记 `retried_by`。
- `tests/test_trash.py` 的 `test_purge_and_expiry` 契约更新：重复 purge 由 404 改为 200（幂等成功），并断言条目已从 `/api/trash` 消失。
- 相关套件：`test_round33_idempotency.py`、`test_trash.py`、`test_collections.py`、`test_download.py` = **53 passed / 2 skipped**；下载相关八件套（`test_download*.py`、`test_tiktok_downloader.py`、`test_media_sources.py`、`test_run_media_sources.py`）= **151 passed / 2 skipped**。
- 全量：**616 passed / 8 skipped in 217.44s**（第 32 轮基线 614，新增 2 条为本轮用例）。

## 八、覆盖率缺口

- 没有真正的并发集成测试（两个进程/两个线程同时提交同一 URL）；本轮的去重靠锁内查重，正确性由代码结构保证，未被测试覆盖。
- 崩溃窗口（N1-9）无法在测试里可靠复现，只有静态推理。
- 前端回车重复提交只有人工推演，没有浏览器自动化用例。

## 九、下轮建议

视角：**错误恢复与降级**——外部依赖不可用（yt-dlp/ffmpeg 缺失、磁盘满、数据库锁、网络中断）时，哪些路径会留下半状态、哪些提示会误导用户。
