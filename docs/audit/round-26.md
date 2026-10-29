# 第 26 轮审查报告（外部依赖与网络边界）

前几轮都在看自己写的代码：第 21 轮新代码与安全、第 22 轮数据完整性与并发、第 23 轮前端状态机、第 24 轮后端错误路径、第 25 轮内存与资源预算。本轮换个方向——**凡是越过进程边界的地方**：调用的外部二进制（yt-dlp / gallery-dl / ffmpeg / taskkill）、主动发起的出站 HTTP（sidecar、头像、embedding）、被外部访问的入站面（0.0.0.0 绑定、反代头、TLS、`--dev`）、以及前端引入的第三方资源与外部 URL。

四个域并行审查 + 汇总人自查一个域，共 32 条发现（去重后 32 条）：**P0 0 条、P1 5 条、P2 10 条、P3 17 条**。P1 当轮修完 4 条、缓解 1 条，P2 修 7 条，P3 修 4 条；另有 2 条复核后撤销、1 条驳回、13 条转 backlog。

## 一、下载链路的外部依赖（域 E1）

审查 `tiklocal/services/download_tools.py`、`downloader.py` 中所有 subprocess 与外部程序交界处。

| ID | 级别 | 位置 | 结论 |
|---|---|---|---|
| E1-1 | P1 | `tiklocal/services/download_tools.py:107-137` | `yt_dlp_command` 从不传 `--ffmpeg-location`，而 ffmpeg 只由 `resolve_tool('ffmpeg')` 解析（`TIKLOCAL_FFMPEG`、venv `Scripts`、`imageio-ffmpeg` 三种非 PATH 来源）；按安装文档只装 `imageio-ffmpeg` 时，音视频分离站点跳过合并：落地为无声视频，或直接报 ffmpeg 未安装，而 `/api/download/probe` 仍报 ffmpeg 正常 |
| E1-2 | P1 | `tiklocal/web/downloads.py:246-261` + `downloader.py:606` + `download_tools.py:192-213` | 批量入队对每一行都 `enqueue`，每条都无缓存 spawn 一次 `yt-dlp --version`（超时 15+20 s）；列表上限 5000 行时是 5000 次子进程、半小时到两小时的单请求阻塞，前端先超时而后台仍在逐条入队 |
| E1-3 | P2 | `downloader.py:1505-1531` | Windows 分支先 `terminate()`+`wait(2)` 回收父进程，之后才对同一 pid 执行 `taskkill /T /F`；此时已无法枚举子进程，取消/超时正在合并的任务时 yt-dlp 的 ffmpeg 变成孤儿继续跑，写进已被 `rmtree(ignore_errors=True)` 的 temp |
| E1-4 | P2 | `downloader.py:78-97` | `OUTPUT_ENCODINGS` 把 `locale.getpreferredencoding(False)`（zh-CN=cp936）排第一，而 cp936 解 UTF-8 字节几乎不抛错，永远轮不到 `'utf-8'`；子进程输出 UTF-8 且媒体目录含中文时 `Destination:` 整行 mojibake → 相对路径为空 → 已成功的下载被判 `download.error.no_output` |
| E1-5 | P3 | `downloader.py:1500`、`download_tools.py:221`、`downloader.py:1543` | `sp.Popen`/`sp.check_output`/`sp.run` 均无 `creationflags=CREATE_NO_WINDOW`；pythonw 或快捷方式启动时每次探测、每个任务、每次 taskkill 都弹黑框 |
| E1-6 | P3 | `download_tools.py:192-213` | 只展示 `--version`、无最低版本门禁；`error_line` 把含 `error:` 的行原样回显，yt-dlp<2022 缺 `-S`/`--file-access-retries` 时报错不可诊断 |
| E1-7 | P3 | `download_tools.py:41-43` + `125-131` | 模板字段上限 24+80+80 字符，temp 路径还要再加 media_root；`display_id` 接近上限时总长可越 260，落盘以 WinError 206 失败 |

已排除（确认无问题）：sidecar 字段的信任边界——`payload["url"]` 只有 http(s) 才会被当作下载地址（`:52-77`），`source_domain`/`source_url_*` 只作文本渲染与分组 key，全仓无一处把 sidecar 字段当路径或命令用；yt-dlp 以外部二进制方式调用（非 Python API 混用），无 `shell=True`，cookie 以 `--cookies` 独立文件传入。

## 二、出站 HTTP 与 DNS（域 E2）

审查所有由本应用主动发起的请求：sidecar、头像、embedding、直链下载。

| ID | 级别 | 位置 | 结论 |
|---|---|---|---|
| E2-1 | P1 | `downloader.py:1284-1351` | `_direct_fetch` 分块读到 EOF，**无累计字节上限**（`total_bytes` 只用于进度与完整性比较），唯一兜底是 3600 s 任务超时；sidecar 返回的媒体 URL 可把媒体盘写满 |
| E2-2 | P1 | `author_profiles.py:185-196` | sidecar 作者块的 `avatar_url` 被篡改为 `127.0.0.1` / `169.254.169.254` 时，`download_avatar` 只判 `startswith(('http://','https://'))`：无主机校验、无 `geturl()` 终态复核、无 Content-Type 校验，字节存成 `authors/<id>.jpg` 后由 `/author/<id>/avatar` 原样回吐 |
| E2-3 | P2 | `tiktok_downloader.py:302-303` | sidecar 返回超大或慢速响应时 `response.read()` 无大小上限，`timeout` 只是单次 socket 操作而非总预算 → 单进程 OOM 或 slowloris 长期占线程（线程池仅 8） |
| E2-4 | P2 | `web/author.py:431-444` | 一次 `POST /api/author/sync` 可提交 200 条（`MAX_SYNC_AUTHORS_PER_REQUEST`），逐条串行 `sync_author_profiles([entry])`，每条最长 150 s → 单请求占一个 waitress 线程数小时 |
| E2-5 | P2 | `downloader.py:1284-1306` | payload 内 URL 指向回环/内网/元数据地址时，`_direct_fetch` 只查 scheme，**请求行在 `_is_local_host(final_url)` 之前已经发出**，中间 3xx 跳转全程不校验 → 盲 SSRF |
| E2-6 | P3 | `downloader.py:253` | 域名先解析为公网、校验后再解析为 127.0.0.1 的 DNS rebinding TOCTOU；`_is_local_host` 与 yt-dlp 各自独立解析、无绑定 |
| E2-7 | P3 | `tiktok_downloader.py` 作者作品更新 | 逐个作品 N+1 请求，无批量端点 |
| E2-8 | P3 | `experiments/similarity/embedding.py:46` | `config.yaml` 的 `embedding.base_url` 指向会 302 的地址时 `requests.post` 默认跟随重定向，Bearer key 与请求体被转发到重定向目标 |

已排除（确认无问题）：无 `verify=False` / `_create_unverified_context` / `CERT_NONE`；三处出站都先验 scheme，无 `file://` 直达；作者名/简介/标题在 Jinja 自动转义 + `douyinCaptionHtml`/`escapeHtml` + 全局 nosniff 下无 HTML/JS 注入。

## 三、入站 HTTP 边界与部署面（域 E3）

审查反代部署、TLS、`Host` 头、安全响应头与默认暴露面。

| ID | 级别 | 位置 | 结论 |
|---|---|---|---|
| E3-1 | P1 | `services/auth.py:17,85` + `run.py:637-646` | 首次启动用固定口令 `admin666`，而默认绑定是 `0.0.0.0`：同网段任何人可登录、浏览并删除媒体，还能改口令把主人锁在外面。测试 `tests/test_auth.py:75-109` 明确锁定该行为，属产品契约，不是可静默变更的实现细节 |
| E3-2 | P2 | `auth.py:222` + 限流/审计日志 | 客户端地址一律取 `request.remote_addr`；放在 Nginx/Cloudflare Tunnel 之后时所有请求都是 127.0.0.1，登录限流退化为单桶（一人失败全站被锁）且审计日志无真实来源 |
| E3-3 | P3 | 全局响应头 | 无 `Strict-Transport-Security`；但默认 TLS 是自签证书、需要用户点过警告，加 HSTS 会让该主机后续无法点过 → 不适合默认开启 |
| E3-4 | P3 | `run.py:759-763` | `--dev` 直接 `app.run(host=host)`，而默认 host 是 `0.0.0.0`：Werkzeug 调试器（可在进程内执行代码）被发布到整个网络 |
| E3-5 | P3 | `web/*.py` 绝对 URL 生成 | 未校验 `Host` 头；当前无 `url_for(_external=True)` 用于邮件/回调，影响面为零，登记备查 |
| E3-6 | P3 | `MAX_CONTENT_LENGTH` | 表单/上传上限由各端点自行限制（cookie 1 MB），无全局值；当前无大体积上传端点，登记备查 |

已排除（确认无问题）：`Content-Disposition` 文件名由固定模板生成，无 CRLF 注入面；`X-Frame-Options`/`Referrer-Policy`/`nosniff` 已全局设置；Cookie 已 `HttpOnly`+`SameSite=Lax`。

## 四、前端第三方依赖与外部资源（域 E4）

审查 vendor 资源、模板外部 URL、前端跳转校验与跨账号残留。

| ID | 级别 | 位置 | 结论 |
|---|---|---|---|
| E4-1 | P2 | `tiklocal/auth.py:107-112` | `safe_next` 只拦 `//` 与 scheme，未拦反斜杠；浏览器把 `\` 当 `/`，`/\evil.com` 通过校验后变成 `//evil.com` 跳出站点 |
| E4-2 | P2 | `static/author_page_controller.js:127-136` | 作者候选列表把 sidecar 给的远端 `avatar` 直接塞进 `img.src`：请求带本站 Referer 发给第三方，且加载失败时留一个破图图标，无兜底 |
| E4-3 | P3 | `static/page_transitions.js:44` | `tiklocal:vt-from` 每次 pageswap 都写、pagereveal 只读不清；无匹配 hero 的页面会把上一次的来源继续传给下一次导航 |
| E4-4 | P3 | `static/output.css` | 怀疑与源码不一致 → 复核：本轮未改 `input.css`，产物无漂移 |
| E4-5 | P3 | 模板静态资源 | 怀疑有硬编码 `?v=` → 复核：模板全部走 `static_asset()`（按文件 mtime 打戳），无硬编码 |
| E4-6 | P3 | vendor 资源 | feather/hammer 均为本地文件、无 CDN 引用；无 SRI 需求（同源） |
| E4-7 | P3 | `localStorage` 残留 | `author_page_controller.js:15` 裸 `localStorage.getItem` 已在 backlog（第 23 轮登记），本轮不重复 |

## 五、汇总人自查（域 E5）

| ID | 级别 | 位置 | 结论 |
|---|---|---|---|
| E5-1 | P2 | `experiments/similarity/web.py:86` + `embedding.py:19-23` | `POST /api/library/media-search` 在请求线程里同步调 embedding，`REQUEST_TIMEOUT=180`；embedding 端点挂起时一个请求钉住一个 waitress worker，并发几次全站不可用 |
| E5-2 | P2 | `experiments/similarity/embedding.py:36` | `encode_image` 先整图转 RGB base64 再发，60 MP 图单次约 480 MB 峰值 |
| E5-3 | P3 | `services/captions.py:484` | `requests.post` 只给了总超时，连接阶段与读取阶段共用一个数；DNS 卡住时占用整段预算 |
| E5-4 | P3 | `experiments/similarity/embedding.py:46` | 与 E2-8 重复（重定向跟随） |

## 六、修复取舍

修了 15 条、缓解 1 条、撤销 2 条、驳回 1 条、转 backlog 13 条。取舍理由：

- **E1-1 与第 18 轮 radio_energy 的 P1 同构**（「只认 PATH 里的 ffmpeg」），当时只修了 `/radio` 波形，下载链路没修。这次在 `yt_dlp_command` 加一个参数即收口，属于把同一根因补完。
- **E1-2 用进程内 TTL 缓存**（60 s，键含解析出的路径）而不是改批量入队协议：缓存键带 `resolve_tool` 的结果，用户改了工具路径自然失效；`refresh=True` 留给显式重新探测。
- **E2-1/E2-2/E2-5 合并到一个新模块** `tiklocal/services/net_guard.py`：同一套「拒绝回环/链路本地/元数据地址」规则此前散在 `downloader._is_local_host` 里，头像下载根本没有。现在直链下载与头像下载共用 `is_local_host` 与 `build_guarded_opener()`（逐跳校验重定向），并在请求发出前先判一次。
- **E3-1 只做缓解、不改契约**：默认口令被 `tests/test_auth.py` 锁定，改随机口令属产品决策（会让每个老用户的首次启动行为变化）。本轮只在非回环绑定且仍用默认口令时打印警告，并把「首次启动随机口令」写入 backlog 待决策。
- **E3-3 HSTS 驳回**：默认自签证书 + 点过警告的流程与 HSTS 冲突，开了会让用户永久无法访问自己的服务。
- **E2-8 驳回**：`embedding.base_url` 是用户自己在 `config.yaml` 里配的端点，与已驳回的「yt-dlp 重定向内网」同类；真正要防的是「响应内容决定 URL」的那两条，已修。
- **E1-4 未修**：两种顺序都有反例（cp936 控制台 vs UTF-8 子进程），正确修法是解出的路径不存在时换编码重试，属结构性改动，转 backlog。

## 七、修复实现要点

- `tiklocal/services/net_guard.py`（新增）：`is_local_host(url)`（解析主机名并检查所有 A/AAAA，回环/链路本地/未指定/保留地址一律拒绝；私有 LAN 放行，指向 NAS 是合法用法）+ `GuardedRedirectHandler.redirect_request`（在发出跳转请求前判目标）+ `build_guarded_opener()`。
- `downloader.py`：`DIRECT_DOWNLOAD_MAX_BYTES = 2 GiB`，`Content-Length` 超限直接拒、流式累计超限即删 `.part` 并返回错误；`_direct_fetch` 改为先 `_is_local_host(url)` 再请求，并用 `_DIRECT_OPENER`；`_terminate_process` 在 Windows 上**先** `taskkill /T /F` 再 terminate/wait/kill；`NO_WINDOW_KWARGS` 用于 Popen/check_output/taskkill；`yt_dlp_command(..., ffmpeg_path=resolve_tool('ffmpeg')[0])`。
- `author_profiles.py`：`download_avatar` 前置 `is_local_host(url)`、跟随重定向后用 `geturl()` 复核终态、要求 `Content-Type` 以 `image/` 开头；opener 为 `build_guarded_opener()`。
- `tiktok_downloader.py`：`MAX_RESPONSE_BYTES = 8 MiB`，分块读到上限即报错；总预算 `max(budget,1)*4` 秒，超时抛 `TikTokDownloaderError`。
- `auth.py`：`safe_next` 先把 `\` 归一为 `/`，再拒 `//`、CR/LF/Tab 与带 scheme/netloc 的值，返回归一化结果。
- `run.py`：`is_loopback_bind()`；`--dev` 在非回环绑定时改为绑 `127.0.0.1` 并打印一行说明；默认口令 + 非回环绑定时打印安全警告。
- `page_transitions.js`：读取 `tiklocal:vt-from` 后立即 `removeItem`。
- `author_page_controller.js`：候选头像加 `referrerPolicy='no-referrer'` 与一次性 `error` 兜底（换 `default-avatar.jpg` 并加 `is-empty`），非 http(s) 一律不设 `src`。
- `embedding.py`：`embed_text` 走 `SEARCH_TIMEOUT=15`；`encode_image` 先 `draft('RGB', (1280,1280))` 再编码。
- `captions.py`：`timeout=(5, 30)` 区分连接与读取阶段。

## 八、验证

- 全量 `.\\.venv\\Scripts\\python.exe -m pytest -q`：**602 passed, 8 skipped**（与第 25 轮基线一致）。
- 定向：`tests/test_download.py`、`test_download_urls.py`、`test_download_tools.py`、`test_downloader_lifecycle.py`、`test_tiktok_downloader.py`、`test_auth.py`、`test_author_page.py`、`test_author_sync.py` 共 **198 passed / 2 skipped**。
- 测试缝调整：`tests/test_tiktok_downloader.py` 的 `FakeResponse.read` 接受 `size` 参数（分块读）；媒体下载改由 `_FakeOpener` 替换 `downloader._DIRECT_OPENER`（每次 `open` 返回新响应，两张图不会共用一条流）。公开行为未变。

## 九、覆盖率缺口

- 前端改动（`page_transitions.js`、`author_page_controller.js`）无 pytest 覆盖，只做了静态复核。
- `net_guard` 的重定向拒绝只在 `test_client` 下手测过「终态 URL 判定」，未构造真实 302 链。
- `--dev` 改绑 `127.0.0.1` 与默认口令警告只做了一次手动启动观察。
- 环境注记：本机 `%TEMP%` 在当前沙箱下不可写，`tempfile.gettempdir()` 会回退到进程 cwd，因此跑一次测试会在仓库根留下若干零字节 `tmpXXXXXXXX` **目录**（`mkdtemp()` 产物）。这是环境行为，不是产品缺陷，但排查「工作区为什么变脏」时别误判成泄漏。

## 十、转 backlog

13 条，见 `docs/audit/backlog.md` 的「第 26 轮新增」：E3-1（随机默认口令，待产品决策）、E1-4（输出编码判定）、E3-2（反代头/ProxyFix）、E2-4（作者同步批量上限）、E1-6（版本门禁）、E1-7（长路径）、E2-6（DNS rebinding）、E2-7（N+1）、E3-4（`--dev` 强制忽略 `--host`）、E3-5（Host 头）、E3-6（全局上传上限）、E4-6（vendor 版本登记）、E4-7（裸 localStorage，沿用既有条目）。HSTS（E3-3）与 embedding 重定向（E2-8）为驳回项，在 `round-26.md` 第六节与 backlog 末尾留档。

## 十一、下一轮建议

视角选**「数据生命周期与删除语义」**：软删除/回收站/缩略图缓存/来源映射/活动事件在「删除—恢复—再删除」与「改名—移动」交叉时的语义，是本项目唯一还没有系统审过的方向；同时它天然覆盖前几轮反复出现的「孤儿行/孤儿文件」类问题。
