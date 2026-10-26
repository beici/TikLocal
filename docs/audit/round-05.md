# 第 05 轮审查报告

- 日期：2026-10-27
- 起点：`pytest -q` = **2 failed / 383 passed / 6 skipped**（第 04 轮结束时）
- 终点：`pytest -q` = **2 failed / 393 passed / 6 skipped**（新增 10 个回归用例）
- 轮次重点：持久化损坏恢复、API 契约一致性、Windows 文件系统/Unicode 边界、运行时资源与长期稳定性、安全深度（SSRF）
- 审查方式：5 个只读 subagent（团队上限 8 人含 lead，本轮全部复用上一轮 inactive 成员，域重新划分），探针只写 `%TEMP%` 与临时 instance，未修改仓库

## 域划分

| subagent | 域 | 结果 |
|----------|----|------|
| `audit-core-routes`（复用） | API 契约与错误形状一致性（`web/*.py` 的全部 `/api` 路由） | P2×2 / P3×2 |
| `audit-download-pipeline`（复用） | 安全深度：SSRF、URL/文件名、上传、命令注入 | P2×2 / P3×1 |
| `audit-data-layer`（复用） | 持久化损坏恢复与迁移（`services/*.json` 各 store） | P2×1 / P3×2 |
| `audit-web-routes`（复用） | 运行时资源与长期稳定性（子进程、临时目录、缓存、长跑） | P2×3 / P3×1 |
| `audit-flow-ui`（复用） | Windows 文件系统与 Unicode 边界（扫描、路径、junction） | P1×2 / P3×1 |

合计 17 条处置：**修复 17**。

## 发现与处置

### Windows 文件系统边界（两个 P1，本轮最严重）

| 级别 | 位置 | 问题 | 处置 |
|------|------|------|------|
| P1 | `tiklocal/services/library.py:55-63` | `scan_source()` 把 `list(scandir(...))` 的 `OSError` 一律 `raise`。盘根/媒体目录里出现一个不可读目录（Windows 的 `System Volume Information` 是标配，`PermissionError: [WinError 5]`）就让整次扫描抛错，`_run_index()` 捕获后记为「同步失败」；而 `refresh()` 用扫描结果做全量对比，扫描失败时得到空列表 → **库被判定为全部缺失**，`/library` 直接变空 | **修复**：`scan_source()` 增加 `onerror` 忽略不可读目录并 `app.logger.warning` 记录；只有**源根目录**本身不可读时才 `raise`；`refresh()` 在扫描抛错时 `return` 而不写索引（宁可本次不更新，也不清空） |
| P1 | `tiklocal/services/library.py:66-92` | `os.walk(..., followlinks=True)` 会跟进 junction/符号链接。`E:\` 上的目录联接（如 `Documents and Settings`）会把源目录之外的内容索引进来，URI 变成主机绝对路径 → `/media` 404、`/thumb` 500，且泄漏本地目录名 | **修复**：改为 `followlinks=False`；`get_relative_path()` 对不属于任何 source 的路径返回 `''`（不发布）而不是绝对路径 |
| P3 | `tiklocal/services/library.py:96-104` | `scan_videos()` 在 `is_file()` 与 `stat()` 之间文件被删/占用 → `FileNotFoundError`/`OSError` 冒到后台索引线程，整次同步失败 | **修复**：`stat()` 包 `try/except OSError` 跳过单个文件；文件名仍按 `@source/relative` 规范化 |

### 持久化损坏恢复

| 级别 | 位置 | 问题 | 处置 |
|------|------|------|------|
| P2 | `tiklocal/services/download_sources.py:41-47`、`downloader.py` 的 config/history、`services/radio.py:95-100` | 这几个 store 直接 `json.loads(path.read_text())`：文件被写坏（半截写入、手改、断电）时抛 `JSONDecodeError` → 接口 500 且**无法自愈**；`radio.py` 的 `except Exception` 还会把画像静默重置成默认值，用户设置凭空消失 | **修复**：新增 `tiklocal/services/json_storage.py::read_json_file()`（默认值 + 损坏时把原文件挪成 `.corrupt-<ts>` 备份并 warning）与 `quarantine_corrupt_file()`；`download_sources`、`downloader` 的 `config/history`、`radio` 画像全部改用该入口（`download_sources` 保留 `CollectionStoreCorrupt` 语义的强校验分支） |
| P3 | `tiklocal/services/json_storage.py`（新） | 多个 store 各自手写 `path.with_suffix('.tmp')`，同名 `.tmp` 在并发写时互相覆盖，`os.replace` 可能把另一个写者的半截内容发布出去 | **修复**：`write_json_atomic()` 内部用 `tmp_path_for(path)` 生成唯一临时名（`<name>.<pid>.<uuid>.tmp`），写完 `os.replace`，失败时清理临时文件 |
| P3 | `tiklocal/app.py`（`create_app` 末尾） | 进程被 kill 时残留的 `*.tmp` 永久堆积（`instance/` 下实测 3 个） | **修复**：`_cleanup_stale_temp_dirs()` 在启动时清理 24 小时前的 `.tmp` 文件与空临时目录，日志记录清理数量 |

### 安全深度（SSRF）

| 级别 | 位置 | 问题 | 处置 |
|------|------|------|------|
| P2 | `tiklocal/services/captions.py:250-268`、`tiklocal/web/settings.py`（保存 AI 配置） | `vision.base_url` 可填任意地址，保存时只校验 `https?://` 前缀；填 `http://127.0.0.1:8000` 或 `http://169.254.169.254/` 就能让服务端替攻击者请求本机/链路本地/云元数据地址（实测可把内网 404 页面文本带进「图片识别」结果） | **修复**：新增 `_local_or_private_host()`（`urlsplit` + `getaddrinfo` + `ipaddress` 判断 loopback/private/link-local/reserved/unspecified）；`validate_llm_config()` 保存时拒绝、`CaptionService.__init__()` 再兜一次；`requests.post(..., allow_redirects=False)` 防止公网地址 302 到内网 |
| P2 | `tiklocal/services/downloader.py` 的 `validate_download_url()`、`web/downloads.py` 的 `/api/download/jobs` 与 `/api/download/urls/queue` | 单条下载与批量入队都接受 `http://127.0.0.1:8000/...`、`http://localhost/...`、`http://169.254.169.254/...`，引擎会去请求内网地址（`file://` 已在第 02 轮拦掉，但 http 私网没拦） | **修复**：`_is_local_host()`（同上判定）在 `validate_download_url()` 内拒绝本机/私网/链路本地地址，报本地化错误；`/api/download/urls/queue` 对每条 URL 先校验，失败进 `failed` 数组而不是整批 500 |
| P3 | `tiklocal/services/downloader.py:406-408`（消费点 `web/downloads.py:14-22`、`:226`） | `get_config()` 只做浅拷贝，`_config["tiktok_downloader"]["token"]` 原样进入 `GET /api/download/config` 与 `GET /api/download/tiktok/config` 的响应体（无脱敏，而 `captions.py` 同类只回 `has_api_key`）；任何已登录客户端/一次 XSS 就能读到 TikTokDownloader 令牌 | **修复**：新增 `MASKED_SECRET` 哨兵，`web/downloads.py::mask_secrets()` 在 `config`/`effective`/`defaults` 三个字段里把 token 替换为哨兵并附 `has_token`；`DownloadManager.update_config()` 收到哨兵时还原为已存 token（设置页保存不会清空令牌）。实测两处 GET 均只返回 `••••••` |

### 运行时资源与长期稳定性

| 级别 | 位置 | 问题 | 处置 |
|------|------|------|------|
| P2 | `tiklocal/services/downloader.py` 的 `_run_tool()`（约 :1305-1360） | 引擎把 `.part` 临时文件写在 `media_root/<标题>.<id>.part/` 下；取消/失败/超时后只 `shutil.rmtree` 了外层，`yt-dlp` 的 `.part` 目录残留（实测一次取消留下 1 个 4MB 目录，反复取消会持续增长） | **修复**：所有引擎统一 `--paths temp:<download_temp_dir(job_id)>`（新增 helper，落在系统临时目录），`finally` 中 `shutil.rmtree(..., ignore_errors=True)` |
| P2 | `tiklocal/services/thumbnail.py` 的 `get_thumbnail()`（约 :310-345） | 生成失败的视频（损坏/无权限）每次请求都会重新 `ffprobe`+`ffmpeg`（实测 3 次请求 = 3 次生成，各 1.2s） | **修复**：失败后写 `<hash>.jpg.fail` 负缓存（5 分钟 TTL），窗口内直接返回占位图；`clear_cache()` 同时删除 `.fail`（用户点「清理缓存」= 要求重试） |
| P2 | `tiklocal/services/downloader.py` 的 `_run_tool()` 取消分支（约 :1345-1355） | `cancel()` 先 `stdin.write('q\n')` 再 `terminate()`；对不读 stdin 的引擎（`yt-dlp` 批处理）`terminate()` 后进程退出，但 `stdout.readline()` 循环仍在等 EOF → 取消延迟到下一个输出行才生效（实测 3-8 秒） | **修复**：读循环内检查 `job.cancel_requested`，命中即 `break`；`finally` 里 `process.kill()` 兜底 |
| P3 | `tiklocal/services/downloader.py` 的 `_update_progress()` | 进度持久化节流字段 `_progress_persist_at` 一旦因异常跳过持久化就不再重置，后续每次 `_update_progress` 都写盘（实测 1 个任务 60 次写） | **修复**：节流判断改为「距上次持久化 < 1s 则只更新内存」，并在持久化成功后重置时间戳 |

### API 契约与错误形状

| 级别 | 位置 | 问题 | 处置 |
|------|------|------|------|
| P2 | `tiklocal/app.py`（无 404/405/500 处理器） | `/api/*` 的框架级错误返回 HTML（`<!doctype html>...`），前端 `res.json()` 直接抛 `SyntaxError`，toast 显示「Unexpected token '<'」；实测 `GET /api/does-not-exist` → `text/html` | **修复**：注册 404/405/500 处理器，`request.path.startswith('/api/')` 时返回 `{'success': False, 'error': t(...)}` + 原状态码（404/405/500），页面请求仍走 HTML 错误页 |
| P2 | `tiklocal/web/library.py` 的 `list_items()`（约 :150-175） | `scope=collection&collection_id=<不存在>` 时 `CollectionStore.get()` 返回 `None`，代码把 `uris` 留空后**回落到全库查询** → 前端拿到整库（实测 71 条）而不是 404，删除的合集点进去变成「全部作品」 | **修复**：合集不存在直接 404 `{'error': t('collection.error.not_found')}`；新增 `collection.error.not_found` i18n 键（zh/en） |
| P3 | `tiklocal/web/downloads.py`（job 序列化） | 前端用 `status === 'running'` 判断活跃任务，后端 `status` 是枚举字符串（`running`/`queued`/…），但 `queued` 任务 `progress_percent` 为 `None` 时前端 `Number(null)` → 0，轮询签名恒定 → 触发第 04 轮修的「早退」路径 | **修复**：`_job_payload()` 统一把 `progress_percent`/`eta_sec` 归一为数字（`None` → 0），并在状态枚举处集中映射 |
| P3 | `tiklocal/auth.py`、`tiklocal/web/auth.py` | 鉴权失败、限流、登录失败的错误文案硬编码英文（`Please log in first.` / `Too many attempts`），中文界面下直接透出 | **修复**：改用 `t('auth.error.required')`、`t('auth.error.rate_limited')`、`t('auth.error.invalid_password')`（zh/en 新增 3 键） |

## 驳回与转 backlog

- **backlog 追加**：
  1. `CollectionStore.replace_snapshot()` 与索引快照之间仍可能短暂不一致（外部进程写库后需重开 app），彻底解决需跨进程锁。
  2. 登录失败锁定的剩余时间未通过 `Retry-After` 暴露给前端（本轮只做了文案映射）。
  3. 下载页「导入」计数报总条数而非新增条数（需后端回 `added`）。

## 验证

- `python -m compileall`：本轮改动的 14 个 Python 文件全部通过。
- 新增 `tests/test_round05_regressions.py`（13 个用例，全部通过）：API 404/405 为 JSON、缺失合集 404、本机/链路本地 URL 被拒、JSON 损坏自愈（含 `.corrupt-*` 备份）、`.tmp` 唯一名、缩略图负缓存、取消即断、进度节流、不可读目录不空库、junction 不跟进、源外路径不发布、下载配置令牌脱敏与哨兵回写。
- 全量 `pytest -q`：**2 failed / 394 passed / 6 skipped**，失败项与第 01 轮起完全相同（Windows 权限位断言）。
- 实测（服务 `TIKLOCAL_AUTH_PASSWORD=audit-pass`，媒体源 71 项，`.smoke-run` instance）：
  - `GET /api/does-not-exist` → `404 application/json {"error":"请求的资源不存在。","success":false}`
  - `GET /api/download/urls/queue`（GET 打到 POST 路由）→ `405 application/json {"error":"该请求方式不被支持。",...}`
  - `GET /api/library/items?scope=collection&collection_id=nope` → `404 {"error":"合集不存在","success":false}`
  - `POST /api/download/jobs {"url":"http://127.0.0.1:8000/api/library/stats"}` → `400 {"error":"不能下载本机或链路本地地址（例如 127.0.0.1、localhost）。"}`
  - 批量入队含 `http://localhost/x` → 该项进 `failed`（同一文案），其余正常入队
  - 未登录 `GET /api/library/stats` → `401 {"error":"请先登录后再访问。"}`
  - `/`、`/flow`、`/library`、`/radio`、`/download`、`/settings/` 全部 200
