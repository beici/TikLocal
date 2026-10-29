# 第 24 轮审查报告（路由错误路径 / 服务层输入与外部进程 / 文件系统与路径 / 索引与数据库边界 / 线程与进程生命周期 / 日志与可观测性 / 配置与打包）

- **轮次**：24
- **日期**：2026-10-29
- **起点基线**：HEAD `908171d`（tag `audit-round-24-start`），`pytest -q` = **602 passed / 8 skipped / 0 failed**（213.71 s）
- **审查域**：① 路由层错误路径与状态码 ② 服务层输入校验与外部进程 ③ 文件系统与路径安全 ④ 索引与数据库边界 ⑤ 线程与进程生命周期 ⑥ 日志与可观测性 ⑦ 配置、启动与打包

> 说明：本轮把视角从第 23 轮的「前端状态机 + 可访问性」轮换成「**后端错误路径与边界**」——异常是否被吞、状态码是否说谎、失败后能否恢复、外部进程/线程/文件句柄是否收得住。七域共报 **49 条**（P0×0 / P1×4 / P2×26 / P3×19）。
>
> 其中 1 条是域内重复上报（`author_profiles.py` 的固定 `.tmp` 名同时被 E3 与 E4 报为 P3，按 #21 计），另有 2 条与既有 backlog 条目同源（回收站孤儿条目、`/api/media/info` 回绝对路径）。去重后 **46 条**。
>
> 处置：**修 28 条**（4 条 P1 全部修完 + 19 条 P2 + 5 条 P3），**转 backlog 18 条**，**并入既有 backlog 条目 3 条**，驳回 0 条。共 **21 文件 +530/−113**（含本报告与 `backlog.md`、`README.md` 的更新）。
>
> 本轮有两条改动**做完了又回退**，理由写在 §八：它们各自撞上一条既有测试锁定的契约（CLI 与 web 复用同一份缩略图缓存；作者搜索未配置时返 503 而非 400），回退比改契约更小。

## 一、路由层错误路径与状态码（域 E1）

| # | 级别 | 问题 | 位置 | 处置 |
|---|------|------|------|------|
| 1 | **P1** | 对 `failure_stage='index'` 的失败任务点「重试」时，`job.update(status='queued')` 之后紧跟 `_persist_locked()` 且无 try：`jobs.json` 被 AV/OneDrive 占用时异常穿出 `retry_job`（只捕 RuntimeError）→ 500，而内存里任务已改 queued、`_pending`/`_dispatch_locked` 都没执行 → 卡片永远「排队中 0%」，再点重试被 `retry_state` 拒绝，只能重启 | `tiklocal/services/downloader.py:752-762`、`tiklocal/web/downloads.py:457-463` | 已修 |
| 2 | P2 | 作者域 4 个路由把 `str(exc)` 直接回给客户端：sidecar 未启动时原文是 `HTTPConnectionPool(host='127.0.0.1', port=8080): Max retries exceeded with url: /api/...`，等于把内网地址与私有路由交给任意 LAN 客户端（同仓库 `captions.py:80-84` 对同类情况只记日志 + 稳定文案） | `tiklocal/web/author.py:255-256,275-276,325-326,341-342` | 已修 |
| 3 | P2 | 改目录先落盘、`apply_sources` 生效，随后 `sync()` 只把 locked/busy 降级成 warning，其余 `raise`：索引损坏（`database disk image is malformed` 是 DatabaseError，不是 OperationalError）时接口 500，前端不刷新 → 界面旧列表、服务端新列表、新文件夹直到重启才被扫 | `tiklocal/web/settings.py:177-197` | 已修 |
| 4 | P2 | `size = target.stat().st_size` 与两处 `open(target,'rb')` 在 `serve_media()` 的 `exists()` 之后无保护：播放中在另一标签页删掉该视频后拖进度条触发 Range → `FileNotFoundError` 未捕获 → 500 + JSON 错误体（普通缺文件是 404 纯文本），播放器硬失败 | `tiklocal/web/media.py:376,400` | 已修 |
| 5 | P2 | `author_profiles.set_profile_url()` 无 try：`profiles.json` 只读/被占用时 `write_json_atomic` 抛 OSError 直穿 500，绑定没保存但页面无提示 | `tiklocal/web/author.py:299,314` | 已修 |
| 6 | P3 | `GET /api/media/info` 回 `'path': str(target)`，即 `C:\Users\<用户名>\Videos\…`；默认 `host=0.0.0.0` 时同网段可枚举用户名与目录结构（与第 22 轮 E5 同源） | `tiklocal/web/media.py:477` | 并入既有条目 |
| 7 | P3 | `store.set_source_path(source)` 在 `store.pull()/push()` 之前执行且失败无回滚 → 报 400 但坏路径已持久化，刷新后输入框显示坏值 | `tiklocal/web/downloads.py:210-222` | 转 backlog |
| 8 | P3 | 未配 sidecar 时 `/api/author/<id>/search` 返 503，而同条件的 `_sync_profile`、update-videos、`author_sync` 返 400；503 的语义是「稍后重试」，实际是永久配置问题 | `tiklocal/web/author.py:333` | 转 backlog |

域内已排除：错误响应缺 `success` 键（客户端按 `!response.ok || payload.success === false` 判定）；`/api/download/jobs?limit=` 无上下界（`list_jobs` 内部 `max(1, min(limit, DOWNLOAD_HISTORY_LIMIT))`）；URL 列表的错误文本不含绝对路径；`enqueue` 只抛 RuntimeError；`_byte_range()` 的边界与 RFC 9110 一致。

## 二、服务层输入校验与外部进程（域 E2）

| # | 级别 | 问题 | 位置 | 处置 |
|---|------|------|------|------|
| 9 | **P1** | `_move_file_to_media_root` 里两处 `logger.warning(...)`，而模块只定义了 `LOGGER`：媒体库根只读、磁盘满、目标名超长或被杀软锁住时抛 `NameError` 逃出函数，`_execute_download_gallery_dl` 的 `finally: shutil.rmtree(temp_dir)` 把尚未搬走的已下载文件全删，任务以 `name 'logger' is not defined` 失败 | `tiklocal/services/downloader.py:1537,1546` | 已修 |
| 10 | P2 | yt-dlp 退出 0 但 `output_rel_list` 为空（「has already been downloaded」、`[MoveFiles]` 变体、只跑了后处理器）→ 记 success / 0 文件 / 无错误，文件入库却不进索引；gallery-dl 分支有保护，yt-dlp 没有 | `tiklocal/services/downloader.py:985,1139-1140` | 已修 |
| 11 | P2 | 直链下载不校验实际收到字节数、也不校验 Content-Type → CDN 断连的截断 mp4、或 200 + HTML 挑战页被 `os.replace` 搬进媒体库并记 success | `tiklocal/services/downloader.py:1273-1292` | 已修 |
| 12 | P2 | 图集/实况 N 个 URL 中 1 个 403 时 `return 1, error, []`，已下好的 N-1 个仍留在 temp_dir 被 `finally` 的 `rmtree` 删掉，与 `_run_job` 的「部分成功」设计相反 | `tiklocal/services/downloader.py:1228-1230` | 已修 |
| 13 | P2 | `urlparse(url)` 无 try（同文件 `_is_local_host` 反而包了 ValueError）→ `POST /api/download/jobs {"url":"http://[::1"}` 抛 `ValueError("Invalid IPv6 URL")`，`web/downloads.py:281` 在 try 之外 → 500 HTML 而非 400 | `tiklocal/services/downloader.py:276` | 已修 |
| 14 | P2 | `urlparse(text)` 同样无 try → 手改 `download-urls.txt` 含一行 `http://[::1` 时 `normalize_url → parse_text_limited → lines()` 冒泡，`web/downloads.py:239` 只捕 OSError → 500，整页队列与整批导入全废 | `tiklocal/services/url_list.py:50` | 已修 |
| 15 | P2 | 批量入队循环里 `enqueue()` 无 try：download `enabled: false` 时抛 RuntimeError，`web/downloads.py:315-319` 只捕 ValueError/TikTokDownloaderError → 500，且已入队的 job 从响应里消失 | `tiklocal/services/tiktok_downloader.py:1052` | 已修 |
| 16 | P2 | 校验允许 `max_concurrent=0`，而 `_dispatch_locked` 把 `limit == 0` 当「无上限」→ 一次起整个 pending 队列（上限 5000）的线程与 yt-dlp 进程 | `tiklocal/services/downloader.py:204,925` | 转 backlog |

域内已排除：cookie 路径穿越（`is_safe_cookie_filename` + `resolve().relative_to(cookie_dir)`）；captions provider 异常（`web/captions.py:80` 捕 Exception → 502 固定文案）；取消/超时的子进程回收；每 job 独占的临时目录与 cookie 副本；目标名竞争（`O_CREAT|O_EXCL` + 1000 次上限）。

## 三、文件系统与路径安全（域 E3）

| # | 级别 | 问题 | 位置 | 处置 |
|---|------|------|------|------|
| 17 | P2 | 缩略图缓存键只含**默认**根 + `@source_id/rel`，且只在默认源路径变化时才重算：把非默认来源改到别的目录、或删源后新建同名文件夹复用同一 id（`_unique_id` 只看当前列表）→ 新目录里 mtime 更旧的文件命中旧缓存，网格显示上一个目录的画面 | `tiklocal/services/thumbnail.py:167`、`tiklocal/web/settings.py:183-186` | 转 backlog |
| 18 | P2 | 移除来源后该源 `.tiklocal-trash` 下的文件不再被列出、`restore` 抛 KeyError、30 天保留期永不生效 → 磁盘永不回收且用户无入口（与第 22 轮 C4 同源） | `tiklocal/services/trash.py:89-90,106`、`media_sources_store.py:228-245` | 并入既有条目 |
| 19 | P2 | 回收站目标路径比原路径长约 39 字符：全路径落在 221..260 时能扫描/播放，但 `shutil.move`（MoveFileExW，未开 LongPathsEnabled）抛 `OSError(winerror 3)` → 这类文件永远删不掉，`media.py:268` 只把 32/33 当冲突故回 500，重试无效 | `tiklocal/services/trash.py:26` | 转 backlog |
| 20 | P2 | `source.path.is_dir()` 与 `_coerce_path` 的 `.resolve()` 对 NAS/离线映射盘（`\\nas\media`、`Z:\`）无超时（Windows 默认 20 s 起）：打开设置页或每次 `/api/library/sources` 阻塞一个 waitress 线程，多来源可占满线程池；启动时对每个已存来源做同样调用 | `tiklocal/services/media_sources_store.py:350,116` | 转 backlog |
| 21 | P3 | 头像下载用固定临时名 `path.with_suffix('.jpg.tmp')`，而该模块注释自认「绑定主页链接」与「同步博主」会并发 → 两次不同 URL 互相覆盖成撕裂 JPEG，崩溃后 `.jpg.tmp` 永久残留 | `tiklocal/services/author_profiles.py:192-196` | 已修 |
| 22 | P3 | `resolve_path()` 只做包含性校验、不要求 `is_file()` → `/detail/@default/sub`（目录）与 `/detail/@default/NUL`（设备）通过 `exists()` 校验，渲染 200 详情页并 `target.stat()`；`/api/playback` 也接受目录 URI 落库 | `tiklocal/services/library.py:325-328` | 已修 |

域内已排除：`..`/绝对路径/UNC/驱动器相对名穿越（`relative_to` 抛 ValueError → None，大小写经 WindowsPath 归一）；`_is_reparse_dir` 与 `os.walk(followlinks=False)` 不成环；`write_json_atomic` 不半写；`trash._entry` 的 name 过滤挡住 `/`、`\`、前导 `.`；`scan_source` 的 `onerror` 不把「不可读」当「全被删」。

## 四、索引与数据库边界（域 E4）

| # | 级别 | 问题 | 位置 | 处置 |
|---|------|------|------|------|
| 23 | **P1** | `register_uris()` 用 `with self._sync_lock` 无超时，而冷扫可在锁内持数十秒（19k 图库 EXIF 探测，且 `/api/library/sync` 就跑在请求线程上）：`/api/trash/<id>/restore` 与下载完成回调都会阻塞，`_register_uris_locked` 还在锁内做 `time_states()` 全表物化 | `tiklocal/services/library_index.py:985,989` | 已修 |
| 24 | P2 | 损坏库隔离时 `for extra in ('-wal','-shm'): sidecar.unlink(missing_ok=True) / except OSError: pass`：①被保留的 `.corrupt` 丢掉所有未 checkpoint 的事务；②sidecar 仍被占用时 unlink 静默失败 → 新库带着旧 WAL 打开、`migrate()` 读到旧 `schema_migrations` 跳过所有迁移 → 起库后处处 DatabaseError，实例永久卡死 | `tiklocal/app.py:148-153` | 已修 |
| 25 | P2 | `_read()` 的 `except Exception` 把 AV/编辑器的瞬时占用当成「文件损坏」：非 strict 分支返回空 payload → 收藏夹页面变空、形似数据丢失；`_quarantine` 还把这份**健康**文件复制成 `.corrupt` 且 `if backup.exists(): return`，后续真正损坏永不再被保存 | `tiklocal/services/collections.py:86-93` | 已修 |
| 26 | P3 | `_backoff` 上限 400 且 `skip` 截断：未装 ffprobe 时第 401 个之后的 uri 永远进不了 skip 列表，`while True` 空转到 5 s 预算耗尽 | `tiklocal/services/library_index.py:1193-1196,1224-1227` | 转 backlog |
| 27 | P3 | `"indexed": len(records)` 把因 ffprobe 缺失而被跳过的行也计入，UI 报成功但文件永不可见 | `tiklocal/services/library_index.py:145` | 转 backlog |
| 28 | P3 | 头像固定临时名的并发撕裂（与 #21 同一处，域内重复上报，不重复计） | `tiklocal/services/author_profiles.py:193-196` | 同 #21 |

域内已排除：`page()` 的 `preserve_order` 清空 where/params 但非 all scope 强制 `search=''`（不可达）；`_records_for_paths` 线程池只做 stat/EXIF；迁移 012/013 在 `BEGIN IMMEDIATE` 内二次校验；变量上限均 <999；`_is_damaged_database` 只隔离 malformed/corrupt。

## 五、线程与进程生命周期（域 E5）

| # | 级别 | 问题 | 位置 | 处置 |
|---|------|------|------|------|
| 29 | P2 | `_terminate_process` 无条件对已正常退出的子进程发 `taskkill /T /F`（`_run_job` 的 finally 对每个 job 都调它）：PID 复用后会误杀无关进程树（编辑器/构建/另一个 job 的 ffmpeg），每个 job 还多 spawn 一个 taskkill | `tiklocal/services/downloader.py:1466,1076` | 已修 |
| 30 | P2 | 单张视频封面可能串行跑「时长探测 10 s + 色彩探测 10 s + 4×30 s ffmpeg」，HDR 回退再 +30 s，单请求上限约 170 s；而 `_GENERATE_SLOTS` 只有 2 槽 → 6-8 个并发冷请求钉死全部 8 个 waitress worker 2-3 分钟 | `tiklocal/services/thumbnail.py:540-563` | 已修 |
| 31 | P3 | `--dev` 时父进程先完整 `create_app()`（全量 sync + media_probe.start + DownloadManager）再起 reloader 子进程重跑 `main()`：启动扫描做两次，父进程留着常驻 probe 线程抢 WAL 写锁 | `tiklocal/run.py:706,739` | 转 backlog |
| 32 | P3 | 关闭时先串行终止所有子进程（每个 2 s wait + taskkill ≤15 s）之后才起算 5 s join deadline → 3 个并发 job 最坏约 50 s | `tiklocal/services/downloader.py:487-491` | 转 backlog |
| 33 | P3 | `finally` 先还原 SIGTERM 再 `download_manager.close()` → 收尾期间的第二次 Ctrl+C 直接终止，yt-dlp/ffmpeg 变孤儿继续往 `.tiklocal-download-tmp` 写 | `tiklocal/run.py:775-777` | 转 backlog |
| 34 | P3 | `create_app()` 在监听端口前同步全量扫描；`MediaProbeWorker` 只有 `start()`，没有 `stop()/join()` | `tiklocal/app.py:428,433-434` | 转 backlog |

域内已排除：`close()` 不会 KeyError（`_load_history` 建 Event）；jobs.json 不半写；SQLite 连接不泄漏（`database.py:400-417` 的 `@contextmanager`）；槽位不因异常永久泄漏；werkzeug 重载的 `sys.exit(3)` 会穿出 `close()`；全仓线程均 `daemon=True`。

## 六、日志与可观测性（域 E6）

| # | 级别 | 问题 | 位置 | 处置 |
|---|------|------|------|------|
| 35 | **P1** | 所有致命启动错误都发生在 `create_app()`（日志初始化的地方）之前，只写 stderr：nssm、任务计划、pythonw 或 `> nul` 启动时 stderr 被丢弃 → 启动失败后 `tiklocal.log` 零记录 | `tiklocal/run.py:636,231-232,538,542,554-556,748-750` | 已修 |
| 36 | P2 | 500 处理器里 `app.logger.exception(...)`，而 Flask 在调用它之前已 `log_exception` 记了完整 traceback → 每个 500 落两份，2 MB×3 轮转的有效保留量减半且看着像两个故障 | `tiklocal/app.py:604-614` | 已修 |
| 37 | P2 | collections 读失败返回空 payload 且零日志：收藏夹页面变空，日志里没有任何线索（与 #25 同源，此处计「静默」这一半） | `tiklocal/services/collections.py:89-93` | 已修（随 #25） |
| 38 | P2 | `radio_energy.py` 全文无 logger：ffmpeg 解码失败/超时、非 16bit WAV、缓存不可读全静默 → 波形恒平线，日志无法区分「没装 ffmpeg」与「音轨解不了」 | `tiklocal/services/radio_energy.py:61-63` | 已修 |
| 39 | P2 | purge 时 `thumbnail_service.delete_thumbnail()` 包在 `except OSError: pass` 内，紧邻的收藏清理却会 warning → 缩略图残留被同名新文件复用，日志里什么都没有 | `tiklocal/web/media.py:222-231` | 已修 |
| 40 | P3 | cookie 上传被拒时也打 `'URL list request rejected: %s'`，排查被引到无关功能 | `tiklocal/web/downloads.py:101,125` | 已修 |
| 41 | P3 | 相对 `TIKLOCAL_INSTANCE` 的 warning 在 `app.py:204` 实参求值阶段触发，此刻 root logger 还没有 handler，只落 last-resort stderr，永远进不了 `tiklocal.log` | `tiklocal/paths.py:31-37` | 转 backlog |

域内已排除：凭据脱敏（`RedactFilter` + `RedactingFormatter` 覆盖 message 与 traceback，并把 `args` 清空）；重复输出（`app.py:103` 的 `_logger` 与 `app.logger` 同为 `tiklocal.app`）；轮转自带锁；traceback 不进用户响应；级别使用。

## 七、配置、启动与打包（域 E7）

| # | 级别 | 问题 | 位置 | 处置 |
|---|------|------|------|------|
| 42 | P2 | `TIKLOCAL_PORT` 在判断 `args.port` 之前就被 `int()`：残留 `TIKLOCAL_PORT=8o00` 时 `tiklocal --port 9000` 直接 exit 2，CLI 最高优先级被无视 | `tiklocal/run.py:505-511` | 已修 |
| 43 | P2 | `validate_config_types(config)` 在构造 parser 之前：config.yaml 写 `port: "8000"`（带引号）时 `--help`/`tls status`/`auth status`/`thumbs` 全部 exit 2，报错也不含实际读取的文件路径 | `tiklocal/run.py:259-260` | 转 backlog |
| 44 | P2 | `user: "1000:1000"` + `./tiklocal-data:/data`：新克隆按文档 `docker compose up -d --build` 时宿主机没有该目录，Docker 以 root 建 0755 → 容器内 `auth_store.ensure()` PermissionError，`run.py` 只捕 ValueError → 裸 traceback | `compose.yaml:10,18` | 转 backlog |
| 45 | P3 | `if 'thumbs' in argv: argv.insert(0,'thumbs')` 会把 `--tls-hosts thumbs`、`--name thumbs`、`tls generate --hosts thumbs` 整条命令改写 | `tiklocal/run.py:269-277` | 已修 |
| 46 | P3 | 纯 HTTP 在 `create_app()`（含全库索引）之后才 bind，`--tls` 分支有 `probe_listen` 而 HTTP 没有 → 端口被占时要等几分钟才报错，期间已建库、写 auth.json | `tiklocal/run.py:745-751` | 转 backlog |
| 47 | P3 | 配置只校验已知键：`prot: 9000`、`medai_root:` 这类拼错静默忽略并按默认值启动，零 warning | `tiklocal/run.py:104-159` | 转 backlog |
| 48 | P3 | `docs/https.md` 称 `https: yes` 是 startup error，但 PyYAML 把 `yes` 解析成 True 放行、静默以 HTTPS 起在 8443（compose 只发布 8000）；`run.py:134-136` 的注释把同一事实说反 | `docs/https.md:32-34`、`tiklocal/run.py:134-136` | 转 backlog |
| 49 | P3 | 代码直接 `from werkzeug.serving import make_server` 与 `werkzeug.security`，依赖里却没有 werkzeug → `pip install --no-deps` 时在启动阶段 ImportError 而非安装阶段报错 | `pyproject.toml:18-24` | 已修 |

域内已排除：配置优先级 CLI > env > config > 默认逐项一致；端口/主机边界 1..65535、`--port 0`、bool/float port 全拦；`~` 展开除 `run.py:545` 外都有且 app 内会再纠正；版本号三处一致、poetry-core 默认打包整个 `tiklocal/`；Dockerfile 入口/环境与 `docs/docker.md` 一致（8443 冲突已登记）。

## 八、修复取舍

- **4 条 P1 全部修**：其中 #9 是本轮唯一一条「错误处理本身写错」的缺陷（`logger` vs `LOGGER`），后果是把已下载文件删光，必须修；#1、#23、#35 都是「一次失败让功能或实例长时间不可用」。
- **19 条 P2 修**：集中在三组。①**错误路径把内部信息或未完成状态当成功**（#2 泄漏内网地址、#10 零文件记 success、#11 截断文件入库、#12 部分成功被删、#3/#4/#5 该 503/404 的地方给 500）；②**一次异常把整个请求变成 500**（#13/#14/#15 的 `urlparse` 与 `enqueue`）；③**收不住的外部资源**（#29 taskkill、#30 封面预算、#24 WAL、#36 双份 traceback）。#37 随 #25 一起解决——不再把瞬时占用降级成空列表，异常到路由层会由全局 500 处理器记录。
- **5 条 P3 修**：#21/#22 是「同一函数里的正确性」顺手改（`is_file()`、唯一临时名），#40 是日志标签，#45 是命令行解析，#49 是一行依赖声明。
- **两条「改了又回退」**（记在这里避免下一轮重做）：
  - #17 缩略图缓存键扩到全部来源 → `tests/test_thumbnails.py::test_cli_cache_is_reused_by_web_and_isolated_between_sources` 两条参数化用例立刻失败（CLI `generate_thumbnails()` 只认识默认根，键一扩 CLI 生成的缓存就不再被 web 复用，19k 图库启动会全部重生成）。根因是「来源隔离」本来就由 URI 里的 `@source_id` 承担（`cache_path()` 先 `canonicalize_uri`），根键只负责「同一 `@default/rel` 落在不同库」。回退并转 backlog，修它必须同时让 CLI 拿到完整来源列表。
  - #8 作者搜索未配置 sidecar 从 503 改 400 → `tests/test_author_page.py` 两条既有用例失败。这是产品语义变更而不是缺陷修复，回退并转 backlog。

## 九、修复实现要点

1. `tiklocal/run.py` 新增 `_fatal(message)`（打印 stderr + `LOGGER.error` + `sys.exit(1)`），把 8 处致命 `print(...stderr)` 收进来；`main()` 里 `data_dir = Path(resolve_data_dir(parser))` 之后立即 `configure_logging(data_dir)`（`configure_logging` 用 `_tiklocal_logging_configured` 保证只配一次，`create_app` 里那次变成 no-op）。
2. `tiklocal/run.py` 子命令归一化只认两种文档化形式（`argv[0] == 'thumbs'`，或 `argv[1] == 'thumbs'` 且 `argv[0]` 不以 `-` 开头），其余落到「未知子命令则插 serve」。
3. `tiklocal/run.py` env 端口只在 `args.port is None` 时解析，错误信息带原值。
4. `tiklocal/services/downloader.py`：`_execute_download` 改为先取 `result`、`_normalize_execute_result` 拆包，再统一判「`code == 0` 且无产物 → `return 1, t('download.error.no_output'), []`」。判定放在分发器而不是 `_run_job`，因为它约束的是**外部工具**的怪癖（yt-dlp 对「已下载过」也退 0），不应影响内部调用与测试桩。`_run_job` 的成功分支因此恢复 `output_rel_list[0] if output_rel_list else ""`。
5. `tiklocal/services/downloader.py`：`_terminate_process` 首行 `if process.poll() is not None: return`；`_parse_download_payload` 的 `urlparse` 包 ValueError；直链下载在读 body 前查 `Content-Type`、在 `os.replace` 前比对 `received` 与 `Content-Length`；`_execute_download_direct` 的错误分支先把已下好的文件搬进媒体库再返回；`retry_job` 的 `_persist_locked()` 失败时回滚内存字段并返回 503 文案。
6. `tiklocal/services/url_list.py`：`normalize_url` 的 `urlparse` 包 ValueError 返回 `''`（坏行被当成无效行丢弃，不再让整页队列 500）。
7. `tiklocal/services/library.py`：拆出 `resolve_target_path()`（只做 resolve + `relative_to` 包含性校验，不要求文件存在）与 `resolve_path()`（在其上加 `is_file()`）。`trash.restore()` 改用前者——它要恢复的文件此刻在回收站里，加 `is_file()` 会让恢复必然失败（`tests/test_trash.py` 当场抓出）。
8. `tiklocal/services/library_index.py`：新增 `REGISTER_LOCK_TIMEOUT_SEC = 0.5`，`register_uris()` 用 `acquire(timeout=...)`，拿不到就 `LOGGER.warning` 并返回 0（两个调用方都把 0 当「尚未索引」，下次扫描补上）。
9. `tiklocal/services/collections.py`：`except ValueError` 才是「字节不是 collections 文件」（隔离 + 严格抛 `CollectionStoreCorrupt`），`except OSError: raise` 让路由回 503。
10. `tiklocal/services/thumbnail.py`：新增 `VIDEO_FRAME_BUDGET_SEC = 35.0`，候选帧循环按剩余预算 `min(FFMPEG_TIMEOUT_SEC, remaining)` 取超时，预算耗尽就带着已选的最亮帧收工。
11. `tiklocal/app.py`：`_quarantine_database()` 把 `-wal`/`-shm` **改名**成 `<backup>-wal`/`<backup>-shm`（与备份同去，不删字节），改名失败则返回 None 拒绝重建——带着旧 WAL 起新库会重放旧事务并让 `migrate()` 跳过所有迁移。
12. `tiklocal/web/media.py`：`_ranged_media` 的 `stat()` 与两处 `open()` 包 `try/except OSError: return None`（回落 404）；purge 的派生清理失败改为 warning。
13. `tiklocal/web/author.py`：4 处 `str(exc)` 换成固定文案 + `logger.warning(..., exc_info=True)`；`set_profile_url` 的 OSError 回 503。
14. `tiklocal/web/settings.py`：`sync()` 的 `sqlite3.Error`（含 `DatabaseError: database disk image is malformed`）降级为 `scan['error']`，接口照常 200。
15. `tiklocal/web/downloads.py`：4 条日志按路由分别命名；`tiklocal/services/radio_energy.py` 新增模块 logger 并在两个静默分支记 warning；`tiklocal/services/tiktok_downloader.py` 的批量入队逐条捕 RuntimeError 并在响应里带 `failed`/`error`；`tiklocal/services/author_profiles.py` 的临时名改为 `pid + itertools.count()` 并在 finally 清理。

## 十、验证

- 全量：`pytest -q` = **602 passed / 8 skipped / 0 failed**（203.76 s）。本轮没有新增 `pytest` 用例：27 处修复里绝大多数是「异常路径改成正确的状态码/日志」，而现有用例已经把每条路径的成功分支钉住了；真正需要新用例的两处（`_quarantine_database` 的 WAL 改名、`_execute_download` 的零产物判定）要么依赖 Windows 文件占用时序，要么会与 `tests/test_download.py::test_reducing_concurrency_waits_for_running_jobs` 的测试桩语义冲突（见 §八），故以「既有用例全绿 + 手工核对 diff」为准，缺口记在 §十一。
- 分组回归：`test_thumbnails/test_media_sources/test_run_media_sources/test_downloader_lifecycle/test_download_urls/test_tls/test_web_runtime` = 103 passed / 1 skipped；`test_trash/test_collections/test_douyin_features/test_author_page/test_media_state` = 48 passed；`test_radio/test_download/test_tiktok_downloader/test_media_state/test_request_boundaries` = 100 passed / 2 skipped（修完 #29 的 `taskkill` 守卫与 #10 的判定位置后复跑为 76 passed / 2 skipped）。
- 两次由测试抓出的回归：#22 的 `is_file()` 让 `tests/test_trash.py` 的恢复用例失败（改用 `resolve_target_path` 解决）；#10 的判定最初放在 `_run_job`，让 `tests/test_download.py::test_reducing_concurrency_waits_for_running_jobs` 的 `(0, '', [])` 桩被判成失败（判定下移到分发器解决）。这两次都是「先写实现、被既有用例纠正」，不是用例过时。

## 十一、覆盖率缺口

1. `_quarantine_database()` 的 WAL 改名分支没有自动化用例：需要构造「sidecar 被占用 → 改名失败 → 拒绝重建」的时序，在 Windows 上依赖真实的文件锁行为，测试会很脆。
2. `_execute_download` 的「零产物即失败」只有间接覆盖（`tests/test_downloader_lifecycle.py` 的假引擎都会返回产物）；真实 yt-dlp 的「already downloaded」路径无法在离线环境复现。
3. `VIDEO_FRAME_BUDGET_SEC` 的预算耗尽分支没有用例：需要让 ffmpeg 每次调用都接近超时，只能用桩，而桩会把「真的在解码」这件事抹掉。
4. `TIKLOCAL_PORT` 与 `--port` 的优先级只有手工验证（`TIKLOCAL_PORT=8o00 .venv\Scripts\tiklocal.exe --port 9000` 现在能起来），没有进入 pytest——`run.py` 的 `main()` 依赖 `sys.argv` 与真实监听。
5. `docker compose` 的 `/data` 属主问题（#44）未复现：本机没有 Docker 环境，结论来自 `compose.yaml` 与 `app.py:251` 的静态推演。

## 十二、转 backlog

18 条转 backlog（#7、#8、#16、#17、#19、#20、#26、#27、#31、#32、#33、#34、#41、#43、#44、#46、#47、#48），另 3 条并入既有条目（#6 与第 22 轮 E5 同源、#18 与第 22 轮 C4 同源、#28 与 #21 同源）。明细见 `docs/audit/backlog.md` 的「第 24 轮新增」段。
