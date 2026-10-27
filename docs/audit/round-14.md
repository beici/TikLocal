# 第 14 轮审查报告

审查域：i18n 落地核查、错误处理与 HTTP 语义、数据库迁移/备份恢复、媒体解码与缩略图管线、前端状态机回归。
派单方式：5 个复用只读 subagent（`audit-core-routes`、`audit-frontend-shell`、`audit-data-layer`、`audit-download-pipeline`、`audit-web-routes`），全部要求「只读 + 证据（文件:行号、逐字代码、复现、影响）+ 分级」，禁止报风格与重构建议。

确认 13 条（P2×3 / P3×10），全部修复，无 backlog 新增。

## 一、请求边界与错误面

| # | 级别 | 问题 | 位置 | 处置 |
|---|------|------|------|------|
| 1 | P2 | `POST /api/download/urls` 缺 `action` 时默认按 `replace` 处理：任何忘记字段的调用都会清空整个队列，还回 200 `success: true` | `tiklocal/web/downloads.py:138-142` | 缺 `action` → 400 + `download.urls.error_missing_action`；`replace` 且既无文本也无线索时同样 400 |
| 2 | P2 | 队列请求全部被拒仍回 200：`queued=0` 的失败批次看起来像「已开始」 | `tiklocal/web/downloads.py` queue 分支 | `if not queued and failed` → 400 + `download.urls.queue_rejected`，响应体带 `failed`/`items` 明细 |
| 3 | P3 | 超大请求体由 Werkzeug 回英文 HTML 页，上传前端 `response.json()` 直接抛错，UI 只剩「上传失败」 | `tiklocal/app.py` 413 handler | 新增 `@app.errorhandler(413)`，按 `MAX_CONTENT_LENGTH` 生成 `common.error.too_large`（zh「文件太大（上限 8 MB）。」） |
| 4 | P3 | `/api/*` 的 404/405 走默认 HTML 错误页，前端解析失败 | `tiklocal/app.py::_api_error` | `/api/` 前缀统一返回 `{success: false, error}` JSON，404/405/413 共用同一出口 |
| 5 | P3 | `author.py` 的 502/400 错误信封缺 `success` 字段，与其它接口不一致 | `tiklocal/web/author.py`（4 处错误分支） | 统一 `{'success': False, 'ok': False, 'error': ...}`；测试同步更新 |

## 二、数据层损坏的早发现

| # | 级别 | 问题 | 位置 | 处置 |
|---|------|------|------|------|
| 6 | P2 | 数据页中段损坏漏网：`migrate()` 只读 `schema_migrations`（页 1-2），20k 行库覆盖 page 21..60 后 `create_app()` 在首次索引同步时才炸，此时应用已开始服务 | `tiklocal/app.py:147-152`、`tiklocal/services/database.py` | `_open_database()` 里 `migrate()` 后执行 `PRAGMA quick_check`（毫秒级），非 `ok` 行即 `sqlite3.DatabaseError` → 沿用既有的隔离（改名 `*.corrupt` + 重建）路径 |
| 7 | P3 | 数据库不可用时 `/settings/` 直接 500，页面无任何解释 | `tiklocal/web/settings.py` | 捕获 `sqlite3.DatabaseError`/`OSError` → 503 + `settings.error.database_unavailable` |

## 三、媒体与缩略图

| # | 级别 | 问题 | 位置 | 处置 |
|---|------|------|------|------|
| 8 | P3 | EXIF 旋转照片入库宽高是旋转前的：竖拍照片在瀑布流被横向压扁，缩略图却是正的 | `tiklocal/services/media_info.py` | 先 `ImageOps.exif_transpose()` 再取 `size`，与缩略图管线的方向处理一致 |
| 9 | P3 | 缩略图缓存键不含渲染版本：改了渲染逻辑（尺寸/方向/质量）旧缓存永不失效 | `tiklocal/services/thumbnail.py` | 键前缀加 `THUMB_VERSION = 2`；`_GENERATE_SLOTS` 改为按 CPU 动态（2-4）；同输出路径 single-flight 去重 |

## 四、i18n 与文案

| # | 级别 | 问题 | 位置 | 处置 |
|---|------|------|------|------|
| 10 | P3 | `/experiments/similarity` 整页英文（服务/模型/API key/进度/分页/按钮） | `tiklocal/templates/experiments/similarity.html` | 全部改 `t()`，新增 `similarity.*` 20 余键（zh/en 成对） |
| 11 | P3 | 电台配对页设备状态（已配对/已过期/未知）直出英文 | `tiklocal/radio_client.py`、`tiklocal/i18n_pages_radio.py` | 改为 `t('radio.device.*')` 键，客户端只负责状态码 |
| 12 | P3 | 时间线「N 项」固定用 `en` 数字格式（中文界面显示 `1,234`） | `tiklocal/static/library_timeline_controller.js:38` | 改 `uiLocale()`（跟随 `<html lang>`），中文走 `zh-CN` 格式 |

## 五、可达性

| # | 级别 | 问题 | 位置 | 处置 |
|---|------|------|------|------|
| 13 | P3 | 下载页底部动作条按钮/把手在粗指针设备上只有 36px 高（< 44px 触控目标） | `tiklocal/static/download.css` | `@media (pointer: coarse)` 下 `.sheet-handle`/`.sheet-action` `min-height: 44px` |

## 检索与取舍

- 复核「数组体 400 是否算破坏兼容」：唯一调用方 `download_page_controller.js:788-889` 始终显式发送 `action`（`add`/`remove`/`clear`），400 只影响手写脚本与旧测试；保留 400，并同步更新 `tests/test_request_boundaries.py`（数组体不再等价于「清空」）。
- 复核「`quick_check` 是否拖慢启动」：本地 20k 行索引实测 < 20 ms，且只在启动时跑一次；相比「启动后第一次同步才崩」值得。
- 未采纳：把 `quick_check` 做成每日后台任务（无收益，启动时已覆盖）；把 EXIF 方向写回文件（会改用户原文件）。

## 验证

- 语法：`node --check` 覆盖 `library_timeline_controller.js`、`radio_controller.js`；`py_compile` 覆盖 12 个 Python 文件。
- 定向测试：`tests/test_author_page.py`（18）、`tests/test_request_boundaries.py`、`tests/test_i18n.py`、`tests/test_round14_regressions.py`（3）全过。
- 全量套件：见 `README.md` 第 14 轮基线行。
- 实机：重启服务后 `/api/collections` 无 `name` → 400、`/api/download/urls` 空体 → 400、13 MB 上传 → 413 中文、`/api/download/jobs/clear` → 405 + `Allow` 头、`/api/library/sync` 持锁 → 409、`/api/cache/clear` 500 不再回显服务器路径。
