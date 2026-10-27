# 第 15 轮审查报告

审查基：依赖安全与版本约束、静态资源与缓存头、时间/时区/时间边界、搜索筛选排序语义、CLI/打包/部署。
派单方式：5 个复用只读 subagent（`audit-frontend-shell`、`audit-web-routes`、`audit-data-layer`、`audit-core-routes`、`audit-download-pipeline`），全部要求「只读 + 证据（文件:行号、逐字代码、复现、影响）+ 分级」，禁止报风格与重构建议。

确认 12 条（P1×1 / P2×4 / P3×7），修复 10 条，转 backlog 2 条。

## 一、依赖安全

| # | 级别 | 问题 | 位置 | 处置 |
|---|------|------|------|------|
| 1 | P1 | Pillow 12.3.0 命中 41 条 2026 年公告，而 `^12.3.0` 的 caret 上限把唯一修复版 13.0.0 挡在门外 | `pyproject.toml:21` | 约束改为 `>=12.3.0,<14`：不再阻塞 13.0.0。本机 Python 3.14 尚无 13.0.0 轮子，装 13 会退化为源码编译，故本环境仍跑 12.3.0；升级路径记入 backlog |

## 二、HTTP 缓存与 Range（媒体服务）

| # | 级别 | 问题 | 位置 | 处置 |
|---|------|------|------|------|
| 2 | P2 | `/media` 的 Range 走 Werkzeug `RangeWrapper`（普通生成器），由 waitress 在请求线程里拷贝：暂停的视频标签页 / 掉线的手机把 worker 一直挂到 `channel_timeout`，8 个就能饿死整个线程池 | `tiklocal/web/media.py::serve_media` | 单区间 206 改由 `wsgi.file_wrapper` 下发（waitress 用 `wsgi.file_wrapper` 把字节搬离请求线程）；无 wrapper 时退回分块生成器。两条路径都用 `_BoundedReader` 限长，避免把剩余文件当 206 体发出去 |
| 3 | P3 | `/media` 忽略 `If-None-Match`：带 Range 的重复请求永远 200/206，缓存验证失效 | 同上 | 带 `If-None-Match` / `If-Modified-Since` / `If-Range` 的请求不再走快路径，交回 `send_file(conditional=True)`，304 与 `If-Range` 语义保持 Werkzeug 原生行为 |
| 4 | P3 | 越界 Range（`bytes=999999-`）被当成「忽略」返回整份 200，播放器把失败的 seek 当成功；多区间 Range 同样返回 200 | `tiklocal/web/media.py::_byte_range` | 起点越界 → 416 + `Content-Range: bytes */<size>`；多区间/语法错误按 RFC 9110 忽略 Range、返回 200 全量（原本 werkzeug 一律 416，看起来像文件丢失） |
| 5 | P3 | `/media/<不存在>` 返回硬编码英文 `File not found` | `tiklocal/web/media.py::serve_media` | 改 `t('image.error.file_not_found')` |

## 三、时间边界

| # | 级别 | 问题 | 位置 | 处置 |
|---|------|------|------|------|
| 6 | P2 | 一个 mtime 早于 1970 / 晚于 9999 的文件让整个媒体源构建记录失败：`datetime.fromtimestamp` 在 Windows 抛 `OSError`，被 `_sync_locked` 的 `except OSError` 吞成「源不可用」——新库整库空、旧库索引永久冻结，而 `/api/library/sync` 仍回 `success: true`，界面只说「扫描完成，0 个」 | `tiklocal/services/library_index.py::discover_capture_time` | 新增 `_safe_mtime_datetime()`：越界时间戳退化为 1970-01-01，不再抛异常；`fromtimestamp` 全部走这条路径 |
| 7 | P3 | 未来日期不 clamp：文件名或 EXIF 声称 2031 的文件把「2031」永久钉在时间线与年份导航最上方，且 `capture_month` 写入后不会自愈 | `tiklocal/services/library_index.py::_capture_payload` | 新增 `_clamp_future()`：`captured_at > now + 1 天` 视为不可信，降级为文件系统时间（`filesystem_mtime`/`fallback`） |
| 8 | P3 | `datetime.utcnow()` 两处未改（已废弃，3.15 移除），全量测试 581 条 DeprecationWarning 全指向它们 | `tiklocal/services/collections.py:29`、`tiklocal/services/download_sources.py:18` | 改 `datetime.now(datetime.timezone.utc)` 并保留原 `...Z` 线上格式 |

## 四、搜索与源重叠

| # | 级别 | 问题 | 位置 | 处置 |
|---|------|------|------|------|
| 9 | P2 | 搜索只做 ASCII 大小写折叠：`q=ärger` 搜不到 `Ärger.jpg`（SQLite `LIKE` 只折叠 ASCII），非 ASCII 文件名按小写输入静默返回「无结果」 | `tiklocal/services/library_index.py:673` | 需要 `search_key` 列（NFC + casefold + 空白折叠）才能根治，属 schema 变更 + 回填，本轮转 backlog；中文（CJK）不受影响 |
| 10 | P3 | 搜索不做 Unicode 归一化（NFC/NFD 互不匹配）与空白折叠（`hello  world` 双空格 0 结果） | `tiklocal/services/library_index.py:671` | 与第 9 条同一修法，一并转 backlog |
| 11 | P3 | 两个源指向重叠目录时，后配置的源整体消失（`ref_for_path` 先匹配先赢），无告警、`?blog=<id>` 恒 0、chip 不渲染 | `tiklocal/services/library.py::LibraryService` | 新增 `_warn_overlapping_sources()`：启动时逐个源比对根目录，被吞掉的源打 `logger.warning`，点名双方 id 并提示改为非重叠目录 |

## 五、CLI / 部署

| # | 级别 | 问题 | 位置 | 处置 |
|---|------|------|------|------|
| 12 | P2 | CLI 指定不存在的嵌套媒体目录时服务照常启动，媒体根不可用却打印 `Serving on ...`，用户只看到空库 | `tiklocal/app.py::create_app` | 默认媒体根不存在时 `mkdir(parents=True, exist_ok=True)` 并记日志；失败才告警（配置里的其它源保持原样，不擅自建目录） |

## 六、验证

- 新增 `tests/test_round15_regressions.py`（5 个用例，全过）：
  - `test_pre_epoch_mtime_does_not_freeze_the_whole_source`：`os.utime(ancient, (-1.0, -1.0))` 后 `/api/library/sync` 仍 `indexed >= 3`、`unavailable_sources` 为空、`ancient.mp4` 出现在列表里；
  - `test_future_filename_date_falls_back_to_the_filesystem`：`2031-05-05 …future.mp4` 的 `time_source` 为 `filesystem_mtime`；
  - `test_utc_helpers_keep_the_z_suffix_without_utcnow`：两个 store 的时间戳仍以 `Z` 结尾且可解析为 UTC；
  - `test_overlapping_sources_are_named_in_a_warning`：子目录源触发含双方 id 的 warning；
  - `test_missing_nested_media_root_is_created`：`MEDIA_ROOT=a/b/media` 启动后目录存在。
- Range 语义实测（test client，1000 字节文件）：`bytes=2-5` → 206 `bytes 2-5/1000` 且体长 4；`bytes=-10` → 206 `bytes 990-999/1000`；`bytes=999999-` → 416 `bytes */1000`；`bytes=0-1,5-6` → 200 全量；`If-None-Match` → 304。
- Range 语义实测（真实 waitress + 1.45 MB 视频，验证 `wsgi.file_wrapper` 路径）：`bytes=2-5` → 206 体长 4；`bytes=-10` → 206 体长 10；`bytes=1520545-` → 416 `bytes */1520535`；`bytes=0-1,5-6` → 200 全量 1520535；`If-None-Match` / `If-Modified-Since` → 304；普通 GET 仍带 `ETag` 与 `Last-Modified`；不存在的媒体 → 404 且文案已本地化。
- `tests/test_web_runtime.py`（含 `test_browser_cache_policy_and_media_range`）、`tests/test_thumbnails.py`、`tests/test_round14_regressions.py` 全绿。
- 全量 `pytest -q` = **454 passed / 6 skipped / 0 failed**（172.04 s，修 Range 前一轮；Range 修完后又跑一次见提交记录）。

## 七、转 backlog

1. **搜索归一化（第 9/10 条）**：需要 `media_items.search_key` 列（`unicodedata.normalize('NFC', name).casefold()` + 空白折叠）+ 迁移回填 + 查询改写；影响面含 `_filters()`、`page()` 与 `q` 长度截断，单独一轮做。
2. **Pillow 13.0.0 升级**：约束已放开（`>=12.3.0,<14`），等 cp314 轮子发布后 `poetry update pillow` 并复跑 `tests/test_thumbnails.py`、`tests/test_round13_regressions.py`。
