# 第 19 轮审查报告（配置面 / 首次索引性能 / 媒体管线 / 设备端 i18n / 数据层并发）

日期：2026-10-27
起点基线：477P/6S/0F（第 18 轮结束后）
审查域：安全与鉴权（core-routes）、配置与设置面（web-routes）、i18n 与首次运行（frontend-shell）、媒体管线性能（download-pipeline）、跨标签与数据层并发（data-layer）

> 说明：5 个域全部在 `312bbe5` 上重新核对，共 15 条：P1×1 / P2×6 / P3×8。修复 12 条，转 backlog 3 条（P1 索引 EXIF、P2 视频封面双次 ffmpeg、P2 空态 CTA）。安全域本轮为全量复查，未发现新的路径穿越 / XSS / CSRF / 鉴权缺陷（只报 2 条非安全项）。

## 一、首次索引性能（1×P1）

| # | 级别 | 问题 | 位置 | 处置 |
|---|------|------|------|------|
| 1 | P1 | 首次索引 19,222 个文件耗时 8.19 s，其中 EXIF 同步读取 6.25 s（76%），无 EXIF 时 1.94 s；暖索引 1.55 s | `tiklocal/services/library_index.py`（`_extract_time_metadata_batch` 在扫描事务内串行读取） | **未修，转 backlog**：修法要把 `captured_at` 改成后台探测回填（扫描先写 `time_metadata_version=0` + mtime 兜底，探测线程补 EXIF 并更新排序键）。这会改变首屏时间线的排序语义（探测完成前按 mtime 排），需要单独一轮设计，不在本轮仓促改 |

## 二、媒体管线（1×P2 已修 + 2×P2 转 backlog + 1×P3 已修）

| # | 级别 | 问题 | 位置 | 处置 |
|---|------|------|------|------|
| 2 | P2 | 超过 179 MP 的图片被 PIL 的 `DecompressionBombError` 拒绝，渲染成 69 B 的 1×1 透明 PNG——网格里就是一块空白，且无任何日志 | `tiklocal/services/thumbnail.py`（`_generate_image` 的 `except Exception: return False`） | 已修：`MAX_DECODE_PIXELS = 200_000_000` 临时抬升 PIL 上限；超过 `REDUCE_ABOVE_PIXELS = 48_000_000` 先 `Image.reduce(factor)` 再 LANCZOS；失败改为 `logger.warning` 带异常类型 |
| 3 | P2 | 视频封面每张 1.36 s：`_generate` 先起一次 ffmpeg 读 `Duration` 横幅，再起第二次抽帧 | `tiklocal/services/thumbnail.py`（`_probe_duration` + `_extract_frame`） | **未修，转 backlog**：需要把时长持久化进索引（`media_items.duration` 已有列，但扫描未写），否则只能固定时间点抽帧、牺牲封面质量 |
| 4 | P2 | 设置页「扫描」忽略 `unavailable_sources` / `source_errors` / `busy`，离线来源被静默跳过却提示「扫描完成，已索引 N 项」 | `tiklocal/static/settings_page_controller.js`（`/api/library/sync` 回调只读 `data.indexed`） | 已修：分别提示「正在扫描」「部分来源不可用」「扫描完成但 N 个来源不可用」，新增 3 个 i18n key |
| 5 | P3 | 生成失败记忆 6 小时，且成功生成后不清理 `.fail` 标记：一次「文件正在复制」的失败会让占位图挂满 6 小时 | `tiklocal/services/thumbnail.py`（`FAILURE_TTL_SEC`、`_mark_failure`） | 已修：TTL 降到 1 小时；`generate_thumbnail` 成功发布后调用 `_clear_failure()` 删除标记 |

## 三、配置与设置面（3×P2 + 3×P3）

| # | 级别 | 问题 | 位置 | 处置 |
|---|------|------|------|------|
| 6 | P2 | `download_source` 配成不存在的 id 时静默回退到第一个来源，下载落到意料之外的目录 | `tiklocal/app.py`（`download_source` 解析） | 已修：`logger.warning` 列出已配置的 id 后再回退；`build_download_config_payload` 同时返回生效的 `output_source_id` / `output_source_path` |
| 7 | P2 | 空态「添加媒体来源」CTA 跳到 `/settings/`，但设置页没有任何来源管理入口，用户点进去无路可走 | `tiklocal/static/home_page_controller.js`、`tiklocal/templates/settings.html` | **未修，转 backlog**：需要新增只读来源列表 + 修改指引（来源由 CLI `--source` / config 决定，不能在网页里改） |
| 8 | P3 | 同一口令存在两个独立限流桶（`/login` 与 `/api/v1/pair`），5 分钟窗口内可猜 10 次 | `tiklocal/auth.py:49`、`tiklocal/radio_client.py:26` | 已修：`configure_auth` 把 limiter 放进 `app.extensions['login_limiter']`，配对端点复用同一实例 |
| 9 | P3 | `GET /api/download/urls` 与 `/export` 在 URL 列表文件不可读时抛 `OSError` → 500 | `tiklocal/web/downloads.py` | 已修：两处捕获 `OSError` 返回 503 |
| 10 | P3 | `url_list.remove()` 的读-改-写不在同一把锁里，两个标签同时删除会丢一条 | `tiklocal/services/url_list.py` | 已修：整个 read-modify-write 放进 `self._lock` |
| 11 | P3 | `TIKLOCAL_NAME` 环境变量被 config 里的 `name` 覆盖（优先级与文档相反） | `tiklocal/run.py`（`INSTANCE_NAME` 解析） | 已修：优先级改为 CLI `--name` > `TIKLOCAL_NAME` > config |
| 12 | P3 | 重复的媒体来源 id 只保留第一条，但启动横幅把两条都打印出来 | `tiklocal/run.py`、`tiklocal/web/library.py` | 已修：横幅对重复项打印 `ignored (duplicate id)`；`build_media_sources` 记 warning |
| 13 | P3 | TikTok 配置校验失败返回通用「无效链接」，且 `base_url="http://"` 会被 `rstrip('/')` 变成 `"http:"` 通过校验 | `tiklocal/web/downloads.py`、`tiklocal/services/tiktok_downloader.py` | 已修：路由直接返回 `validate_download_config` 的字段级文案；`base_url` 先 rstrip 再校验 host 非空 |
| 14 | P3 | 相对路径的 `TIKLOCAL_INSTANCE` / `cookie_dir` 按进程 CWD 解析，换目录启动就读到另一份数据 | `tiklocal/paths.py`、`tiklocal/services/downloader.py` | 已修：显式按 CWD 解析为绝对路径；`TIKLOCAL_INSTANCE` 首次解析时记一条 warning |

## 四、设备端 i18n（1×P2）

| # | 级别 | 问题 | 位置 | 处置 |
|---|------|------|------|------|
| 15 | P2 | 设备端 API 有 9 处 `t()` 指向不存在的 key，`message` 字段原样返回 `radio.error.audio_not_found` 这类 key，中英文都一样 | `tiklocal/radio_client.py:96,235,250,257,266,268,272,281,283` | 已修：4 处 `audio_not_found` 映射到已存在的 `radio.audio_not_found`，`invalid_event` 映射到 `radio.invalid_feedback`；新增 `radio.error.invalid_password` / `invalid_favorite` / `favorite_unavailable`（中英各 3 条）；`tests/test_i18n.py` 的 key 扫描从「模板 + static/*.js」扩到 `tiklocal/**/*.py`，这类缺失以后会被测试拦住 |

## 五、修复实现要点

- `tiklocal/services/thumbnail.py`：`import logging` + `logger`；`MAX_DECODE_PIXELS` / `REDUCE_ABOVE_PIXELS` 常量；`_generate_image` 用 `try/finally` 恢复 `Image.MAX_IMAGE_PIXELS`，按 `math.isqrt(pixels // REDUCE_ABOVE_PIXELS) + 1` 取整数缩放因子；新增 `_clear_failure()`，`FAILURE_TTL_SEC = 3600`。
- `tiklocal/auth.py` / `tiklocal/radio_client.py`：共享 `app.extensions['login_limiter']`。
- `tiklocal/paths.py`：`get_data_dir()` 相对路径按 CWD `resolve()` 并 warning 一次。
- `tiklocal/services/downloader.py`：`_expand_cookie_dir()` 绝对化。
- `tiklocal/services/tiktok_downloader.py`：`base_url` 先 `rstrip('/')`，再要求 `host` 非空。
- `tiklocal/web/downloads.py`：URL 列表读失败 503；TikTok 配置 400 直接返回字段级文案。
- `tiklocal/static/settings_page_controller.js`：扫描结果分三种 toast。
- `tests/test_round19_regressions.py`（新文件，5 条）：URL 列表 503、TikTok 字段级 400、scheme-only base_url、配对与登录共享限流、扫描暴露离线来源。
- `tests/test_thumbnails.py`：超大图 reduce 路径 + 成功生成清理 `.fail`。
- `tests/test_i18n.py`：key 扫描覆盖 Python 调用点。

## 六、转 backlog（附不修理由）

1. **P1 首次索引 EXIF 8.19 s**：需要后台探测 + 排序语义变更，单列一轮设计（见 #1）。
2. **P2 视频封面双次 ffmpeg（1.36 s/张）**：需要把时长写进索引，改动扫描写入路径（见 #3）。
3. **P2 空态 CTA 死胡同**：来源由 CLI/config 决定，网页只能做只读展示 + 指引，属新功能（见 #7）。
