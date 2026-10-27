# 第 20 轮审查报告（流媒体边界 / 设备令牌 / 下载状态机 / 交互可达性 / 媒体缓存）

日期：2026-10-27
起点基线：484P/6S/0F（第 19 轮结束后）
审查域：播放与流媒体安全（core-routes）、下载任务状态机与配置生效性（web-routes）、键盘可达性与状态一致性（frontend-shell）、缩略图缓存与索引一致性（download-pipeline）、跨标签/幂等/数据层并发（data-layer）

> 说明：5 个域中 4 个交付报告，共 10 条：P2×7 / P3×3，全部已复现并修复；另完成第 19 轮遗留 P1（首次索引 EXIF 串行）的并行化。**data-layer 域的 subagent 中途失败、未留报告**，该域的跨标签广播、下载 URL 幂等、数据层并发本轮未审（见 §八）。

## 一、播放与流媒体边界（1×P2）

| # | 级别 | 问题 | 位置 | 处置 |
|---|------|------|------|------|
| 1 | P2 | 206 响应把不可 seek 的 `_BoundedReader` 交给 `wsgi.file_wrapper`，waitress 的 `prepare()` 返回 0，file_wrapper 快路径被整体旁路：慢客户端仍会把 worker 钉到 `channel_timeout`（`threads=8`，8 个暂停的播放器就让 UI 无响应），而 `:338-346` docstring 声称已修好 | `tiklocal/web/media.py:367-372`（构造）、`:61-87`（`_BoundedReader` 无 `seekable/tell/seek`） | 已修：有 file_wrapper 时把真实文件句柄 `seek(start)` 后交给 wrapper，`Content-Length` 已在手写，waitress 的 `prepare(cl)` 用 `min(size-start, cl)=cl` 正确截断；无 wrapper（测试客户端/其他 WSGI 服务器）才回退 `_BoundedReader` |

## 二、设备令牌生命周期（1×P3）

| # | 级别 | 问题 | 位置 | 处置 |
|---|------|------|------|------|
| 2 | P3 | 设备令牌永不过期、无数量上限，改密码后旧条目永久残留：丢失的手机/泄漏的配对码截图一直有效，`device_auth.json` 每次请求全量读取且单调增长 | `tiklocal/services/device_auth.py:36-44`（issue 不写过期）、`:54-57`（verify 只比 revision+hash） | 已修：`DEVICE_TOKEN_TTL_DAYS = 180`；issue 写 `expires_at` 并顺手丢弃已过期行；verify 拒绝过期；旧文件无 `expires_at` 时按 `created_at + TTL` 回退（升级不立刻踢掉已配对设备）；`list_devices` 增加 `expires_at` / `expired` |

## 三、下载任务状态机与配置生效性（1×P2 + 1×P3）

| # | 级别 | 问题 | 位置 | 处置 |
|---|------|------|------|------|
| 3 | P2 | `download-urls.txt` 被记事本存成 GBK 后，`lines()` 抛 `UnicodeDecodeError`（`ValueError`，两个 `except` 都不接）→ `GET /api/download/urls` 500、`/export` 500、`/queue` 无 try 直接 500；`add/remove/replace` 退化成 400 通用文案，用户只能「清空」恢复 | `tiklocal/services/url_list.py:102-110`；暴露点 `tiklocal/web/downloads.py:140`、`:192`、`:237` | 已修：本地读取改 `errors='replace'`（与 `pull()` 的容错对齐）；`/queue` 补 `except OSError → 503`，与 GET/export 一致 |
| 4 | P3 | 取消/重启写入的 `error_message` 是硬编码英文（`"Canceled."`、`"The job was interrupted by a service restart."`），原样显示在中文界面；同文件其余 20+ 处已用 `t()` | `tiklocal/services/downloader.py:475`、`:864`、`:1185`、`:1253`、`:1362`、`:1392` | 已修：全部改走 `translate(locale, ...)`，中英键各补 2 条（`tiklocal/i18n.py`）。**注意**：这些消息产生在 worker 线程，那里没有 request context，直接用 `t()` 会永远回落到中文默认值（英文界面反而变中文）。所以 `enqueue()` 在请求线程里把 `current_locale()` 记进 job（`"locale"` 字段，随 `jobs.json` 持久化），终态消息用 `translate(self._job_locale(job_id), ...)`；重启中断那条读持久化 job 里的 locale，老记录缺该字段时回落到默认 locale |

## 四、交互可达性（3×P2）

| # | 级别 | 问题 | 位置 | 处置 |
|---|------|------|------|------|
| 5 | P2 | 库页快速查看器把 Space 全局吞掉：守卫只排除输入框，Tab 到查看器内的关闭/倍速/收藏按钮按 Space 被 `preventDefault`，图片卡上 `togglePlay()` 直接 return → Space 变成死键（第 12 轮在 radio/detail 修过同一漏网点） | `tiklocal/static/library_page_controller.js:939`、`:958-961` | 已修：守卫补 `button, a[href], summary, [role="button"]` |
| 6 | P2 | 「加入合集」弹层关闭态用 `opacity:0` + `pointer-events:none` 隐藏，`display:flex` 仍在：`aria-hidden="true"` 子树里三个控件留在 Tab 序列（实测 `focus()` 后 `activeElement` 进入该子树，axe `aria-hidden-focus`）；打开不移入焦点、关闭不归还 | `tiklocal/static/collection_picker.js:111-116`、`:141-146`；`tiklocal/static/library.css:758-770` | 已修：关闭态加 `visibility:hidden`（连同 `transition`），移出 Tab 序列；`open()` 记录触发元素并 `nameInput.focus()`；`close()` 归还焦点 |
| 7 | P2 | 「整理」弹层声明了 `role="dialog" aria-modal="true"` 却不接管焦点：打开后焦点留在底层多选工具条，读屏用户不知道弹层已出现；关闭后也不回到触发按钮 | `tiklocal/static/library_page_controller.js:1086-1100` | 已修：`openOrganizeSheet` 记录 opener 并聚焦 `#organize-sheet-close`；`closeOrganizeSheet` 归还焦点 |

## 五、缩略图缓存与索引一致性（2×P2 + 1×P3）

| # | 级别 | 问题 | 位置 | 处置 |
|---|------|------|------|------|
| 8 | P2 | legacy 缓存（`sha1(裸相对路径)`，无 root key、无版本）可跨库串图：两个库有同名相对路径时，库 B 会「收养」库 A 的封面并 `replace` 成 B 的正式缓存，永久生效（实测库 A 红图被库 B 当封面：(220,20,20) vs (21,20,220)） | `tiklocal/services/thumbnail.py:150-152`（`_legacy_path_for_key`）、`:165-172`（收养） | 已修：新增 `_claim_legacy_adoption()`，收养前在同目录 `.legacy-root` 记录归属 root key；归属不符时视为未命中并重新生成。单库升级仍照旧收养，不会丢历史缓存 |
| 9 | P2 | `Image.MAX_IMAGE_PIXELS` 被进程级改写 + `finally` 恢复，与并发生成竞态：两线程时 182 MP PNG 抛 `DecompressionBombError` → 间歇空白封面并被 `_mark_failure` 记住 1 小时（网格正好 2–4 路并发） | `tiklocal/services/thumbnail.py:516-518`、`:561-562` | 已修：新增 `_open_image_with_wide_ceiling()`，只在 `Image.open` 的头部解析期间把上限抬到 `MAX_DECODE_PIXELS` 再恢复，并用 `_PIXEL_LIMIT_LOCK` 把这段「保存-抬高-恢复」窗口串行化；昂贵的解码/缩放仍在锁外并行。**没有**改成 `Image.MAX_IMAGE_PIXELS = None`：那会顺带把 `captions.py:425`、`media_info.py:27`、`library_index.py:761` 等所有 Pillow 用户的炸弹保护也取消掉（这些路径先 `convert`/`resize` 再缩小，300 MP 图会直接吃掉约 900 MB）。全局值保持 Pillow 默认（89 MP），`_generate_image` 仍按 `MAX_DECODE_PIXELS`（200 MP）在解码前自行拒绝并 `logger.warning` |
| 10 | P3 | `delete_thumbnail` 只删 `.jpg` 不删 `.fail`：删掉损坏媒体后换上新文件（`copy2`/解压/rsync 保留更旧 mtime），`get_thumbnail` 直接返回 1×1 占位图最长 1 小时，一次生成都不尝试 | `tiklocal/services/thumbnail.py:367-376`；调用点 `tiklocal/web/media.py:203` | 已修：删除预览时同步 unlink 对应 `.fail`（canonical 与 legacy 两种 key 都处理） |

## 六、第 19 轮遗留 P1：首次索引 EXIF 串行（已并行化）

第 19 轮测得冷索引 8.19 s 中 EXIF 同步读取占 6.25 s（76%）。该轮结论是「转 backlog、需后台回填 + 排序语义变更」；本轮做了不改语义的部分：`tiklocal/services/library_index.py` 新增 `CAPTURE_WORKERS = min(8, os.cpu_count() or 4)` 与 `_records_for_paths()`，`_sync_locked` 在 :875 改调它，逐文件 `_record_for_path` 走 `ThreadPoolExecutor.map`（顺序与串行一致，`_record_for_path` 不写共享状态）。

**未复测**：本机 media root 只有 127 个文件（cold 0.09 s / warm 0.04 s），拿不到 19k 文件的前后对比；后台回填 `time_metadata_version=0` 的完整方案仍未做，backlog 条目保留。

## 七、修复实现要点

- `tiklocal/web/media.py`：206 分支 `open(target,'rb') + seek(start)` 交给 `wsgi.file_wrapper`，无 wrapper 时仍用 `_iter_reader(_BoundedReader(...))`。
- `tiklocal/services/device_auth.py`：`DEVICE_TOKEN_TTL_DAYS`、issue 写入 `expires_at` + 过期清理、verify/list 拒绝过期、`_is_expired()` 对旧文件按 `created_at` 回退。
- `tiklocal/services/thumbnail.py`：`_open_image_with_wide_ceiling()` + `_PIXEL_LIMIT_LOCK`（只在 open 期间抬高上限）；`_generate_image` 自行按像素上限拒绝；`_claim_legacy_adoption()` + `.legacy-root`；`delete_thumbnail` 清 `.fail`。
- `tiklocal/services/library_index.py`：`CAPTURE_WORKERS` + `_records_for_paths()`。
- `tiklocal/services/url_list.py` / `tiklocal/web/downloads.py`：`errors='replace'`；`/queue` 读失败返回 503。
- `tiklocal/services/downloader.py` / `tiklocal/i18n.py`：6 处取消/中断文案改 `translate(locale, ...)`，job 记录 enqueue 时的 locale，中英键各 2 条。
- `tiklocal/static/library_page_controller.js`：Space 守卫补可交互元素；整理弹层 opener/焦点归还。
- `tiklocal/static/collection_picker.js` + `tiklocal/static/library.css`：`visibility:hidden`；打开聚焦输入框、关闭归还焦点。

## 八、验证

- 新增 `tests/test_round20_regressions.py`（7 条）：legacy 缓存跨库隔离、`.fail` 随预览删除、设备令牌过期、无 `expires_at` 按年龄回退、GBK URL 列表可读、`/api/download/urls*` 对不可读存储不 500、206 交给 wrapper 的是已 seek 的可 seek 文件。
- `tests/test_thumbnails.py` 新增 2 条：像素上限只在 `Image.open` 期间被抬高（4 线程 + 慢 open 探针，断言并发窗口为 1、结束后全局值复原）、超 `MAX_DECODE_PIXELS` 的图在解码前被拒。
- `tests/test_downloader_lifecycle.py` 新增 1 条：`Accept-Language: en-US` 入队的任务被取消后，`error_message` 是英文 `Canceled.`（证明 locale 确实从请求线程传到了 worker）；同文件的取消断言改为 `t('download.error.canceled')`（随第 4 条一起改）。
- 全量：`pytest -q` = **494 passed / 6 skipped / 0 failed**（234.08 s）。
- `node --check` 通过两个改动过的 controller。

### 真实 waitress 冒烟（本条的验收证据）

单测只能证明「交给 wrapper 的是可 seek 文件」，证明不了「慢客户端不再占用 worker」，所以起真服务器验了一遍：waitress 3.0.2、`threads=8`、`create_server(app, ...)` 与 `tiklocal/run.py:450` 同样的配置，各请求 `Range: bytes=0-<size-1>` 后只读一个 chunk 就停住（模拟暂停的视频标签页），再测一个无关的 `GET /` 延迟。

| 慢读者 | 修复后（seekable 句柄交给 wrapper） | 修复前（隐藏 `wsgi.file_wrapper`，走 `_BoundedReader`） |
|--------|--------------------------------------|--------------------------------------------------------|
| 4 个（6 MB 文件） | 65.2 ms | 48.2 ms |
| 8 个（64 MB 文件） | **4.9 ms** | **6015.0 ms，TimeoutError** |

- 区间正确性：`2-5`→`[2,3,4,5]`、`0-0`→`[0]`、`-4`→`[1020..1023]`（内容按 `i % 251` 造，逐字节比对通过）；`2000-3000`→416 + `bytes */1024`；整文件 200 且 1024 字节。
- 结论：修复前 8 个暂停的播放器就把 8 个 worker 全部钉死，UI 请求 6 s 超时——正是本条要修的现象；修复后同样的 8 个慢读者下 UI 请求 4.9 ms。

## 九、覆盖率缺口

`data-layer` 域（跨标签数据变更广播、下载 URL 幂等、数据层并发）的 subagent 中途 failed、未留报告，本轮没有该域的结论。README 中该域的三条已知项（backlog 第 16 轮：`BroadcastChannel`、URL 幂等、`CollectionStore` 快照）继续留待后续轮次。

## 十、转 backlog

本轮 10 条全部修复，未新增 backlog 条目；第 19 轮 P1（索引 EXIF 后台回填）因本轮只做了并行化、未改排序语义，继续保留。
