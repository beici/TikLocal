# 第 25 轮 R2 域：下载队列与下载器

| ID | 级别 | 位置 | 一句话结论 |
|---|---|---|---|
| R2-1 | P1 | `tiklocal/services/download_sources.py:156-166` | 读来源映射遇 WinError 32 或 JSON 损坏时返回空表，下一次 set_many 即用单条记录覆盖整表，全库来源元数据丢失 |
| R2-2 | P2 | `tiklocal/services/downloader.py:433-437` | `_jobs`/`_job_order`/`_cancel_events`/`_progress_persist_at` 只在手动删除时回收，200 上限仅作用于落盘，万级任务常驻数十 MB |
| R2-3 | P2 | `tiklocal/services/downloader.py:1466-1468` | `download_payload` 终态后仍随 job 永久保留，jobs.json 每秒全量重写，2 并发约 2-6 MB/s 且全程持锁 |
| R2-4 | P2 | `tiklocal/web/downloads.py:246-261` | 批量入队对每条 URL 各做一次全量落盘与子进程探测，5000 条时单请求占死一个 waitress 线程数分钟 |
| R2-5 | P2 | `tiklocal/services/downloader.py:1639-1646` | `/api/source` 未命中即整表重写来源映射，随库规模 O(n²) I/O；固定 `.tmp` 无重试会把成功下载改成 failed |
| R2-6 | P2 | `tiklocal/services/tiktok_downloader.py:302-303` | sidecar 响应 `read()` 无大小上限，`source=true` 取百条原始作品约 4 MB JSON 仅为读 id |

P3：3 条（详见对话）

## 已排除

- `_workers`/`_processes` 在 `_run_job` 的 finally 中正确弹出（`tiklocal/services/downloader.py:1089`、`:1097`），`_dispatch_locked` 只数活 worker，并发槽不会泄漏。
- 临时目录不泄漏：每个 job 的 finally 里 rmtree（`tiklocal/services/downloader.py:1161`、`:1207`、`:1268`），崩溃残留由启动时 `_cleanup_stale_temp_dirs`（`tiklocal/services/downloader.py:443-462`）清理。
- `_run_tool` 的 `lines` 队列虽无界，但生产与消费同属一个 job 且消费侧 1 s 轮询，峰值仅几 KB，不构成累积。
- `_terminate_process` 已有 `poll() is None` 守卫（`tiklocal/services/downloader.py:1499`），不会对已回收 pid 跑 taskkill。
- `claim_available_path` 有 1000 次上限（`tiklocal/services/download_tools.py:251`），不会死循环或覆盖同名文件。
- `DownloadHistoryStore.save` 已截断到 200 条（`tiklocal/services/downloader.py:406`），jobs.json 条目数有界，无界的只是单条大小。
