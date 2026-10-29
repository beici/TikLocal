# 第 35 轮审查报告：错误恢复与部分失败

日期：2026-10-29
起点：`5e63190`（第 34 轮收尾，`audit-round-34-end`）
视角：一个源/一个文件/一个子步骤失败时，整体是回滚、跳过还是降级？会不会留下半成品、
脏索引，或者把失败播报成成功。

## 一、方法与范围

五个域并行只读审查（四个子代理 + 自查），每域先读代码再跑探针，结论必须落到
`文件:行` 与「谁会踩到」。四份域报告都在同一批代码上独立取证，重叠条目按「同一
后果链」合并。

| 域 | 范围 | P0 | P1 | P2 | P3 |
| --- | --- | --- | --- | --- | --- |
| P1 | 索引与扫描的部分失败（scan_source / replace_snapshot / 登记） | 0 | 1 | 1 | 3 |
| P2 | 下载链路的部分失败（yt-dlp 输出、取消、重试、批量子任务） | 0 | 0 | 4 | 3 |
| P3 | 存储写入的部分失败（设置保存、索引启动、purge、回收站） | 0 | 0 | 3 | 2 |
| P4 | 前端批量的部分失败（超时、逐条入队、整批删除） | 0 | 0 | 5 | 1 |
| P5 | 自查（启动日志、sync 载荷、错误信封） | 0 | 0 | 0 | 0 |

去重后 **22 条**（P4 的「api() 无超时」与 P2 的超时条目同源）：P0 0 / P1 1 / P2 13 / P3 8。
本轮修 **15 条**（P1 1、P2 11、P3 3），转 backlog **7 条**。

## 二、P1

### P1-1 扫描中途拔盘，整源的行与历史被当成「已删除」清掉

`tiklocal/services/library_index.py:1121-1156`（`_sync_locked`）。

`scan_source()` 走完整棵树返回 N 个路径，随后 `_records_for_paths()` 在 8 线程池里逐个
`stat()`。源盘此刻不可达（USB 拔出、SMB 断开、笔记本睡眠后网盘未重连）→ 每个 `stat()`
抛 `FileNotFoundError` → `_record_for_path()` 按「单个文件消失」返回 `None` →
`records == []`，而 `completed_sources.add(source.id)` 照旧执行。`replace_snapshot()`
于是认为「这个源扫过了，看到的集合是空」：删掉该源全部 `media_items`，`deleted > 0`
又触发 `_sweep_orphan_state`，把续播点、观看历史、亲和权重、向量**永久删除**——正好
违反 `library_index.py:171-173` 自己写的「拔掉的源保留历史，回来时还在」。`source_errors`
为空，前端只显示「新增 0 项」。

修法：`if paths and not records: raise OSError(errno.ENOENT, 'source vanished during scan', ...)`
落进既有的 `failures` 分支，保留行并把源报进 `source_errors`。

## 三、P2（本轮修 11 条）

### P2-1 下载历史记录了从未存在的文件

`tiklocal/services/downloader.py:1305-1313`。yt-dlp 会为分离的视频/音频流各打一行
`Destination:`，合并后把中间文件删掉；原实现把所有 `Destination:` 都记进
`output_files_rel`。结果：历史卡片多一个文件、「删除文件」每次都报缺失、任务声称的
`file_count` 大于磁盘上的文件数。修法：转换前先按 `Path(path).is_file()` 过滤。

### P2-2 两条子任务之间取消，已经落盘的文件被丢掉

`tiklocal/services/downloader.py:1387-1396`（`_execute_download_gallery_dl`）。逐条下载
循环里，取消分支直接 `return 1, canceled, []`，而同一函数的「下载中途取消」分支一直
保留已完成部分。修法：取消时把 `downloaded` 逐个 `_move_file_to_media_root()` 后返回。

### P2-3 `process.wait()` 无上限，卡死的工具会永久占住队列槽

`tiklocal/services/downloader.py:1602-1612`。`_terminate_process()` 只等几秒就放弃，随后
无条件 `process.wait()`；一个忽略终止信号的子进程会让该 worker 线程（和队列里的下一个
任务）永远停住。修法：`process.wait(timeout=10)` + `TimeoutExpired` 记 warning 并放弃。

### P2-4 索引写不进去时，应用拒绝启动

`tiklocal/app.py:466-480`。启动期 `library_indexer.sync()` 的 `sqlite3.Error`（磁盘满、
索引损坏）与 `OSError` 直接冒泡 → `create_app()` 失败，整个应用起不来，而索引每次写入
都是单事务、旧快照本身仍然一致。修法：捕获后记 error、用空结果继续启动，页面读旧索引。

### P2-5 purge 的清理步骤一步失败，后面全部跳过

`tiklocal/web/media.py:298-325`。收藏、合集、字幕三个清理步骤原来包在**同一个**
`try` 里：收藏文件被占用就跳过合集与字幕清理，而 purge 没有第二次机会（回收站条目
已删）。修法：每步各自 `try/except` 并记 warning。

### P2-6 设置保存「先写盘、后解析」，解析失败留下半应用状态

`tiklocal/services/media_sources_store.py:320-333` + `tiklocal/web/settings.py:179-235`。
`apply_sources()` 先把新源赋给 `library_service` 再 `resolve()` 各根目录；`resolve()`
失败（离线共享、权限撤销）时源已换、根还是旧的，而磁盘上的列表已经写好了。修法：
先解析再赋值；`settings.py` 捕获 `OSError` 后把旧列表写回磁盘并重新 `apply_sources`，
返回 500 而不是把不一致留给下一次请求。

### P2-7 扫描失败仍弹「已添加文件夹」成功提示

`tiklocal/static/settings_page_controller.js:637-660`。后端把非 busy 的 `sqlite3.Error`
吞成 `result={'error':'failed'}` 但仍返回 `success: True`，前端只看 `scan.busy`，于是
在索引一条都没写进去时播报成功。修法：加 `else if (scan.error)` 分支 + 新键
`settings.library.source_scan_failed`（中英）。

### P2-8 非 JSON 响应被当成 JSON 解析，把 `Unexpected token '<'` 显示给用户

`tiklocal/static/settings_page_controller.js:107-118`。反代 502/504 返回 HTML 时
`response.json()` 抛 `SyntaxError`，toast 原样显示。修法：`readJson()` 把 `SyntaxError`
换成 `settings.request_failed`。

### P2-9 作者资料同步没有超时，按钮永久禁用

`tiklocal/static/settings_page_controller.js:970-990`。`postJSON` 无 `AbortSignal`，
后端抓取链卡住时 `finally` 永不执行 → 输入框与三个按钮一直 `disabled`。修法：
`signal: AbortSignal.timeout(120000)`。

### P2-10 下载页 `api()` 没有超时，轮询链会静默冻结

`tiklocal/static/download_page_controller.js:24-30`。`refreshJobs → renderJobs →
schedulePolling` 是自链，`fetch` 永不 settle 时链断，进度条冻结且零提示。修法：默认
`AbortSignal.timeout(20000)`，并对两个慢端点显式放宽——整批删除 `120000`（服务端先把
文件移进回收站，中止会「报失败但文件已经没了」）、批量入队 `180000`（逐条落盘）。

### P2-11 批量操作的部分失败被播报成成功

`tiklocal/static/download_page_controller.js:600-660`。整批链接都已入队时提示「列表为空」；
TikTok 作品全部被拒时仍弹绿色「已入队 0」；`truncated` 只在响应里。修法：三个分支分别
提示 `download.urls.already_queued` / `download.urls.queued_partial` /
`download.tiktok_truncated`（新增中英键），并清空已入队的链接列表。

## 四、P3（本轮修 3 条）

- **P1-3** `tiklocal/services/library_index.py:1180-1190`：`_register_uris_locked` 只隔离
  「文件已消失」，`stat()` 的非 ENOENT `OSError`（共享冲突、杀软、ACL）让整批 `records`
  丢弃。修法：单条包 `except OSError` + `continue`，与相邻分支同形。
- **P1-4** `tiklocal/services/library_index.py:1436-1446`：probe worker 的
  `else: self._wake.wait()` 无超时，没装 ffprobe 时装上后永不重试。修法：`wait(30.0)`。
- **P1-5** `tiklocal/services/library.py:179-190`：`resolve()` 在刚被拒绝列目录的路径上
  可能同样抛错 → `relative=None` → `skipped` 前缀不记录 → 该子目录行被当删除。修法：
  词法兜底 `os.path.relpath()`。

## 五、未修（转 backlog 7 条）

| 条目 | 位置 | 为什么暂不修 |
| --- | --- | --- |
| P2 域：重试合并 `output_files_rel` 会去重 | `downloader.py:1263-1277` | 需要重做 job 的「输出集合」语义，改动面大 |
| P2 域：`delete_files` 部分失败只报总数 | `web/downloads.py:520-560` | 要新增逐文件结果信封，前端同步改 |
| P2 域：批量入队逐条落盘 | `downloader.py:619-639` | 与第 30 轮「批量入队一次落盘」是同一处，需先定 API 语义 |
| P3 域：`_persist_sources` 失败后 `removed_ids` 仍生效 | `web/settings.py:158-176` | 需要「先落盘再清理」的两阶段，风险高于收益 |
| P3 域：回收站清空部分失败只报计数 | `web/media.py:328-340` | 同 `delete_files`，等统一信封 |
| P4 域：`AbortSignal` 在旧 Safari 缺失 | `download_page_controller.js:24` | 项目已要求现代浏览器，仅记录 |
| P1 域：`_defer_probe` 无上限退避 | `library_index.py:1390-1400` | 30s 轮询已缓解，暂不改退避策略 |

## 六、实现要点

1. **部分失败必须有名字**：`source_errors`、`scan.error`、`skipped`、`truncated` 这些
   字段原来只在后端存在，前端一律不看——本轮把「已经写在响应里但没人读」的字段逐个
   接上 UI。
2. **一个失败不能拖垮一批**：`_register_uris_locked`、`purge` 的清理、批量入队都改成
   逐项隔离；共同点是「整体重试成本高于逐项跳过」。
3. **回滚要连磁盘一起回滚**：`settings.py` 的恢复分支既写回 `media_sources.json`，也
   重新 `apply_sources`，避免「内存旧、磁盘新」。
4. **超时要按端点定**：默认 20s 是给普通读接口的；批量写接口按最坏耗时放宽，而不是
   让默认值决定「前端以为失败、后端已经成功」。

## 七、验证

- 新增 `tests/test_round35_regressions.py`：源在扫描中消失时保留行并报 `source_errors`；
  设置保存时 `apply_sources` 失败会恢复旧列表。
- 相关用例：`tests/test_download_tools.py`、`tests/test_media_sources.py`、
  `tests/test_i18n.py`、`tests/test_round35_regressions.py` 全绿。
- 全量 `pytest -q`：**623 passed / 8 skipped**（第 34 轮基线 621，新增 2 条）。

## 八、覆盖率缺口

- **没有真的拔盘**：P1-1 用「`_records_for_paths` 返回空」模拟，真实 USB/SMB 断开时
  `scan_source()` 自身就可能抛错，走的是另一条分支。
- **没有并发取消的端到端**：P2-2/P2-3 只覆盖到函数级，未验证「取消 + 合并 + 落盘」同时发生。
- **前端的部分失败只测了文案**：toast 分支没有断言，靠人工读页面。

## 九、下轮建议

第 36 轮视角建议 **首次运行与空状态**：全新安装、空媒体库、只有一种媒体、配置缺失、
权限被拒时，首页/flow/library/radio/download 各自是什么样；这类路径在已有数据的开发机
上几乎不会被走到，却是新用户看到的第一屏。
