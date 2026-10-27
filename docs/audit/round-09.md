# 第 09 轮 · 断网恢复、HTTP 缓存与冷启动性能

- 日期：2026-10-27
- 审查方式：5 个只读 subagent（备份/同步/导入导出、冷启动性能、HTTP 缓存与静态资源、断网/慢网、下载失败恢复），每条要求「文件:行号 + 逐字代码 + 复现 + 影响」，Lead 逐条甄别后自己改代码。
- 结论：**22 条发现 → 修复 20 条，转 backlog 1 条，驳回 1 条**（P1×1 / P2×15 / P3×6）。

## 一、备份 / 同步 / 导入导出（5 条，全部修复）

| # | 级别 | 问题 | 修复 |
| --- | --- | --- | --- |
| 1 | P2 | `POST /api/sync/pull` 用远端文件整体覆盖本地队列，无确认、无备份、无撤销；空文件即清空队列 | `tiklocal/services/url_list.py`：pull 前把当前队列备份为 `<name>.bak`（`_backup_current`），响应里回 `backup` 文件名 |
| 2 | P2 | 5000 行以上的队列文件被静默截断（导入/同步都只保留前 5000 条，无任何提示） | `parse_text_limited()` 超限抛 `ValueError`，`downloads.py` 的 push/pull/import 捕获后返回 400 + `download.error.list_too_long` |
| 3 | P2 | `POST /api/sync/source` 先写 `source_path` 再校验方向/路径，`direction` 非法时把已有 `source_path` 覆盖成空串 | 先校验 `direction in {'push','pull'}` 与非空 `source_path`，再 `_write_config` |
| 4 | P2 | 配置写盘不是原子的（`yaml.safe_dump` 直接覆盖），进程被杀可能留下半截配置 | `tiklocal/app.py` 的 `_write_config()` 改用 `write_json_atomic` 同款的 tmp+`os.replace` |
| 5 | P2 | 设置页「同步全部作者」串行无上限，几百个作者时请求挂几分钟 | `author.py` 加 `MAX_SYNC_AUTHORS_PER_REQUEST = 200`，超限返回 400 + `settings.sync.author_too_many` |

## 二、断网 / 慢网（4 条，全部修复）

| # | 级别 | 问题 | 修复 |
| --- | --- | --- | --- |
| 1 | P2 | 全站没有任何 `online`/`offline` 监听：断网后每页停在错误态，网络恢复也不会自动重拉 | `base.js` 广播 `tiklocal:online` / `tiklocal:offline`；库页、合集页、下载页、设置页各自监听后重跑首屏加载 |
| 2 | P2 | 下载页离线时后台轮询每 12 秒弹一条英文红条，还会挤掉用户刚触发的提示 | `download_page_controller.js`：`navigator.onLine === false` 时静默等待；其余失败 2s→30s 指数退避；恢复后 `force` 刷新 |
| 3 | P2 | 只有 `/flow` 的请求有超时，其余页面的 fetch 挂起时永远停在 loading，重试按钮也不出现 | `library_page_controller.js`、`collections_page_controller.js`、`settings_page_controller.js` 统一加 `AbortSignal.timeout(20000/30000)` |
| 4 | P2 | 缩略图 404 时浏览器显示破图图标，alt 为空读屏也没有提示 | `base.js` 捕获阶段监听 `error`：隐藏破图并给父节点铺一层 `--surface-muted` 占位底色 |

## 三、下载失败恢复（3 条，全部修复）

| # | 级别 | 问题 | 修复 |
| --- | --- | --- | --- |
| 1 | **P1** | 第 07 轮把 yt-dlp 改成 `-P temp:` 后，`MoveFiles` 行仍只打印绝对路径，成功任务的 `output_files_rel` 指向已被删除的临时目录，后续缩略图/播放全部 404 | `download_tools.py` 的 `MoveFiles` 同时打印临时目录内的相对路径 |
| 2 | P2 | 重新下载已存在文件时任务「成功」但 `file_count = 0`，前端显示「完成 · 0 个文件」 | `downloader.py` 过滤掉落在临时目录下的结果文件，并记一条 warning；真正 0 文件时保留提示 |
| 3 | P2 | `jobs.json` 持久化失败时任务仍留在内存里，UI 显示排队但永远不会被处理（幽灵任务） | `enqueue()` 持久化失败时回滚：弹出任务、删除已写文件，接口返回 500 |

## 四、HTTP 缓存与静态资源（5 条，修复 4 条）

| # | 级别 | 问题 | 修复 |
| --- | --- | --- | --- |
| 1 | P2 | `/media/<uri>` 对所有扩展名都 `inline` 下发，`.svg` 可在本站源内执行脚本（存储型 XSS 面） | `media.py` 加 `INLINE_MEDIA_SUFFIXES` 白名单（视频/图片/音频），其余一律 `attachment` |
| 2 | P2 | `/thumb` 设置了 ETag 但从不调用 `make_conditional`，ETag 形同虚设 | `thumb_view()` 调用 `make_conditional(request)`，`If-None-Match` 命中返回 304（实测 8018B → 0B） |
| 3 | P2 | `/thumb` 的非 200（占位图）也被缓存 1 小时，清完缓存后缩略图一直不出来 | 非 200 改 `no-store`；`app.py` 的 `after_request` 再加一道兜底（`status >= 400` 时强制 `no-store`） |
| 4 | P3 | `thumb_url` 不带版本参数，文件更新后浏览器可能继续用旧图 | **驳回**：`/thumb` 已改成 `private, no-cache` + 304 校验，加版本参数属于重复缓存层 |
| 5 | P2 | 设置页从 bfcache 恢复后表单还是旧值，此时点保存会把旧配置写回去 | `settings_page_controller.js` 监听 `pageshow`，`event.persisted` 时整页重载 |

## 五、冷启动与请求性能（5 条，修复 4 条）

| # | 级别 | 问题（实测） | 修复 |
| --- | --- | --- | --- |
| 1 | P2 | 缩略图生成把整张原图解码进内存：24MP 单张 +192MB，6 线程并发峰值 **983MB** | `thumbnail.py` 先 `source.draft('RGB', (1280, 1280))` 让 libjpeg 按 1/2–1/8 解码，再用 `BoundedSemaphore(2)` 限并发；24MP 峰值 **121MB**（与空载基线一致），6 并发同为 121MB，输出仍是 640px |
| 2 | P2 | 全新库首启后 media-probe 线程连续 15 分钟给每条视频起 ffprobe，无节流（8000 条 ≈ 15.5 分钟） | `library_index.py` 的 `_loop` 加每轮预算（默认 5s），跑满即 `self._wake.wait(0.5)` 让出 CPU |
| 3 | P2 | 每次 `/api/feed/mix` 全量重算候选池：8000 条 553–622ms、单次 127 万次 Python 调用 | `recommendation.py` 按索引变更计数缓存候选池（2s TTL）+ `canonicalize_many()` 批量归一收藏：**热路径 50ms vs 冷路径 261ms**，线上 `/api/feed/mix` 0.6s → 0.35s |
| 4 | P3 | 冷启动 3.3s 花在每文件一次 `Path.resolve()`（backlog 曾记为已修，实测未修） | `library.py` 的 `ref_for_path()` 先用 `path.relative_to(resolved_root)` 命中，失败再 `resolve()` 兜底：**8000 次 4173ms → 272ms** |
| 5 | P3 | 每个 HTML 页面内嵌 905 条 / 48KB i18n 目录，占首页 62% | **转 backlog**：需要按页面拆分 key 或产出 `i18n.<locale>.<hash>.js`，改动面覆盖所有模板与控制器，单独一轮处理更稳妥 |

## 验证

- `pytest -q`：**427 passed / 6 skipped / 0 failed**（162.72s；性能修复后比上一轮 203s 更快，`ref_for_path` 与候选池缓存直接体现在启动与首屏用例上）。
- 新增 `tests/test_round09_regressions.py`（8 个用例）：非媒体后缀附件下发、jpg 内联、`/thumb` ETag + 304 + miss `no-store`、清单超长 400、pull 保留可恢复备份、yt-dlp `MoveFiles` 相对路径、临时目录产物不入库、作者同步超上限 400。
- 性能修复用基准脚本验证（`.smoke-run/bench_thumb_mem.py` 等）：24MP 缩略图峰值 222MB → **121MB**（与空载基线持平）、6 并发 790MB → 121MB、输出仍为 640×427；`ref_for_path` 8000 次 **4173ms → 272ms**；候选池冷 261ms / 热 50ms，`records()` 只在冷路径调用一次；线上 `/api/feed/mix` 0.6s → 0.35s。
- 浏览器实测（`.smoke-run` 实例，真实媒体库 127 条）：`/thumb` 200 + `ETag` + `private, no-cache`；带 `If-None-Match` 得 304；`/media/@default/<video>.mp4` 仍是 `video/mp4` 内联；设置页 200。

## 记入 backlog

1. **i18n 目录瘦身**（P3，见上表第五节第 5 条）。
