# 第 25 轮审查报告（内存与资源预算 / 长时运行稳定性）

本轮视角与前三轮互补：第 21 轮看新代码与安全、第 22 轮看数据完整性与并发、第 23 轮看前端状态机与可访问性、第 24 轮看后端错误路径与边界。本轮只问一个问题——**一个连续跑几天、库有几万条、前端常开几个标签页的实例，哪里会单调增长、哪里会在峰值上撞墙**。

七个域并行审查（域划分见下），共 50 条发现（去重后 50 条）：**P0 0 条、P1 6 条、P2 27 条、P3 17 条**。P1 当轮修完 5 条（第 6 条 R6-1 在第 23 轮已修，本轮复核确认仍然成立），P2 修 5 条，P3 未修（全部转 backlog）。剩余 40 条按严重度登记在 `docs/audit/backlog.md` 的「第 25 轮新增」一节。

## 一、索引与数据库（域 R1）

审查 `tiklocal/services/library_index.py`、`database.py`、`media_index` 相关读写路径，以及搜索分页。

| ID | 级别 | 位置 | 结论 |
|---|---|---|---|
| R1-1 | P2 | `tiklocal/services/library_index.py:1105-1111` | `Executor.map` 先建满 19k 个 Future，与 `records`、`time_states` 同刻存活，19k 库冷扫峰值 55-60 MB |
| R1-2 | P2 | `tiklocal/services/database.py:523,534` | 活动事件的 `metadata`/`uri` 未截断（其余字段都截），8 MB 单请求 × 2 万行保留，库文件可涨到数 GB |
| R1-3 | P2 | `tiklocal/services/database.py:631-684` | `media_affinity` 每 uri 一行且只增不减，重下载/改名累积孤儿行，10 万行约 20 MB |

已排除（确认无问题）：`AppDatabase.connect()` 是 `@contextmanager` 且 `finally: conn.close()`（`:400-417`），全包无 `with conn:` 误用；全包 `lru_cache`/`functools.cache` 零命中；读路径结果集全部有界（`page()` 的 `safe_limit`、`web/search.py` 的 limit≤48 与 q≤200）；SQL 变量上限安全（按 500 分块、`unprobed` 的 exclude 截断 400）；`replace_snapshot` 的 19k 次 upsert 在单个事务内提交，`media_index_seen` 为 TEMP 表随连接释放。

**R1-2 已修**（见第八节）。

## 二、下载队列与下载器（域 R2）

审查 `tiklocal/services/downloader.py`、`download_sources.py`、`download_tools.py`、`tiktok_downloader.py`、`web/downloads.py`。

| ID | 级别 | 位置 | 结论 |
|---|---|---|---|
| R2-1 | **P1** | `tiklocal/services/download_sources.py:156-166` | 读来源映射遇 WinError 32 或 JSON 损坏时返回空表，下一次 `set_many` 即用单条记录覆盖整表，**全库来源元数据丢失** |
| R2-2 | P2 | `tiklocal/services/downloader.py:433-437` | `_jobs`/`_job_order`/`_cancel_events`/`_progress_persist_at` 只在手动删除时回收，200 上限仅作用于落盘 |
| R2-3 | P2 | `tiklocal/services/downloader.py:1466-1468` | `download_payload` 终态后仍随 job 永久保留，`jobs.json` 每秒全量重写，2 并发约 2-6 MB/s 且全程持锁 |
| R2-4 | P2 | `tiklocal/web/downloads.py:246-261` | 批量入队对每条 URL 各做一次全量落盘与子进程探测，5000 条时单请求占死一个 waitress 线程数分钟 |
| R2-5 | P2 | `tiklocal/services/downloader.py:1639-1646` | `/api/source` 未命中即整表重写来源映射，随库规模 O(n²) I/O |
| R2-6 | P2 | `tiklocal/services/tiktok_downloader.py:302-303` | sidecar 响应 `read()` 无大小上限，`source=true` 取百条原始作品约 4 MB JSON 仅为读 id |

已排除：`_workers`/`_processes` 在 `_run_job` 的 `finally` 中正确弹出（`:1089`、`:1097`）；临时目录不泄漏（每个 job 的 `finally` 里 rmtree，崩溃残留由启动时 `_cleanup_stale_temp_dirs` 清理）；`_run_tool` 的 `lines` 队列虽无界但生产与消费同属一个 job，峰值仅几 KB；`_terminate_process` 有 `poll() is None` 守卫；`claim_available_path` 有 1000 次上限；`DownloadHistoryStore.save` 已截断到 200 条。

**R2-1 已修**（见第八节）。

## 三、媒体服务与缩略图（域 R3）

审查 `tiklocal/services/thumbnail.py`、`media_info.py`、`trash.py`、`web/media.py`、`web/media_payloads.py`。

| ID | 级别 | 位置 | 结论 |
|---|---|---|---|
| R3-1 | **P1** | `tiklocal/services/thumbnail.py:613-648` | ≥48 MP 非 JPEG 冷缓存先全尺寸解码，4 路并发峰值 2.9 GB，可被一次滚动 OOM |
| R3-2 | **P1** | `tiklocal/web/media.py:373-377` | `If-Range` 请求一律绕过 `file_wrapper`：校验符不匹配时 200 全文件走生成器，暂停的视频把 worker 钉死到 `channel_timeout` |
| R3-3 | P2 | `tiklocal/services/thumbnail.py:353-390` | 缩略图缓存无条目上限与淘汰，外部删除的媒体缩略图永不回收 |
| R3-4 | P2 | `tiklocal/services/media_info.py:27-33` | `exif_transpose` 为读一个尺寸对而整图解码并复制，60 MP 图峰值 360 MB，且不占解码槽位 |
| R3-5 | P2 | `tiklocal/web/media.py:531-543` | `/thumb` 先 `read_bytes` 再判 304，no-cache 下每次条件请求都白读整份缩略图 |
| R3-6 | P2 | `tiklocal/web/media.py:287-288` | 每次 `GET /api/trash` 把回收站目录全量扫两遍 |
| R3-7 | P2 | `tiklocal/services/trash.py:88-104` | manifest 缺 `deleted_at` 时抛 `KeyError`，而这段在启动路径上——一个坏条目让整个应用起不来 |

已排除：`/media` 纯 Range 与无 Range 的 200 都是真流式（`wsgi.file_wrapper` → waitress `ReadOnlyFileBasedBuffer`，不占请求线程）；`_BoundedReader`/`_iter_reader` 有 `finally: handle.close()`；`_GENERATE_SLOTS`、`_locks`（固定 8 个）、`_inflight`、`_TEMP_COUNTER` 均有界；`/thumb` 单次 `read_bytes()` ≤ ~200 KB，60 格约 12 MB，不构成 RSS 问题（只计 R3-5 的读放大）；`_delete_folder` 先删 payload 再删 manifest，中途失败只留可被忽略的半删条目。

**R3-1、R3-2 已修**，R3-4、R3-7 也已修（见第八节）。

## 四、推荐 / 电台 / 作者 / 元数据（域 R4）

| ID | 级别 | 位置 | 结论 |
|---|---|---|---|
| R4-1 | **P1** | `tiklocal/web/flow.py:334-345` | 话题流每翻一页都重建全库记录、对每个专辑封面各查一次 `album_members`，20k 库单页约 30 MB 峰值加数千次 SQL |
| R4-2 | P2 | `tiklocal/services/recommendation.py:79-87` | 候选缓存键取未校验的 `blog` 查询串且从不删键，进程内键空间只增不减 |
| R4-3 | P2 | `tiklocal/services/metadata.py:15-17` | 标注元数据每次读写都整份解析并重写 JSON，20k 条约 10 MB 且全程持锁串行 |
| R4-4 | P2 | `tiklocal/services/embedded_metadata.py:25` | 只解析 JPEG 头部注释却整文件 `read_bytes`，打开大图详情的内存峰值等于文件大小 |
| R4-5 | P2 | `tiklocal/services/radio_energy.py:225-226` | 超 4 小时曲目提前断流致 ffmpeg 非零退出、恒返回 None，且每次请求重解 4 小时音频 |
| R4-6 | P2 | `tiklocal/services/captions.py:430-436` | AI 标注前先全分辨率解码再缩到 1536px，大 PNG 单请求峰值可达 300 MB 以上 |

已排除：`radio_energy` 的包络缓存按 `st_mtime_ns` 校验，改文件不会命中旧值；候选池与挑选量有 `POOL_CAP=1500` / `DIVERSITY_PICKS=240` 硬上限，`rng.sample` 不物化整池；头像下载有 4 MB 上限与唯一临时名；解码超时会 `_kill_process` 且 `join(60)` 有界；`json_storage` 的损坏文件走 quarantine，持久 OSError 抛 `StoreUnreadableError(OSError)`，不会用 `{}` 覆盖真实 profile。

**R4-1、R4-4 已修**。

## 五、feed 与详情页前端（域 R5）

| ID | 级别 | 位置 | 结论 |
|---|---|---|---|
| R5-1 | P2 | `tiklocal/static/library_page_controller.js:840` | Quick Viewer 图片直取原图，翻页无守卫无节流，按住方向键约 30 req/s 打满线程池 |
| R5-2 | P2 | `tiklocal/templates/image_detail.html:163` | 隐藏的全屏层 eager 拉原图，控制器又 `new Image` 二次请求并常驻，24 MP 峰值 200 MB+ |
| R5-3 | P2 | `tiklocal/static/flow_ui_shared.js:108` | 放大镜关闭不清 `backgroundImage`，原图解码位图与 2.5× 光栅常驻，每换一张钉住一张大图 |

已排除：相册 stage 子元素不随相册长度累积（滑动结束移除 outgoing，另有按 `data-group-generation` 的兜底清扫）；时间线页 4 个 `IntersectionObserver` 只在模块初始化建一次，命中即 `unobserve`；域内 Set/Map 均有界（`collectionsById` 每次 render 先 `clear()`、`flow_session.keys` 随 `dropHead`/`reset` 同步删、`selectedNames` 退出多选即清空）；`playback_resume.tracked` 全站只有 2 处 `track()` 且复用同一元素；两个 `MutationObserver` 与 Hammer 实例数量固定；全站 `static/*.js` 无 `createObjectURL`。

## 六、其余前端页面与基础层（域 R6）

审查 `base.js`、`radio_*`、`download_page_controller.js`、`author_page_controller.js`、`settings_page_controller.js`、`search_page_controller.js`、`douyin_mode.js`、`i18n.js`、`ui_feedback.js`、`playback_resume.js`。

| ID | 级别 | 位置 | 结论 |
|---|---|---|---|
| R6-1 | （P1，第 23 轮已修） | `tiklocal/static/radio_controller.js` | 电台偏好写在 `localStorage` 的全局键上，同一浏览器切换账号后能看到上一位用户的房间/音量/收藏；本轮复核确认修复仍成立 |
| R6-2 | P2 | `tiklocal/static/base.js:199-214` | 图片 `error` 兜底是单向闩锁：重试成功、图已解码，`visibility:hidden` 与 `fallbackApplied` 无人撤销 → 该条画面永久空白，搜索卡片永远看不到兜底头像 |
| R6-3 | P2 | `tiklocal/static/radio_ambience.js:49`、`:243-248` | 房间音效 `cache` 只写不删：切过雨→雪→火→夜后四套 PCM 常驻约 70 MB（48 kHz），iOS Safari 单标签音频预算约 256 MB，反复切有整页被回收风险 |
| R6-4 | P2 | `tiklocal/static/download_page_controller.js:24-28` | `api()` 无 `AbortSignal`，`refreshJobs → renderJobs → schedulePolling` 自链在后端半断时断掉，进度条与状态永久冻结且零提示 |
| R6-5 | P2 | `tiklocal/static/author_page_controller.js:107-115` | `postJSON` 无超时，`setBusy(true)` 后靠 `finally` 解锁 → 后端卡住时输入框与三个按钮永久禁用（设置页同类请求已加 60 s 超时，此处漏改） |

已排除：下载页轮询不会重复注册（`schedulePolling` 先 `clearTimeout`，全文件无 `setInterval`）；电台页隐藏时正确停资源（`syncAtmosphere` 的 `shouldMove` 含 `!document.hidden`/`prefers-reduced-motion`/`saveData`）；WebGL 不随曲目增长（纹理只在 setup 建一次、program 按 shader URL 缓存、场景每页只建一次）；`playback_resume` 的 `tracked` 只装 2 个常驻 video；`i18n.js` 无状态、toast 3-6 s 自毁、`douyin_mode` 的 `getItem` 已 try/catch、设置页配对倒计时启动前先 `stopPairingTimer` 且到点自停。

**R6-2 已修**。

## 七、装配、存储、日志与启动（域 R7）

| ID | 级别 | 位置 | 结论 |
|---|---|---|---|
| R7-1 | **P1** | `tiklocal/logging_setup.py:89-94` | 固定文件名 + 每个子命令都 `configure_logging`：两进程持同一 `tiklocal.log` 时轮转抛 `WinError 32`，`RotatingFileHandler` 关流后永不重开，**日志静默死亡**（连解释失败的 traceback 一起丢） |
| R7-2 | P2 | `tiklocal/app.py:132-166` | 损坏数据库副本无上限：每次启动隔离一份 `*.corrupt-*`，反复启动会把数据目录撑满 |
| R7-3 | P2 | `tiklocal/auth.py:38-43` | 限流表满且所有桶仍在窗口内时，新键不记录（有意取舍：驱逐活桶等于给攻击者清零） |

已排除：`config_backup` 的导出走临时目录 + `finally` 清理；`instance` 锁与 `paths.get_data_dir()` 在启动时只解析一次；`config_sync` 的差异计算有大小上限。

**R7-1 已修**。

## 八、修复取舍

修 10 条：5×P1 + 5×P2。选择标准是「长时运行会真的坏掉」而不是「能省一点内存」。

1. **R2-1 来源映射整表被覆盖（P1）** — 这是本轮唯一的**数据丢失**类缺陷，任何读失败（Windows 上另一进程持句柄、JSON 被截断）都会让下一次写入把空表落盘。修法是让「读不出」和「没有记录」在类型上分开：`_read()` 改用 `json_storage.read_json_file`（文件缺失才返回默认值，真损坏走 quarantine，持续 OSError 抛 `StoreUnreadableError`），写盘改用 `write_json_atomic`（唯一临时名 + 退避重试，替掉固定 `.tmp` + `os.replace`）；`get()`/`get_many()` 把 `StoreUnreadableError` 降级为「没有来源」（渲染徽章的请求不该因为文件锁失败），`set_many`/`delete` 继续向上抛，两个下载回调包 `OSError` 并记 warning——下载本身成功，不该因为读不出的映射变成 failed。
2. **R3-1 缩略图解码峰值（P1）** — 4 个槽位 × 一张 100 MP 的 PNG/WebP 全尺寸解码，峰值接近 3 GB。`draft()` 只对 JPEG 有效，所以给「超过 48 MP」的图单独一个 `_BIG_IMAGE_SLOT`（`BoundedSemaphore(1)`），普通照片仍用全部槽位；同时把 `ImageOps.exif_transpose` 改成 `in_place=True`（Pillow 12.3 支持，返回 None 时回退旧语义），省掉旋转时那份全尺寸 RGB 副本（24 MP 就是 72 MB）。
3. **R3-2 `If-Range` 绕过流式（P1）** — 原来的守卫把 `If-None-Match`/`If-Modified-Since`/`If-Range` 一起交给 `send_file`。前两者确实该交给它（304 语义），但 `If-Range` 不匹配时 `send_file` 会走生成器版 `RangeWrapper`，waitress 只能把 2 GB 在请求线程里抄一遍。改成自己判定：`_if_range_allows_range()` 用与 `_apply_media_validators()` 同一套校验符（`adler32` + mtime + size，与 `send_file` 同形），匹配就走 206，不匹配就用 `_whole_file_media()` 构造 200——有 `wsgi.file_wrapper` 就交给它，没有就退回 `_BoundedReader`。
4. **R4-1 话题流每页重建全库（P1）** — 每翻一页都 `records()` 全表、对每个专辑封面各查一次 `album_members`。给 `collapse_media_entries()` 加 `stop_after`，话题流只要 `end + 1` 条（够回答 `has_more`），把数千次 SQL 和几十 MB 的中间列表砍掉。
5. **R7-1 日志轮转打死 handler（P1）** — `RotatingFileHandler` 在轮转前先关流，Windows 上重命名失败后流就再也不开。新增 `_SharedRotatingFileHandler`，`doRollover()` 捕获 `OSError` 后重开流（重开也失败就保持 None，下一条记录只走 stderr，下次轮转再试）。
6. **R1-2 活动事件无长度上限（P2）** — `uri` 截到 512、`metadata_json` 超过 2048 字符换成 `{"truncated": true}`，与同函数里其余字段的处理保持一致。
7. **R3-4 读尺寸却整图解码（P2）** — `probe_media_dims` 改成只读 `img.size` 与 EXIF `0x0112`（5/6/7/8 交换宽高），不再 `exif_transpose` 解码；48 MP 图的峰值从 ~150 MB 降到几 KB。
8. **R3-7 坏 manifest 让应用起不来（P2）** — `entries()` 跳过没有 `deleted_at` 的条目（`expired()` 因此也安全），注释写明这条路径在启动上。
9. **R4-4 读头部注释却读整文件（P2）** — `read_jpeg_comments` 只读前 4 MB（注释总在首个 SOS 之前，超长头部只会少找到几条而不是读错）。
10. **R6-2 图片兜底单向闩锁（P2）** — 补一个捕获阶段的 `load` 监听：同一个 `<img>` 重试成功时撤销 `visibility`、`fallbackApplied` 与父节点的占位底色。影响面是 flow 的「重试」、断网自动重试与搜索卡片的兜底头像。

**刻意不修**：R7-3（限流表满时新键不记录）是第 21 轮加 `MAX_KEYS` 时的有意取舍——驱逐一个仍在封禁窗口内的地址比少记一次失败更糟，改它需要换成前缀聚合或全局令牌桶，属设计变更。R2-2/R2-3（下载任务对象只增不减）是下一轮最值得做的两条，但要先决定「历史任务详情」是否必须留在内存里。

## 九、修复实现要点

- `tiklocal/services/download_sources.py`：新增 `_empty_payload()`；`_read()` → `read_json_file`；`_write()` → `write_json_atomic`；`get`/`get_many` 降级，`set_many`/`delete` 上抛。
- `tiklocal/services/downloader.py`：`_record_job_sources_on_success()` 与 `_resolve_source_from_info_json()` 各包 `except OSError` + `LOGGER.warning`。
- `tiklocal/services/thumbnail.py`：新增 `_BIG_IMAGE_SLOT = threading.BoundedSemaphore(1)`；`_generate_image()` 在 `big` 分支 acquire/release；`exif_transpose(..., in_place=True)`。
- `tiklocal/web/media.py`：抽出 `_media_validators()`；新增 `_if_range_allows_range()` 与 `_whole_file_media()`；`_ranged_media()` 的守卫拆成「304 交给 send_file」与「If-Range 自己判定」两条。
- `tiklocal/web/flow.py`：`collapse_media_entries(..., stop_after=None)`；`build_tag_feed_page()` 传 `end + 1`。
- `tiklocal/logging_setup.py`：新增 `_SharedRotatingFileHandler`，`configure_logging()` 改用它。
- `tiklocal/services/database.py`：新增 `MAX_URI_CHARS = 512`、`MAX_METADATA_CHARS = 2_048`；`_record_many_once()` 截断 `uri` 与 `metadata_json`。
- `tiklocal/services/media_info.py`：`probe_media_dims()` 只读 `img.size` + EXIF 方向；去掉不再使用的 `ImageOps` 导入。
- `tiklocal/services/trash.py`：`entries()` 跳过缺 `deleted_at` 的条目。
- `tiklocal/services/embedded_metadata.py`：新增 `_HEADER_READ_LIMIT`，`read_jpeg_comments()` 只读前缀。
- `tiklocal/static/base.js`：新增捕获阶段 `load` 监听，撤销 `error` 兜底的隐藏与占位色。

## 十、验证

- `poetry run pytest` 等价命令 `.\.venv\Scripts\python.exe -m pytest -q`：**602 passed, 8 skipped**（与第 24 轮收尾基线一致，无新增失败、无新增跳过）。
- 手工核验新增逻辑（临时脚本，未入库）：
  - `_if_range_allows_range` / `collapse_media_entries(stop_after=…)` / `_BIG_IMAGE_SLOT` / `read_jpeg_comments`（真实 JPEG 带 COM 段）/ `TrashService.entries` 均按预期；
  - `create_app` 起测试客户端实测 `/media/clip.mp4`：纯 `Range` → `206` + `bytes 0-99/102400`；`If-Range: "bogus-etag"` → `200` + 完整 `Content-Length`；`If-Range: <真实 ETag>` → `206`；`If-None-Match` 仍由 `send_file` 处理（`206`，Range 优先）。
- 未验证：Windows 上「两进程同时写日志」的真实轮转（需要两个进程同时持有文件句柄，测试里只能用 mock 覆盖 `doRollover` 的异常分支）；`_BIG_IMAGE_SLOT` 在真实 100 MP 图上的峰值（手上没有这么大的样本，只验证了槽位逻辑）。

## 十一、覆盖率缺口

- 域 R6 与 R7 的审查报告来自子代理的独立会话，未落成 `round-25-r6.md`/`round-25-r7.md`；其结论并入本文件第六、七节，P3 明细只保留计数。
- 「连续跑几天」这一维度只能靠静态推理：没有长时压测脚本，也没有内存采样基线，所以所有 P2 的「会涨到多少」都是按对象大小与条目数估算的量级，不是实测值。
- 前端内存相关结论依赖代码阅读：没有用 DevTools 的 Memory/Performance 面板实测堆快照，`radio_scene.js` 的每曲纹理重传也没在真机上量过帧率。

## 十二、转 backlog

未修 40 条（P2 22 条 + P3 17 条 + 1 条设计取舍说明）全部登记进 `docs/audit/backlog.md` 的「第 25 轮新增（按严重度）」一节，并标注了「为什么暂不修」。下一轮视角建议：**外部依赖与网络边界**（yt-dlp/ffmpeg 子进程、sidecar 接口、DNS 与超时、TLS 与反向代理假设），这是唯一还没单独覆盖过的维度。

## 十三、独立复审（子代理）

派一个独立会话只读复审了全部改动：**阻塞项 0 条**，另给 5 条 P2 + 4 条 P3。它同时确认了几处我拿不准的契约：`read_json_file` 三态语义与三个调用方的兜底、`write_json_atomic` 的唯一临时名与退避、`exif_transpose(in_place=True)` 在 Pillow 12.3 恒返回 `None`（所以 `rotated is None` 的回退是必需的而不是冗余）、`_if_range_allows_range` 的 8 组矩阵（含多段 Range）、`collapse_media_entries` 无 off-by-one、`getexif()` 对无 EXIF 的 JPEG/PNG/GIF/BMP/WebP 都不抛异常且 54 MP JPEG 只读头部 0.5 ms。

其中 5 条直接指向我本轮引入的副作用，当天补修（复审项编号见下）：

1. **R3-11 槽位顺序反了（P2）** — `_GENERATE_SLOTS` 在外层、`_BIG_IMAGE_SLOT` 在内层：一张大 PNG 解码时，其余线程会**拿着** generate 槽位阻塞在 big 槽上（4 核时 generate=2，即全部槽位被占），普通小图和视频封面一起排队。改成 `_generate()` 对图片直接调 `_generate_image()`，由后者按 BIG→GENERATE 的顺序获取（无环），视频/音频仍只拿 GENERATE。
2. **R3-12 大图判定用了 draft 前的像素（P2）** — 50–60 MP JPEG 经 `draft()` 只剩 <1 MP、解码几毫秒，却仍要排队拿 big 槽，把流水线串行化。改成 draft 之后按重算的像素判定。
3. **R3-13 `acquire()` 无超时（P2）** — 病态大图卡住会无限期占槽，最多 `_GENERATE_SLOTS` 个 worker 陪等。改成 `acquire(timeout=_BIG_IMAGE_WAIT_SEC)`（180 s），超时记 warning 并让这块失败、下次请求重试（不写 `.fail` 标记，所以不会把 TTL 内重试也堵死）。
4. **R2-7 批量解析逐文件读映射（P2）** — `resolve_sources_for_files()` 对每个文件调一次 `get()`，store 持续不可读时每次要 5×50 ms 重试（实测 0.267 s，legacy 分支再翻倍），`/api/source/batch`（上限 200）单请求可钉住一个 worker 约 100 s。改成一次 `get_many()` 覆盖直接键与 legacy 键，再对未命中的走 `.info.json` / 文件名兜底。
5. **R3-14 trash 缺时间戳的条目不可见（P2）** — 我原本 `continue` 跳过，这些目录对 `/api/trash` 不可见、`restore`/`purge` 都够不到，会永久占盘。改成用目录 `st_mtime` 兜底生成 `deleted_at`，只有连 `stat` 都失败才跳过。

另外 3 条 P3 也顺手修了：`_prune_quarantine` 的 `stat()` 移进 try（并发清理时 `FileNotFoundError` 会冲出 `read_json_file` 变 500）；`_whole_file_media` 对不可调用的 `wsgi.file_wrapper` 回退到迭代器（原本抛 `TypeError` 并泄漏 fd）；超长 uri 由「直接截断」改成「前缀 + sha1 前 8 位」，否则同前缀的两条媒体共享亲和权重而 `profiles_for()` 又查不到。

只留 1 条进 backlog：日志轮转在第二进程长期占文件时 `shouldRollover` 恒真，**每条**记录都会跑一遍失败的重命名+重开（本轮之前也如此，只是记录被丢掉），可加 30 s 冷却减少无谓 syscall——收益小、改动在日志路径上，留到下一轮。

复审后重跑：相关 8 个测试文件 **98 passed / 2 skipped**，另用 7000×7000 的 PNG 实测大图路径（单张 0.13 s、两张并发 0.17 s/0.32 s 顺序完成、无线程滞留）。
