# 第 28 轮审查报告：并发与多进程一致性

- 日期：2026-10-29
- 起点基线：`6efaaba`（第 27 轮收尾），`pytest -q` = 604 passed / 8 skipped / 0 failed
- 视角：**并发与多进程一致性**——waitress 多线程下的进程内共享状态、CLI 与 web 并行、下载 worker 与子进程生命周期、SQLite 事务与原子文件写
- 结论：**P0 0 / P1 0**；P2 19 条（跨域去重后 15 条），本轮修 12 条 P2 + 2 条 P3，转 backlog 17 条

## 一、方法

前几轮按代码层或数据生命周期切域，这轮按**并发主体**切：谁在并发地动同一份状态，就从那条边看过去。

| 域 | 范围 |
|----|------|
| H1 | waitress 多线程下的进程内共享状态：锁粒度、锁序、TOCTOU、懒初始化、计数丢失 |
| H2 | CLI 与 web 同时运行：索引库、缩略图缓存、`download_jobs.json`、收藏/合集/元数据 JSON、回收站 |
| H3 | 下载 worker 线程与子进程生命周期：队列唤醒、cancel 竞态、Popen 回收、僵尸任务 |
| H4 | SQLite 与原子文件写：连接工厂、`BEGIN IMMEDIATE`、迁移并发、`write_json_atomic` 临时名 |
| H5 | 自查：把上面四域的结论在两处最容易出错的路径上复核（实例锁、进程内共享 dict） |

五个域都只读，未运行服务、未改动文件；所有修复在域报告齐了之后统一做，避免边审边改互相干扰。

## 二、发现汇总

| 域 | P0 | P1 | P2 | P3 |
|----|----|----|----|----|
| H1 waitress 共享状态 | 0 | 0 | 6 | 8 |
| H2 CLI 与 web 并行 | 0 | 0 | 4 | 6 |
| H3 下载 worker 与子进程 | 0 | 0 | 4 | 6 |
| H4 SQLite 与原子写 | 0 | 0 | 3 | 7 |
| H5 自查 | 0 | 0 | 2 | 3 |
| **合计** | **0** | **0** | **19** | **30** |

19 条 P2 里有 4 条是跨域同一根因，去重后 15 条：

- `register_uris()` 的 0.5 s 超时被静默吞掉（H1-4 / H3-3）
- 设置页媒体目录「读-改-写」无锁（H1-1 / H4-3）
- CLI 子命令不取实例锁（H2-2 / H4-8 / H5）
- `_persist_locked()` 全量写与进度写盘失败（H3-2 / H2-5 部分重叠）

这轮 0 P1 不是漏看，而是前几轮把「单请求就能触发的错误路径」扫得比较干净了：剩下的问题几乎都需要第二个并发主体（另一个标签页、另一个进程、另一个 worker）才会显形。

## 三、P2 详述与本轮修法

### P2-1、下载完成的登记失败被当成成功（H1-4 / H3-3）

`library_index.py:1028-1037` 的 `register_uris()` 在 `_sync_lock` 0.5 s 内拿不到锁时打一行 warning 并 `return 0`；`downloader.py:1040-1043` 丢掉 `on_outputs()` 的返回值，`:1055` 照样把 job 置 `success`。全量扫描（几分钟）期间完成的下载因此「成功但搜不到」，且没有任何自动补扫入口。

修法：新增 `IndexBusyError`，`register_uris()` 超时时抛出而不是返回 0。下载侧不吞异常——它落在已有的 `failure_stage='index'` 分支里，文件保留、job 标 `partial`、界面给「重新索引」重试。`web/media.py:322`（恢复后补登记）本来就把 `register_uris()` 包在 `try/except Exception` 里，行为不变。

### P2-2、进度写盘失败会掐断在飞下载并删掉已下载字节（H3-2）

`_update_progress()` 在持锁状态下同步调 `_persist_locked()`；`write_json_atomic` 重试 1.7 s 后仍失败就抛 `PermissionError`。异常从 `on_line` 冒出 `_run_tool`，落到 `_execute_download_yt_dlp` 的 `finally: shutil.rmtree(temp_dir)`，整个 job 判 failed——一次杀软/备份占用 `jobs.json` 就毁掉几十分钟的下载。

修法：进度 tick 的 `_persist_locked()` 包 `try/except OSError`，只记日志，下一 tick 再试。进度是簿记，不该有权力杀死传输。

### P2-3、`worker.start()` 抛异常会永久占住一个并发槽（H3-1）

`_dispatch_locked()` 先 `self._workers[job_id] = worker` 再 `worker.start()`，且无 try。唯一 pop 点是 worker 自己的 `finally`，线程从未运行就永不 pop。`max_concurrent=1` 时此后所有任务永远停在 `queued 0%`，只能重启。

修法：`start()` 包 `try/except BaseException`，失败时 pop 掉登记、把 job 置 `failed`（新 i18n 键 `download.error.worker_start`）、持久化、记异常。

### P2-4、设置页媒体目录「读-改-写」无锁（H1-1 / H4-3）

`web/settings.py:168-191` 的 `_apply_change()` 读 `_media_sources()`、校验（网络盘上 `os.scandir` 可达数秒）、`save()` 整表覆盖、`apply_sources()`。两个标签页同时保存时后写者赢，另一个刚加的目录同时从磁盘和运行中的 `LibraryService` 消失，重启也不恢复。

修法：模块级 `_SOURCES_LOCK` 把「读列表→校验→持久化→apply」整段做成临界区。

### P2-5、下载目录被运行中改写，产物落在两个根之间（H3-4）

`web/settings.py:191` 直接 `download_manager.media_root = ...`，worker 侧有 5 处独立读取（临时目录、`-P`、`_to_media_relative`…）。切换发生在下载中途时，临时目录按旧根、输出按新根、相对路径又按当前根算，产物匹配不到任何 source，被判 `no output files`，文件留在旧根下成为孤儿。

修法：保存前若下载根会变且存在 `queued`/`running` 任务，返回 409 + 新 i18n 键 `settings.library.source_download_active`，请用户等任务结束再改。

### P2-6、缩略图 follower 超时后重复抢锁（H1-5）

`thumbnail.py:285-291` 的 follower 在 `event.wait(20)` 超时后不返回占位符，而是继续走到 `generate_thumbnail()`，于是先等 per-URI 锁、再等 180 s 的大图槽位。一个 >48 MP 的 PNG 被两个请求要，就能占住 2 个 waitress worker 达 200 s。

修法：follower 等到超时仍无缓存时直接返回上一张可用缓存或占位符，不再排队；leader 完成后浏览器下次刷新自然拿到新图。

### P2-7、改文件夹后的重扫被静默跳过（H1-3）

`sync()` 用 `acquire(blocking=False)`，拿不到就返回 `{'busy': True}`，而路由对 busy 仍报 success。大库扫描期间再改一次文件夹，新目录在索引里永远是空的。

修法：`LibraryIndexer` 增加 `_rescan_requested` 标志；拿不到锁时置位，扫描结束时在锁内补跑一轮（循环到标志清除）。

### P2-8、cookie 上传固定临时名 + 无锁（H1-6）

`downloader.py:640-646` 的临时名固定为 `<name>.tmp`，两个并发上传同名文件会互相 `open("wb")` 截断，内容交错，两次都报成功，此后所有走该 cookie 的下载失败。

修法：临时名改为 `.<name>.<pid>.<counter>.tmp`（`itertools.count`，与 `write_json_atomic` 同思路），并用新增的 `self._cookie_lock` 覆盖 `exists → 写 → replace`。

### P2-9、`MediaIndexStore.delete()` 两段事务（H4-2）

媒体行在一个事务里删，`forget_uri()` 的派生行（续播、亲和、事件、向量）在另一个事务里删。两段之间锁竞争失败就只删一半，派生状态被同名新文件继承。

修法：把四个派生表的删除并入媒体行所在的同一个事务。

### P2-10、CLI 维护命令与运行中的服务并行（H2-2 / H4-8）

`dedupe --execute` 可在服务运行时删掉正在被访问的文件；`vectorize` 会跑 `database.migrate()`，迁移在活动连接下执行 DDL 是明确的破坏路径。

修法：`dedupe --execute`（非 dry-run）与 `vectorize` 的迁移段都先取实例锁。`vectorize` 只锁迁移——长时间的向量化写入都是短事务，SQLite 自己能串行化，锁住整段会禁止「服务开着时准备向量」这个正常用法。

## 四、顺带修的两条 P3

- **队头阻塞（H3-5）**：`_dispatch_locked()` 遇到「上一次执行还没退出的重试 job」时 `break`，把后面所有可派发任务一起挡住（旧 worker 可能还在 `on_outputs()` 或 15 s 的 taskkill 里）。改为把该 id 挪到 `deferred` 队列、继续看后面的 job，循环结束后按原顺序放回队头。
- **`_terminate_process()` 末尾 `process.wait()` 无超时（H3-7）**：前面 terminate/kill 都有 timeout，最后一句没有，且只捕 `ProcessLookupError`。`TerminateProcess` 被拒时异常冒出 worker 的 finally，槽位永久占用。改为 `wait(timeout=5)` + 捕 `OSError`/`TimeoutExpired` 记日志。

## 五、实现要点

- **锁序**：`_SOURCES_LOCK`（settings）→ `media_source_store`/`library_service`/`download_manager` 属性赋值；不嵌套 `DownloadManager._lock`。`_cookie_lock` 只覆盖单文件读写，与 job 锁无关。
- **`IndexBusyError` 的边界**：只有 `register_uris()` 抛，`_register_uris_locked()` 仍是返回计数；调用点两处（downloader 的 `on_outputs`、`web/media.py` 的恢复补登记），后者已有兜底。
- **重扫标志的读写**：`_rescan_requested` 在持锁的 `sync()` 内读写，写侧只在 `acquire` 失败时置位；`while` 循环保证一轮扫描结束时补跑，不会无限循环（每轮开头清标志）。
- **任务裁剪**：`enqueue` 里按 `DOWNLOAD_HISTORY_LIMIT` 从 `_job_order` 尾部裁剪**终态**任务（遇到非终态即停），同步清 `_jobs`/`_progress_persist_at`/`_cancel_events`，让内存与 `jobs.json` 的上限一致。
- **新增 i18n 键 2 个**：`download.error.worker_start`、`settings.library.source_download_active`（中英各一条）。

## 六、验证

- 全量：`.\.venv\Scripts\python.exe -m pytest -q` → **604 passed / 8 skipped / 0 failed in 226.90s**（与第 27 轮基线 604P/8S 一致）。
- 专项：`tests/test_download.py tests/test_downloader_lifecycle.py tests/test_download_tools.py tests/test_thumbnails.py tests/test_library_upgrade.py` → 72 passed / 3 skipped。
- 手工探针（一次性脚本，未入库）：
  - **并发 cookie 上传**：两线程各写 200 KB 不同内容到同名文件 → 最终文件恰好是其中一方完整内容（无交错），无 `.tmp` 残留，两次都返回成功。
  - **worker 启动失败**：`patch(threading.Thread.start, side_effect=RuntimeError)` → job 变 `failed`、`_workers` 为空、`_pending` 清空（旧实现会留下永久占坑）。
  - **派发旋转**：`max_concurrent=2`、一个槽位被「上一次执行未退出的重试」占住时入队新任务 → 新任务立即启动，重试 id 按原顺序保留在队头（旧实现会让新任务一直排队）。

## 七、转 backlog（17 条）

并发类问题多数需要「第二个主体」才能触发，本轮按「能稳定复现 + 修复面小」优先，其余登记待后续：

| # | 条目 | 位置 | 级别 |
|---|------|------|------|
| 1 | `/api/activity` 在长写事务期间阻塞约 15 s 后 500，播放/跳过信号整批丢失 | `web/media.py` + `database.py` 写路径 | P2 |
| 2 | 读路径多语句不在同一快照，`total`/`has_more` 与分页可能撕裂 | `web/library.py`、`library_index.py` 读方法 | P2 |
| 3 | `write_json_atomic` 没有 `fsync`，断电可能留下 0 字节目标文件 | `services/json_storage.py:35-77` | P2 |
| 4 | `thumbs.py` 绕过 `write_json_atomic` 且把损坏当空 | `services/thumbs.py` | P2 |
| 5 | 回收站 manifest 用非原子 `write_text`，崩溃后条目永久不可见且不过期 | `services/trash.py` | P2 |
| 6 | `PRAGMA journal_mode = WAL` 的返回值被忽略，网络盘上静默退化为 delete 模式 | `services/database.py:425` | P2 |
| 7 | `--dev` reloader 父进程仍在写库，子进程不取实例锁 | `run.py` | P2 |
| 8 | `_PROBE_CACHE` 无锁，且失败也按「可用」缓存 60 s | `services/download_tools.py:204-243` | P2 |
| 9 | 启动清理按目录全删，不区分实例（两个实例共用媒体根时互删在飞临时目录） | `downloader.py:421-440` | P3 |
| 10 | worker 收尾 `stdout.close()` 与 pump 线程竞争，pump 从不 join | `downloader.py:1091-1095` | P3 |
| 11 | `_BIG_IMAGE_SLOT.acquire` 在 per-URI 锁内，leader 会长时间持锁 | `services/thumbnail.py:641` | P3 |
| 12 | `clear_cache` 不与生成中的缩略图协调，清理后仍有缩略图存活 | `services/thumbnail.py:376-401` | P3 |
| 13 | `_claim_legacy_adoption` 无锁双写 | `services/thumbnail.py:227-247` | P3 |
| 14 | `RecommendService._candidate_cache` 无锁且删除后 TTL 内仍推荐已删 URI | `services/recommendation.py:62,79-87` | P3 |
| 15 | `radio.metadata_for_many` 每请求起 8 线程跑 ffprobe，8 并发最多 64 个进程 | `services/radio.py:250-259` | P3 |
| 16 | `trash.restore` 的 `exists→move` TOCTOU，输者抛 `FileNotFoundError` 变 500 | `services/trash.py:81-84` | P3 |
| 17 | `apply_sources()` 就地多步改写共享 `LibraryService`，读侧无快照 | `services/media_sources_store.py:297-310` | P3 |

## 八、覆盖率缺口

- **没有真正的多进程测试**：两个进程 + 同一实例目录/同一媒体根的场景全靠代码推理，实例锁与 `jobs.json` 全量覆盖的互斥没有自动化用例。
- **没有并发用例**：并发上传、并发保存媒体目录、并发恢复同一回收站条目都只用手工探针验证过一次。
- **Windows 专属路径无法在 CI 复现**：`WinError 32`（文件被占用）是进度写盘、缩略图替换、日志轮转的共同触发条件。
- **时序类缺陷难以断言**：`_rescan_requested` 的补扫、派发旋转都靠探针脚本观察，没有稳定回归测试。

## 九、下轮建议

视角：**可观测性与故障诊断**——日志是否足以定位问题、错误信息是否可操作、启动自检能否提前发现配置错误、诊断端点能否回答「为什么这个文件不见了」。

前 28 轮把「错」修得比较密了，但用户遇到问题时的第一手信息仍然只有一行 toast 和一份滚动日志；下一轮就查这条链。
