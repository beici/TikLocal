# 第 18 轮审查报告（HTTP 语义 / 外部依赖恢复力 / 搜索语义）

日期：2026-10-27
起点基线：469P/6S/0F（第 17 轮结束后）
审查域：HTTP 语义与缓存头、外部依赖（ffmpeg/ffprobe/yt-dlp/sidecar）故障恢复力、搜索与筛选语义

> 说明：本轮派了 5 个域，其中「备份/导出回环保真度」经复核确认**该功能在代码里整体不存在**（无 backup/export/restore 路由与 service），判为 N/A；「首次运行与空态引导」的 agent 未收到本轮任务（其最后交付是第 17 轮），顺延到下一轮重新派发。实际产出 12 条：P1×1 / P2×6 / P3×5，全部修复。

## 一、HTTP 语义与缓存头（3×P2 + 1×P3）

| # | 级别 | 问题 | 位置 | 处置 |
|---|------|------|------|------|
| 1 | P2 | 全站零压缩：带 `Accept-Encoding: gzip` 请求 `/library` 仍是 86 KB 明文（其中约 73 KB 是内联 i18n 目录） | `tiklocal/app.py`（`apply_cache_policy` 从不设 `Content-Encoding`，依赖里也没有压缩中间件） | 已修：新增 `finalize_response` 后置钩子，对文本类响应 gzip 并补 `Vary: Accept-Encoding` |
| 2 | P2 | 页面标了 `Cache-Control: private, no-cache` 却没有验证器：每次导航都重新下载整页 | `tiklocal/app.py` | 已修：HTML 响应按 body 生成 `ETag`，重复访问变 304 |
| 3 | P2 | `206` / `416` 响应不带 `ETag`、`Last-Modified`：播放器拖动后既无法续验也无法协商缓存 | `tiklocal/web/media.py`（Range 分支手工构造响应，绕过了 `send_file` 的头） | 已修：两条分支补验证器，并让 `If-Range` 不匹配时回退整文件 |
| 4 | P3 | `/api/download/jobs/<id>/export` 的 `mimetype='text/plain; charset=utf-8'` 与 Werkzeug 默认 charset 叠加，响应头出现两个 `charset=utf-8` | `tiklocal/web/downloads.py` | 已修：改为 `text/plain`，由框架统一补 charset |

## 二、外部依赖故障恢复力（1×P1 + 2×P2 + 1×P3）

| # | 级别 | 问题 | 位置 | 处置 |
|---|------|------|------|------|
| 5 | P1 | 音量包络只认 PATH 里的裸 `ffmpeg`：默认安装没有系统 ffmpeg 时 `/radio` 波形恒为空，且每次请求白起 3 次子进程 | `tiklocal/services/radio_energy.py`（`shutil.which('ffmpeg')` 决定是否解码） | 已修：新增 `_ffmpeg_argv()`，先走 `download_tools.resolve_tool('ffmpeg')`（能找到随包 imageio-ffmpeg），再退回 PATH |
| 6 | P2 | 解码外部进程输出没有硬截止：解复用器卡在坏容器/死共享上时永久占用一个 waitress worker | `tiklocal/services/radio_energy.py`（`stdout.read()` 无期限阻塞） | 已修：读循环放辅助线程，`DECODE_TIMEOUT_SEC = 60` 后放弃并杀子进程 |
| 7 | P2 | Windows 上只 `terminate()` 父进程：yt-dlp 的 ffmpeg 合并子进程在取消后继续往任务临时目录写 | `tiklocal/services/downloader.py` | 已修：新增 `_kill_process_tree_windows()`（`taskkill /T /F`），POSIX 仍用 `killpg` |
| 8 | P3 | 索引登记失败时整任务判 `failed` 并丢弃已落盘文件，重试会重复下载出 `clip (1).mp4` | `tiklocal/services/downloader.py`（`failure_stage == 'index'` 分支） | 已修：保留文件并标 `partial`，错误文案说明「已下载但登记失败」 |
| 9 | P2 | `HEAD /api/feed/mix` 会完整构建候选池并序列化 JSON（8k 库实测 139 ms）只为返回空 body | `tiklocal/web/flow.py` | 已修：`request.method == 'HEAD'` 时直接返回空 `Response(200)` |

## 三、搜索与筛选语义（1×P2 + 2×P3）

| # | 级别 | 问题 | 位置 | 处置 |
|---|------|------|------|------|
| 10 | P2 | 搜索只折叠 ASCII 大小写：`q=Ärger` 命中 1 条、`q=ärger` 命中 0 条（SQLite `LIKE` 只对 ASCII 生效） | `tiklocal/services/library_index.py`（`filename LIKE ? OR rel_path LIKE ?`） | 已修：入库时写入归一化 `search_text` 列，查询用同一归一化后比较 |
| 11 | P3 | 不做 Unicode 归一：NFC 与 NFD 形式的同名文件互不命中（macOS 拷入的文件常见） | 同上 | 已修：归一化用 `NFKC` + `casefold` |
| 12 | P3 | 不做空白折叠：`hello  world`（双空格）搜不到 `hello world.jpg` | 同上 | 已修：归一化把连续空白压成单空格 |

## 四、修复实现要点

- `tiklocal/app.py`：`import gzip` / `hashlib`；`COMPRESSIBLE_MIMETYPES`（html/css/js/json/svg/xml/txt）与 `COMPRESS_MIN_BYTES = 1024`；`finalize_response` 跳过 `direct_passthrough`、已压缩、非 200、`Range` 请求，HTML 分支先补 `ETag`（body 摘要）再压缩。
- `tiklocal/web/media.py`：`_range_response()` 给 206/416 补 `ETag`（`f'{st_mtime}-{st_size}-{check}'`）与 `Last-Modified`；`If-Range` 不匹配时交回 `send_file` 走整文件。
- `tiklocal/web/flow.py`：`api_feed_mix()` 开头 HEAD 短路。
- `tiklocal/web/downloads.py`：导出响应 `mimetype='text/plain'`。
- `tiklocal/services/radio_energy.py`：`_ffmpeg_argv()`、`DECODE_TIMEOUT_SEC`、`_ffmpeg_powers()` 改为辅助线程 + 超时 + `_kill_process()`。
- `tiklocal/services/downloader.py`：`_kill_process_tree_windows()`；`failure_stage == 'index'` 且已有产物时置 `partial`。
- `tiklocal/services/search_text.py`（新文件）：`normalize()` = `NFKC` + `casefold` + 空白折叠。
- `tiklocal/services/database.py`：迁移 15 新增 `media_items.search_text` 并回填存量行。
- `tiklocal/services/library_index.py`：两条 upsert 写 `search_text`（`ON CONFLICT` 同步更新），`_filters` 改用 `search_text LIKE ?`。
- 附带加固：`tiklocal/services/json_storage.py` 的写重试改为**指数退避 + 抖动**（`_REPLACE_ATTEMPTS = 8`、上限 0.4 s）并给临时文件写入也加 3 次重试——本轮全量套件曾暴露 6 线程并发保存同一 store 时 5×30 ms 的平坦预算会被耗尽。

## 五、验证

- `python -m py_compile`：`app.py`、`flow.py`、`media.py`、`downloads.py`、`radio_energy.py`、`downloader.py`、`database.py`、`library_index.py`、`search_text.py`、`json_storage.py` 全过。
- 新增 `tests/test_round18_regressions.py`（8 个用例）：页面 gzip + `Vary`、HTML 带 `ETag` 且 `If-None-Match` 得 304、206/416 与 200 的验证器逐字相等、`HEAD /api/feed/mix` 空 body、`_ffmpeg_argv()` 优先随包二进制、停滞解码在超时后放弃并杀进程、取消下载调用 `taskkill /T /F`、索引失败置 `partial` 并保留文件。
- 全量 `pytest -q` = **477 passed / 6 skipped / 0 failed**（157.64s；本轮新增 8 个用例，起点 469P）。

### 现场复验（真实 waitress + 真实媒体库，13:47 重启后）

| 项 | 结果 |
|----|------|
| `GET /library`（带 `Accept-Encoding: gzip`） | `200`，`Content-Encoding: gzip`，139,135 → **24,144 B**（5.8×），`Vary: Accept-Encoding, Cookie`，`Etag: "90c2884f12368f7e"` |
| 带 `If-None-Match` 重放 | `304 Not Modified` |
| `HEAD /api/feed/mix` | `200`，`Content-Length: 0`，**90 ms**（同条件 `GET` 174 ms / 43,984 B） |
| `Range: bytes=0-1023` | `206`，`Content-Range: bytes 0-1023/1829304`，带 `Etag` 与 `Last-Modified` |
| `Range: bytes=99999999-` | `416`，`Content-Range: bytes */1829304`，同样带验证器 |
| `Range` + 过期 `If-Range` | 回退 `200` 整文件（1,829,304 B），不再返回残缺 206 |
| 启动后 `server.err.log` | 仅 1 行启动横幅，索引线程无异常 |

## 六、转 backlog

1. 重叠源归属语义（两个源指向同一目录时后配置者整体消失）：第 15 轮已加 `overlap_warnings` 告警收口，彻底解决需要「源优先级」配置项。
2. 备份/导出/恢复功能整体缺失：属功能缺口而非缺陷，需单独立项（含导出文件敏感字段审查）。
3. `tiklocal/web/settings.py` 的 `safe()` 把统计异常吞成 0（P3）：当前以日志留痕替代，若要做「统计不可用」提示需前端配合。
4. 首次运行/空态引导复查：本轮未实际执行，顺延到下一轮。
