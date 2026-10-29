# 第 40 轮审查报告：资源释放与生命周期

- **日期**：2026-10-29
- **起点**：tag `audit-round-40-start`（第 39 轮收尾提交 `ad66c98`）
- **基线**：全量 `pytest -q` = **649 passed / 8 skipped / 0 failed**
- **视角**：谁创建谁关闭 —— 句柄、线程、子进程、连接、解码器、定时器；长会话里只增不减的容器；异常路径上被跳过的清理。

## 一、方法与范围

四个只读子代理 + 我自己的一轮横向核对（U5），每个域都给 `file:line`、代码片段、触发路径、影响量化、最小修复与置信度，并附「已排除的高风险点」。

| 域 | 覆盖 | 结论（P0/P1/P2/P3） |
| --- | --- | --- |
| U1 索引与数据库 | `library_index.py`、`database.py`、`media_info.py` 的连接/线程池/探测器 | 0 / 2 / 5 / 3 |
| U2 下载与子进程 | `downloader.py`、`download_tools.py`、`url_list.py`、`web/downloads.py` | 0 / 0 / 6 / 2 |
| U3 缩略图/电台/推荐 | `thumbnail.py`、`radio.py`、`radio_energy.py`、`recommendation.py`、`metadata.py`、`author_profiles.py` | 0 / 0 / 4 / 6 |
| U4 前端 | `flow_page_controller.js`、`radio_controller.js`、`radio_scene.js`、`library_page_controller.js` | 0 / 1 / 1 / 3 |

合计 **P0 0 / P1 3 / P2 16 / P3 14**（33 条）。修复 16 条（P1 2 + P2 12 + P3 2），转 backlog 17 条。

## 二、P1 三条

### U1-1 探测器背压表的 400 条硬截断让推进永久停在原地

`library_index.py` 的 `run_once()` 把「还在退避」的 uri 塞进 `NOT IN` 排除表，却按插入顺序取前 400 条。没有 ffmpeg/ffprobe 的安装（这是该段代码注释里明写的常态）时，每个视频都以 `base_delay=60s` 进退避表，8 轮就越过 400：第 401 个之后的 uri 不再被排除，`unprobed(batch=50)` 每次返回同一批 50 行、全部失败、`continue` 没有等待 —— 扫描推进停在原地，注释承诺的 "start deeper" 失效。

修复：排除表按**到期时间**排序取前 400（最早到期的优先），并给 `if not results` 加连续三次即退出本轮的限制（把「空转」变成「让 `_loop` 去睡」）。

### U1-3 `records()` 无 LIMIT，flow 每请求物化整库

`MediaIndexStore.records()` 是 `SELECT * ... ORDER BY mtime DESC, uri`，没有 LIMIT；`web/flow.py` 的两处调用（`source_id=...` 与全库）因此每翻一页就物化 19k 行。19k 行 × 23 列 ≈ 15 MB 的 Row 列表 + 19k 个 dict ≈ 13 MB，单请求峰值 35-40 MB、约 1 s。搜索页每个作品链接都带 `start=`，点一次就是一次全量物化。

本轮未修（要动 flow 的选片与折叠语义），已转 backlog 并标注为首要项。

### U4-1 flow 无限流从不释放 `<video>` 的 src

`ensureVideoSrc()` 给元素永久写入 `src`；`clearPreviousState()` 只 `pause()` + `muted = true`，全文件 2425 行里 **0 处** `removeAttribute('src')`/`load()` 释放路径。唯一的回收口是 `feedItems.length > 100 → dropHead(30)`，而 `dropHead` 也只 `el.remove()`。稳态下 70-100 个元素各自持有缓冲与一个解码器槽，滑动 60-100 条后 `play()` 开始失败，正好触发既有的 `maxQuietSkips = 3` 与整屏 `media_error` —— 那段补丁其实一直是这个泄漏的症状。

修复：新增 `releaseVideoElement(el)`（`pause()` → `removeAttribute('src')` → `load()` → 清 `_startReady`/`_startPromise`，保留 `dataset.src` 供 `ensureVideoSrc()` 重新挂载），在 `clearPreviousState()` 里对**距离当前 > 3** 的条目调用，并在 `dropHead` 移除元素前先释放。

## 三、P2 修复详述

| 编号 | 位置 | 问题 | 修复 |
| --- | --- | --- | --- |
| U2-6 | `downloader.py:1645` | `except subprocess.TimeoutExpired`，而文件里只有 `import subprocess as sp` —— 放弃进程的分支必抛 `NameError`，warning 永不打印，job 的错误信息变成 `name 'subprocess' is not defined` | 改 `sp.TimeoutExpired` |
| U2-1 | `downloader.py:1322-1334` | `_private_cookie_copy()` 在 `try` 之外，复制失败（杀软占用/磁盘满/只读）时 `finally` 的 `rmtree` 还没生效，每次重试泄漏一个 `.tiklocal-download-tmp/<job_id>`（扫描器跳过该目录，用户看不见） | 把复制挪进 `try` |
| U2-2 | `downloader.py:1606,1265` | pump 线程的 `Thread` 没保存，取消路径上主线程先 `close()` 管道，pump 仍阻塞在 `for raw_line in process.stdout` → 每次取消一条 `ValueError` traceback，最坏在 Windows 的 `CloseHandle` 里停住、worker 槽位永久占用 | 保存 `pump_thread`，`_run_tool` 返回前 `join(timeout=5)` |
| U2-5 | `downloader.py:1846` | 媒体根里的 0 字节占位名 + 唯一没被 `try` 包住的 `unlink`：`PermissionError` 冲出 `_move_file_to_media_root` 后，调用方 `finally` 的 `rmtree` 会把已下载完成、尚未搬走的文件一起删掉（gallery 侧 archive 已记账，重试也下不到） | `unlink` 包 `try/except OSError` 并记 warning；宁可留一个空占位也不丢文件 |
| U2-4 | `download_tools.py:220-250` | `probe_binary()` 只在成功后写缓存、无 single-flight：8 个请求线程同时 miss 就是 24 个 `--version` 子进程，工具卡住时 8 个 waitress 线程全被占满最长 105 s | 按 `(command, prefix)` 加锁，锁内二次查缓存 |
| U2-3 | `downloader.py:1700-1716` | 每个进度 tick 全量重写 `jobs.json`，而完成的图集任务仍带着几十条 CDN URL（0.15-1.3 MB/次，默认 2 并发 = 0.3-2.6 MB/s 持续写盘） | 新增 `_history_copy()`：`status == 'success'` 的 job 落盘时丢掉 `download_payload`（只有失败任务重试时才读它） |
| U1-2 | `library_index.py:1493` | `2 ** (attempts - 1)` 无上限，重探每轮 +1，约 2-3 分钟到 1025 就 `OverflowError: int too large to convert to float`，冲出 `run_once` 让整趟 pass 中断 | `attempts = min(attempts, 12)` |
| U1-4 | `library_index.py:1487` | `MediaProbeWorker.start()` 无线程引用、无幂等、退出时线程被就地杀掉；若正卡在 `ffprobe` 子进程里，子进程不回收并继续持媒体句柄 | 保存 `_thread`、`start()` 幂等、新增 `stop()`，`app.py` 用 `atexit` 注册 |
| U3-F2 | `radio.py:242-259` | 8 个 `radio-probe` 线程无锁并发改 `OrderedDict`，缓存满 2000 后 `move_to_end()` 与另一个线程的 `popitem()` 相撞 → `KeyError` 经 `pool.map` 冒泡，整页 `/api/radio/items` 500 | 新增 `_METADATA_CACHE_LOCK`，只包住四步字典操作，探测留在锁外 |
| U3-F3 | `radio_energy.py:190-198` | 解码超时路径只 `_kill_process()` 就返回，`process.stdout` 从不关闭；Windows 的 `TerminateProcess` 中断不了内核态读（断开的 SMB），每次超时永久遗留一个线程 + 两个管道句柄 | 超时分支 `kill` 后 `stdout.close()` 再 `join(5.0)` |
| U3-F4 | `recommendation.py:79-87` | `_candidate_cache` 无淘汰、无失效钩子：删源或「删了再加同一目录」都会留下整份候选列表（19k 文件 ≈ 5 MB/条）到进程退出 | 新增 `MAX_CANDIDATE_CACHE = 64`，超限时先按 `library.sources` 清掉死键，再按插入序淘汰 |
| U3-F1 | `thumbnail.py:78` | `_BIG_IMAGE_SLOT` 的 180 s 等待发生在**持有每输出锁**期间，其余请求堵在同一把锁上，8 个 waitress 线程可被一个大图钉死 | 等待上限降到 20 s（并在注释里写明这段等待发生在锁内） |
| U3-F8 | `thumbnail.py:723` | 无内嵌封面的曲目每次都重画 512² PNG（10-20 ms CPU + 1.5 MB 瞬时缓冲），一屏 200 首 ≈ 2-4 s | `functools.lru_cache(maxsize=32)` |
| U4-2 | `radio_controller.js:638` | 切到 shader 房间后氛围视频只 `pause()`，src 一直挂着，整个会话占一个解码器（被 canvas 遮住，用户无感知） | shader 房间分支补 `removeAttribute('src')` + `load()`，回到视频房间时 `applyAtmosphereSource()` 会重挂 |

## 四、未修条目（转 backlog，17 条）

| 编号 | 位置 | 为什么暂不修 |
| --- | --- | --- |
| U1-3 | `library_index.py:410-423` | 要动 flow 的选片与折叠语义，需单独一轮 |
| U1-5 | `library_index.py:1411-1417` | 每源新建线程池 + `shutdown(wait=True)` 会在异常时白跑 19k 任务；改成有界提交要重排扫描结构 |
| U1-6 | `library_index.py:862-872` | `time_states()` 全表 dict（19k 行 ≈ 10 MB）每次扫描与每次下载都建；要改成按需查询 |
| U1-7 | `library_index.py:664-685` | `image_random` 每页全量排序 + shuffle；需加确定性随机序列列 |
| U1-8 | `database.py:517-535` | 无 `wal_checkpoint`/`journal_size_limit`；改动涉及每次连接的开销权衡 |
| U1-9 | `library_index.py:1230-1237` | `_rescan_requested` 循环无次数上限，连点设置页会跑三趟全量扫描 |
| U1-10 | `database.py:507-515` | `rollback()` 自身失败会覆盖原始异常文本（损坏库判定依赖它） |
| U2-7 | `url_list.py:283-291` | `push()` 固定 `.tmp` 名且无 `finally` unlink，失败会把临时文件留在用户目录 |
| U2-8 | `downloader.py:456-468` | 启动清理不校验归属，同进程第二个 manager 会删掉第一个在跑的目录 |
| U3-F5 | `radio_energy.py:75-108` | 波形磁盘缓存无上限、无孤儿清理（19k 首 ≈ 23 MB） |
| U3-F6 | `thumbnail.py:103-109` | 全局锁包住 `Image.open`（文件系统 I/O）；需改成「先无锁打开，撞上限再重开」 |
| U3-F7 | `metadata.py:29-36`、`radio.py:92-108`、`author_profiles.py:87-95` | 三处锁内做整份读写，共享冲突时把 P99 从 3 ms 拉到 1.4 s |
| U3-F9 | `radio.py:209-234` | `collect_candidates()` 每页重建全量候选（19k 首 ≈ 8-10 MB/页） |
| U3-F10 | `author_profiles.py:51-62` | `authors_dir()` 每次调用 `mkdir`，5000 作者页面 2-3 万次系统调用 |
| U4-3 | `flow_page_controller.js:1428-1439` | 相册子视频三条移除路径只 `remove()`；与 U4-1 同源，下轮一起改 |
| U4-4 | `radio_scene.js:422-429` | `destroy()` 不 `deleteProgram`/`deleteTexture`，不丢上下文（每次文档销毁泄漏 10-40 MB GPU） |
| U5-1 | `web/downloads.py` 的 `list_jobs` | 仍把 `download_payload` 发给浏览器（本轮只改了落盘侧） |

## 五、实现要点与教训

1. **释放路径要和获取路径成对出现**。U2-1/U2-2/U3-F3 都是「拿到资源之后、`try` 之前」或「超时提前 return」漏了配对；把资源获取移进 `try`、把关闭放进 `finally` 就够。
2. **锁内不做 I/O，尤其不做带重试的 I/O**。U2-3/U2-4/U3-F1/U3-F7 是同一类：`write_json_atomic` 在 Windows 上最多重试 8 次（≈1.4 s），锁内做这件事等于把锁变成请求超时器。
3. **「只增不减」的容器一定要有主人**。`_candidate_cache`（U3-F4）、`_backoff`（U1-1/U1-2）都没有淘汰策略，前者按源、后者按文件增长，最终都表现为「跑了几天就慢」。
4. **前端 `<video>` 的 `pause()` 不等于释放**。解码器与缓冲跟着 `src` 走，`removeAttribute('src') + load()` 才是释放（U4-1/U4-2）；`dataset.src` 保留即可零成本重挂。
5. **测试要盯「多久之后」，不只是「对不对」**。U1-1/U1-2 这类缺陷单次调用完全正确，只有连跑几千次才暴露；新增用例用 1500 次 `_defer_probe` 与「整批全失败」的假 store 把这两个场景钉死。

## 六、验证

- 新增 `tests/test_round40_lifecycle.py`（4 条，全绿）：
  - `test_probe_backoff_attempts_stay_bounded`（1500 次退避后 `attempts == 12`，不再溢出）
  - `test_a_fully_deferred_batch_stops_instead_of_spinning`（整批退避时 `run_once` 在 5 s 内返回 0）
  - `test_radio_metadata_cache_survives_concurrent_probes`（8 线程 × 40 路径，缓存上限 8，无异常）
  - `test_finished_jobs_do_not_persist_their_download_payload`（落盘的历史里没有 `download_payload`）
- 聚焦回归：`tests/test_round13_regressions.py`（探测推进）、`tests/test_download.py`、`tests/test_download_tools.py`、`tests/test_downloader_lifecycle.py`、`tests/test_round39_locking.py`、`tests/test_thumbnails.py`、`tests/test_radio.py` → 67 passed / 2 skipped。
- 全量 `pytest -q`：**653 passed / 8 skipped / 0 failed in 297.25s**（基线 649，新增 4 条）。

## 七、覆盖率缺口

1. 没有「跑一整天」的长时回归：本轮所有「只增不减」结论都来自静态推演 + 小规模循环，没有 24 h soak。
2. 前端两条释放修复（U4-1/U4-2）没有 pytest 覆盖，靠 `defaultPrevented` 之外的行为无法断言，需要浏览器里数 `video[src]` 元素。
3. 线程/句柄计数没有自动化断言（Windows 上可用 `Get-Process -Id | Select HandleCount`，但测试里不稳定）。
4. 网络盘/杀软占用这类触发条件无法在 CI 复现，相关修复只有代码层保证。

## 八、下轮建议

**契约与幂等之外的第三个高价值角度：长时运行的数值稳定性** —— 计数器/时间戳/浮点在长时间运行后的漂移（`monotonic` 与 `time.time` 混用、进度百分比累积误差、ETA 计算、`_backoff` 的时钟基准、SQLite 自增与 `ROWID` 复用）。本轮已看到「重试次数无限增长」这一类，下一轮可以专门扫「会随时间变大的数字」。
