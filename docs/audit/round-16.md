# 第 16 轮审查报告

审查基：运行形态与并发（单实例/临时文件/共享存储）、数据与索引版本、下载引擎与外部进程、媒体与缩略图、多标签一致性。
派单方式：5 个复用只读 subagent（`audit-core-routes`、`audit-data-layer`、`audit-download-pipeline`、`audit-frontend-shell`、`audit-flow-ui`），全部要求「只读 + 证据（文件:行号、逐字代码、复现、影响）+ 分级」，禁止报风格与重构建议。

确认 20 条（P1×3 / P2×6 / P3×11），修复 14 条，其余 6 条转 backlog（理由见末节）。

## 一、运行形态与存储安全

| # | 级别 | 问题 | 位置 | 处置 |
|---|------|------|------|------|
| 1 | P1 | 同一 instance 双开互毁下载历史：后启动的进程把先启动者的 `queued/running` 任务一律改判 `failed`，且 `download_jobs.json` 每次保存都是整表覆盖，A 的任务被 B 一次保存抹掉 | `tiklocal/services/downloader.py::_load_history`、`DownloadHistoryStore.save` | 新增 `tiklocal/services/instance_lock.py`（Windows `msvcrt.locking` / POSIX `flock`，非阻塞），`run.py` 在 `create_app()` 之前取锁，失败打印原因并 `exit 1`；锁句柄挂在 `app.extensions['instance_lock']` 上随进程存活，崩溃由 OS 释放 |
| 2 | P1 | 数据目录被收回写权限后，任何 JSON 写入 / 缩略图生成会在 `tempfile` 里空转 2³¹-1 次（约 3–6 天 CPU）：`os.access(dir, W_OK)` 在 Windows 对被 ACL 拒绝的目录仍返回 True，`mkdtemp/mkstemp` 把 `PermissionError` 当成重名继续换名 | `tiklocal/services/json_storage.py:19`（同型 `thumbnail.py:318`、`radio_energy.py:79`、`url_list.py:123`） | 四处全部弃用 `tempfile`：临时名改为 `{name}.{pid}.{counter}.tmp`（缩略图为 `.thumb-{pid}-{n}.jpg`），让 `PermissionError` 立刻冒泡到已有的 503 分支；`thumbnail` 在 `finally` 里清理临时图与 `.dark` 备选 |
| 3 | P1 | JSON store「可写但不可读」时读取返回空默认值，下一次写入把整份数据覆盖，无错误、无备份（杀软/同步盘「允许写、不允许读」的共享模式即可触发） | `tiklocal/services/json_storage.py:82-99`（影响 `favorites.py`、`url_list.py`、`radio.py`、`downloader.py`） | 新增 `StoreUnreadableError(OSError)`：`read_json_file` 重试 5×50 ms 后抛错而不再返回默认值；因继承 `OSError`，路由既有的 `except OSError → 503` 直接生效。`UrlListStore.lines()` 同样只把 `FileNotFoundError` 当空 |
| 4 | P2 | 双开时日志轮转 WinError 32：两个 `RotatingFileHandler` 抢同一文件，轮转失败后 logging 打 `--- Logging error ---` 并丢记录，文件停在 1925 B 不再增长 | `tiklocal/services/logging_setup.py:89-94` | 由第 1 条的单实例锁消除常见场景（正常双开被拒）；服务管理器强杀后仍可能同进程重启，转 backlog |

## 二、数据与索引

| # | 级别 | 问题 | 位置 | 处置 |
|---|------|------|------|------|
| 5 | P2 | 数据库由更新版本写入后，旧代码静默继续运行：`migrate()` 从不检查 `schema_migrations` 里高于已知版本的行，若新版本改过表名，旧代码抛裸 `sqlite3.OperationalError: no such table: media_items` | `tiklocal/services/database.py:403-418` | 新增 `DatabaseVersionError`，迁移前比较 `MAX(version)` 与 `MIGRATIONS[-1].version`，更高则明确报「用更新的 TikLocal 或换 instance 目录」 |
| 6 | P3 | `config.yaml` 的 `download:` 段无任何读取入口：用户改了没反应，也没有日志说明 | `tiklocal/app.py:391`、`tiklocal/services/tiktok_downloader.py:511-514` | 转 backlog：需要先定「YAML 与 store 谁优先」的产品语义，避免两处配置互相覆盖 |

## 三、下载引擎与外部进程

| # | 级别 | 问题 | 位置 | 处置 |
|---|------|------|------|------|
| 7 | P2 | 管道解码依赖解释器 locale：`PYTHONUTF8=1` 下 yt-dlp 的 cp936 输出被替换成 U+FFFD，任务报 success 但 `output_files_rel` 是 `@default/��Ƶ/a.mp4`（第 12 轮把 `encoding` 改成固定 utf-8 引入的回归） | `tiklocal/services/downloader.py:1365-1370` | 管道改回字节模式，新增 `_decode_output(raw)`：按 `locale.getpreferredencoding(False)`、`utf-8`、`cp936`、`gbk` 依次尝试，全失败才 `errors='replace'` |
| 8 | P3 | 引擎非 0 退出码丢弃已落盘文件：磁盘上有文件、任务 failed 0 文件、无 source 记录，重试后库里出现 `clip (1).mp4` | `tiklocal/services/downloader.py:1074-1077` | 失败分支保留 `unmoved_outputs` 并标记 `partial`，已落盘产物照常入库 |
| 9 | P3 | `probe_binary` 把「找到二进制」当「可用」：显式配置但缺失的路径仍 `available: True, version: 'unknown'`，设置页显示「组件已就绪」，首个任务才以 WinError 2 失败 | `tiklocal/services/download_tools.py:199-207` | 绝对路径不存在直接返回 `None, ''` |
| 10 | P3 | 侧车 200 + `{"code":500,"data":null}` 被当空结果，作者同步报「该作者没有作品」 | `tiklocal/services/tiktok_downloader.py:344-347` | 检查 body `code`，非 0/200 且 `data` 为空时按错误上抛 |
| 11 | P3 | `sync_author_profiles` 兜底把 `str(exc)` 写进响应（第 13 轮只修了外层路由） | `tiklocal/services/tiktok_downloader.py:856-861` | 只有自家 `TikTokDownloaderError` 回显原文；`ValueError` 记日志并返回 `t('author.sync.error.unknown')` |

## 四、媒体与缩略图

| # | 级别 | 问题 | 位置 | 处置 |
|---|------|------|------|------|
| 12 | P2 | HDR 视频封面无色彩/传输函数转换：10-bit HDR10 样本的 PQ 码被当成 SDR 值，真实亮度 1.4/104/1019 nit 被压成 1.54× 的灰度差，瓦片与 `<video>` 播放明显不一致 | `tiklocal/services/thumbnail.py:380-392` | 探测源 `color_transfer` 属 `HDR_TRANSFERS`（`smpte2084`/`arib-std-b67`）时改用 `zscale=t=linear:npl=100 → tonemap=hable → zscale=bt709` 链；`_FILTER_SUPPORT` 探测一次，构建不含 tonemap 的 ffmpeg 不会每次白跑 |
| 13 | P3 | 片头黑场封面固定取 5.0 s：0–6 s 全黑的视频得到纯黑封面 | `tiklocal/services/thumbnail.py:371` | 新增 `_frame_luma()` 与 `BLACK_FRAME_LUMA = 6.0`：候选时间点逐个测亮度，低于阈值先存 `.dark` 备选继续试，全黑时回退最亮的备选 |
| 14 | P3 | 图片缩略图丢弃 ICC profile：P3/AdobeRGB 照片在网格里过饱和，与 `/media` 原图不一致 | `tiklocal/services/thumbnail.py:417-418` | 保存时带上 `icc_profile=source.info['icc_profile']` |
| 15 | P3 | 透明 PNG/GIF 透明区变纯黑：P 模式 `getbands()` 只有 `('P',)`，走 `convert('RGB')` 把透明索引映射到调色板黑 | `tiklocal/services/thumbnail.py:411-417` | `getbands()` 含 `A` 或 `info` 含 `transparency` 时按 RGBA 合成到 `(247,246,242)` 底色再存 |
| 16 | P3 | 只按 mtime 判新鲜：同 mtime 替换内容后封面与入库宽高永久陈旧 | `tiklocal/services/thumbnail.py:146`、`library_index.py:219` | 转 backlog：需要内容指纹（size+hash）列与回填迁移，影响面覆盖缩略图与索引两处 |

## 五、多标签与前端一致性

| # | 级别 | 问题 | 位置 | 处置 |
|---|------|------|------|------|
| 17 | P2 | 收藏按钮在两个标签下执行相反动作：详情页只在加载时读一次 `isFavorited`，点击时盲 `toggle` | `tiklocal/static/detail_page_controller.js:88`、`tiklocal/static/flow_actions_shared.js:29`、`tiklocal/static/radio_controller.js:425` | 后端 `POST /api/favorite/<name>` 支持 `?favorited=true/false`（或 JSON 体）走 `FavoriteService.set_favorite` 幂等设置；前端一律传目标态；详情页在 `visibilitychange` 时重读状态 |
| 18 | P3 | 列表/详情页不重校验：另一标签删除后卡片仍在，点开 404 | `tiklocal/static/library_page_controller.js` | 详情页已随第 17 条在可见性变化时重读；列表页整体重校验转 backlog（要避免滚动位置丢失） |
| 19 | P3 | 下载无 URL 幂等：同一链接在两个标签各提交一次 → 两条任务、两份文件 | `tiklocal/services/downloader.py::enqueue` | 转 backlog：需要产品语义（「重复即合并」还是「允许重下」） |
| 20 | P3 | 跨标签数据变更无通知：收藏/删除/集合在别的标签发生，本标签只有主题变更会同步 | `tiklocal/static/base.js:27-33` | 转 backlog：`BroadcastChannel` 广播需要逐个页面接事件，单独立一轮 |

## 六、验证

- 新增 `tests/test_round16_regressions.py`（6 个用例，全过）：
  - `test_write_denied_directory_fails_fast_and_keeps_the_old_file`：`Path.write_text` 抛 `PermissionError` 时 `write_json_atomic` 在 5 s 内抛错、旧文件内容不变、不残留 `.tmp`；
  - `test_unreadable_store_raises_instead_of_answering_empty`：持久 `PermissionError` 下 `read_json_file` 抛 `StoreUnreadableError`，文件字节未动；
  - `test_missing_store_still_reads_as_the_default`：文件不存在仍是默认值；
  - `test_url_list_unreadable_file_is_not_treated_as_empty`、`test_url_list_write_denied_directory_raises`：URL 队列同样的契约；
  - `test_output_decoding_prefers_the_console_locale`：cp936 字节按 locale 解码还原中文路径。
- `tests/test_json_storage.py` 的共享冲突用例改为新契约（重试耗尽 → 抛错，不再返回默认值）。
- `thumbnail._frame_luma` 改用 `tobytes()`，消掉 Pillow 14 的 `getdata` 弃用告警。
- 全量 `pytest -q` = **462 passed / 6 skipped / 0 failed**（190.57 s）。

## 七、转 backlog

1. **缩略图内容指纹（第 16 条）**：`thumbnail` 与 `library_index` 两处新鲜度判定都要改，需 `content_hash` 列 + 回填。
2. **下载 URL 幂等（第 19 条）**：等产品语义确定。
3. **跨标签数据广播（第 20 条）**：`BroadcastChannel` + 每页订阅，单独立轮。
4. **列表页可见性重校验（第 18 条）**：需保留滚动位置与筛选状态。
5. **`config.yaml` 的 download 段读取入口（第 6 条）**：先定 YAML 与 store 的优先级。
6. **强杀后同进程日志轮转（第 4 条）**：单实例锁只挡正常双开；服务管理器 kill + 立即重启仍会撞 WinError 32，需按 pid 命名日志文件。
