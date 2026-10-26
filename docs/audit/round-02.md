# 第 02 轮审查报告（2026-10-27）

起点基线：`pytest -q` = **2 failed / 358 passed / 6 skipped**（第 01 轮结束时，剩余 2 个为 Windows 权限位断言）。
本轮终点：`pytest -q` = **2 failed / 365 passed / 6 skipped**（74.52s），失败项与起点完全相同。

## A. 并行审查

5 个 fresh 只读 subagent，文件域互不重叠：

| subagent | 域 | 发现 |
|----------|----|------|
| audit-download-pipeline | `services/downloader.py`、`services/tiktok_downloader.py`、`download_*.js` | 2×P1、2×P2、1×P3 |
| audit-data-layer | `services/favorite.py`、`media_index.py`、`library_index.py`、`collections.py`、`sync.py`、`trash.py` | 1×P1、1×P2、1×P3 |
| audit-core-routes | `web/flow.py`、`library.py`、`collections.py`、`trash.py`、`media.py`、`radio.py` | 2×P1、2×P2、1×P3 |
| audit-web-routes | `web/settings.py`、`author.py`、`captions.py` | 3×P2、1×P3 |
| audit-frontend-shell | `base.html` 及全局 JS（`shell.js`、`keyboard.js`、`detail_controller.js`） | 1×P2、2×P3 |

合计 19 条，全部由 Lead 复核复现后处理。

## B. 确认与处置

### 已修复（17）

| # | 级别 | 问题 | 位置 | 处置 |
|---|------|------|------|------|
| 1 | P1 | `/api/feed/mix` 每页对全池 `filter(...len)` 再 `pool[offset:]`，页号越大越慢（O(n²)），page=5000 时单请求 ≈12 s 且持 GIL | `tiklocal/web/flow.py:105-113` | `start >= len(pool)` 先直接返回空页（复用 `_serialize_item` 提取器），循环起点 `start=0`；实测 page=5000/size=200：**≈12 s → 0.8 s** |
| 2 | P1 | 电台列表对每条未缓存音轨串行 `ffprobe`（最多 200 条），冷缓存下整个 `/radio` 首屏阻塞数十秒 | `tiklocal/web/radio.py:79-90` | 新增 `RADIO_ITEMS_MAX_LIMIT = 60`、按 source 的 `_probe_limits`（每 source 16 条）+ `_warm_queue`/`_warm_worker` 后台预热线程；只对已缓存/预算内的条目探测 |
| 3 | P1 | `cancel(job_id)` 只改状态，`_execute_download`/`_direct_fetch`/`_run_tool` 无检查点，取消后仍下载完并覆盖状态 | `tiklocal/services/downloader.py` | 新增 `_is_cancelled()`，在 `_direct_fetch` 每次写块前、`_run_tool` 轮询循环内、`_execute_download` 各阶段检查；命中即 `DownloadCancelled` |
| 4 | P1 | 下载无墙钟超时，`yt-dlp` 卡住时 job 永久 running（等待线程池） | 同上 | `_run_tool` 增 `deadline = time.monotonic() + self._job_timeout`（`job_timeout` 默认 900 s，可注入）；超时 kill 进程并置失败 |
| 5 | P2 | `enqueue()` 在锁内调 `probe_binary()`（起子进程），并发入队时相互阻塞 | `downloader.py:465-503` | 探测移到锁外，锁内只赋值 `engine_version` |
| 6 | P2 | `_update_progress` 每次都 `_persist_locked()`（整份 jobs.json 重写），进度回调高频时 IO 放大 | `downloader.py:1101-1112` | 节流：距上次持久化 <1 s 且未跨阶段时不写盘，完成/失败/取消仍强制落盘 |
| 7 | P2 | `_direct_fetch` 把 `OSError` 归为「网络不可达」`Unable to reach the media host`，误导用户 | `downloader.py:1041-1077` | 按 `errno` 分类：`EACCES/EPERM/ENOSPC` → `download.error.local_write`，其余网络类保留原文案 |
| 8 | P1 | 集合库 JSON 损坏（非数组/半截写）时 `CollectionStore.load()` 抛 `ValueError` → 所有集合页 500 | `tiklocal/services/collections.py` | `CollectionStoreCorrupt` + `_read(strict)`；损坏文件隔离为 `.corrupt-<ts>` 备份、目录改名重建；`list_with_items()` 单次读盘 + 批量补条目；路由 `errorhandler(CollectionStoreCorrupt)` → 503 + 中文提示；写路径全部 `write_json_atomic` |
| 9 | P2 | 推荐流 `datetime.combine(...).timestamp()` 用本地时区，与媒体日期（UTC）错位，跨时区用户「最近」排序错乱 | `tiklocal/services/recommendation.py` | 统一 `timezone.utc`，并按 `seed` 决定 `DIVERSITY_PICKS` |
| 10 | P2 | `local_work_names()` 吞掉索引读异常返回 `[]`，作者「更新最新视频」会重复下载已有作品 | `tiklocal/web/author.py:102-106` | 删除 `except Exception`，异常冒泡到调用点 502；前端已按 `items[].ok` 计数 |
| 11 | P2 | 设置页/`/api/library/stats` 无异常兜底：favorites.json 非数组或清缓存并发时 500 | `tiklocal/web/settings.py:28-40` | `media_index.stats()`/`cache_stats()`/`favorite_service.load()` 各自 try/except 降级为 0 并 `log.warning` |
| 12 | P2 | `/api/image/metadata` 把任何生成失败变 500 并回显 `str(error)`（可能含掩码 key 片段/内网 base_url） | `tiklocal/web/captions.py:76-77` | `CaptionConfigError` → 400、provider 异常 → 502，均回固定文案；`service.generate_caption` 抛错不再外泄 |
| 13 | P2 | `/api/trash` 清空与单条清理：文件被占用时 500 且绝对路径回显，且首个失败中断整批 | `tiklocal/web/trash.py`、`services/trash.py` | `purge()` 失败计数；单条路由 409 `trash.error.in_use`；清空返回 `{purged, failed}`（保留 `purged` 键） |
| 14 | P2 | 缩略图时间点 `{"time": "abc"}`/负数直接 200 并生成垃圾缩略图 | `tiklocal/web/media.py:247-270` | 校验：非有限数/负数/≥时长 → 400 `detail.error.invalid_time` |
| 15 | P2 | 全局键盘处理器在输入框/弹窗/下载页劫持 Esc、方向键、空格 | `keyboard.js`、`shell.js`、`detail_controller.js` | 统一 `isTypingTarget()`/`isModalOpen()` 守卫；下载页 Esc 改为 `stopPropagation` |
| 16 | P3 | 缩略图接口 `?t=` 用 `time.time()`，同秒内两次请求命中同一 URL，浏览器不刷新 | `tiklocal/web/media.py:79` | 改 `time.time_ns()` |
| 17 | P3 | `web/captions.py` 5 条错误文案硬编码英文，中文界面直接透出 | `captions.py:20/47/54/67/71` | 改用 `t()` + 新增 `image.error.missing_uri/file_not_found/disabled/provider_failed` |

### 驳回 / 转 backlog（2）

| # | 级别 | 问题 | 处置理由 |
|---|------|------|----------|
| 18 | P3 | `douyin_mode.js:52` `location.replace` 丢失来源页 | 抖音模式入口本身是「进入沉浸」，丢失来源页属产品预期；如需返回栈应改前端路由设计，列入 backlog 待评审 |
| 19 | P3 | `/api/library/stats` 在 1500 张缩略图下并发清缓存偶发 `FileNotFoundError` | 已由 #11 兜底（降级为 0），根因在 `thumbnail.py:141` 的 `exists()`→`stat()` 竞态，已在第 01 轮 thumbnail 修复中改为 `try/except` 处理，本轮复核确认不再 500 |

## C. 最优解检索

- **feed 分页 O(n²)**：确认「先判越界再切片」是 Python 列表分页的标准写法；`itertools.islice` 对 `list` 无收益（`list` 切片本身 O(k)），故不引入。
- **下载超时**：`subprocess.wait(timeout=)` 已用于 `_run_tool` 的单次读超时；墙钟 deadline 与 `yt-dlp --socket-timeout` 互补（前者防挂死，后者防慢速），故两者并存。
- **JSON 原子写**：沿用第 01 轮 `write_json_atomic`（`tmp` + `os.replace`），与 `media_index`/`favorite` 一致。

## D. 验证

- 定向：`pytest tests/test_collections.py tests/test_trash.py tests/test_web_runtime.py tests/test_captions.py tests/test_media_state.py -q` 全绿；新增 `test_purge_of_locked_file_is_reported_as_conflict`（409 + `{purged:0, failed:1}`）与集合库损坏测试。
- 全量：**2 failed / 365 passed / 6 skipped**（+7 passed 相对起点，失败集合不变）。
- 实机（`.smoke-run` 实例，`127.0.0.1:8000`）：
  - `POST /login` 302；`/api/feed/mix?page=1` 76 ms、`page=200` 19 ms、`page=5000` 38 ms（空页直接返回）。
  - `POST /api/thumbnail/<video>`：`{"time":"abc"}` / `{"time":-5}` → **400** 中文文案；`{"time":2.5}` → 200。
  - favorites.json 写成 `{"broken":1}` 后：`/api/library/stats` **200**（`favorites: 0`）、`/settings/` **200**（87 KB），恢复后正常。
  - `DELETE /api/trash` → `{"purged": 0}`；`/api/image/metadata` 空体 → 400 中文文案。
  - `POST /api/download/urls/sync` 指向 instance 内 `auth.json` → **400** 中文拒绝文案，`auth.json` 字节数不变；32 MiB 请求体 → **400**（`MAX_CONTENT_LENGTH` 8 MiB），不再 500/401。

## E. 变更清单（18 文件）

`tiklocal/services/downloader.py`、`collections.py`、`recommendation.py`、`trash.py`、`captions.py`、`tiklocal/web/__init__.py`、`flow.py`、`radio.py`、`settings.py`、`author.py`、`trash.py`、`media.py`、`captions.py`、`tiklocal/static/keyboard.js`、`shell.js`、`detail_controller.js`、`i18n_pages_media.py`、`i18n_pages_shared.py`、`tests/test_trash.py`、`tests/test_collections.py`。

## F. 遗留（进入 backlog）

- `douyin_mode.js` 返回栈（#18）。
- 缩略图 `exists()`→`stat()` 竞态的彻底消除（改为 `try/except` 已覆盖，进一步可加锁或 `os.stat` 直读）。
