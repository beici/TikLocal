# 第 22 轮审查报告（持久化存储层 / 数据库与索引 / 重 IO 与外部进程 / 路由层 / 电台与装配 / 前端 feed 与详情 / 前端基础层）

日期：2026-10-29
起点基线：591P/7S/0F（第 21 轮结束后，307.71 s）
审查域：持久化存储层与认证存储（store）、数据库与索引（index）、重 IO 与外部进程（io）、路由层（routes）、电台/推荐/文案/应用装配（radio）、前端 feed/详情/图库（feed）、前端基础层（base）

> 说明：7 个域全部交付报告，共 68 条：P0×0 / P1×3 / P2×30 / P3×35；独立复审又提出 9 条（1 条阻塞 / 3 条指出本轮修复本身有问题 / 5 条新 P3）。本轮修 3 条 P1 + 7 条 P2 + 8 条 P3 = 18 条，其余 54 条转 backlog（P2×23 / P3×31）。复审的 1 条 P3 建议（collections 读路由补 503）经核实**不可达**（`CollectionStore._read(strict=False)` 已经吞掉所有异常），未采纳，理由写进 §十三；另有 4 条是对本轮修复本身的纠正（`neighbors()` 漏活照片、`_load()` 的 `exists()` 前置判断、`_prune()` 会清掉仍在生效的限流块、`chmod` 时序），已在本轮内改正。
>
> 本轮视角按 §七 轮换为**数据完整性与持久化**、**并发与资源泄漏**、**性能**——前两轮的主轴是「新代码」和「安全边界」，这两类缺陷不在那个视角的射程内。因此本轮大量发现集中在「写完没写完」「锁拿没拿」「全表读没读」上，且多数不是最近引入的，而是长期存在、只是没人在这个视角下看过。
>
> 量化说明：域 B 引用的是仓库自带注释（`recommendation.py:13` 的「0.2s on 8000 files」、`library_index.py:260` 的「16000 rows and ~400ms on an 8000-file library」），不是本轮新测的数字。

## 一、持久化存储层与认证存储（4×P2 + 5×P3）

| # | 级别 | 问题 | 位置 | 处置 |
|---|------|------|------|------|
| 1 | P2 | `pull()` 是唯一不加锁的读-改-写：`_write(backup=True)` 先 `store_path.replace(.bak)` 再写新文件，窗口内 `add()` 的 `lines()` 读到 `FileNotFoundError` → 返回 `[]` → 合并结果只剩本次新增，两请求都 200，一方改动静默消失（只有 `.bak` 留底）。`push()`/`replace()`/`add()`/`add_reporting()` 都持 `_LIST_LOCK`，只有 `pull()` 裸调 | `tiklocal/services/url_list.py:241-262` | 已修：`pull()` 的 `_write` 移进 `with _LIST_LOCK:`。复审又指出备份本身用「改名」仍留一个读者看不到文件的窗口，改为 `shutil.copyfile`（拷贝而非改名），窗口彻底消失 |
| 2 | P2 | `AuthStore._save()` 用固定 `.tmp` 名 + 裸 `os.replace`（无重试、无 finally 清理），调用方只捕 `ValueError`。Windows 上目标文件被 Defender/索引服务持有（不含 `FILE_SHARE_DELETE`）→ `PermissionError` 逃到全局 500 处理器，用户看到「内部错误」而非 503，且 `auth.json.tmp`（含 `password_hash` 与 `secret_key` 明文 JSON）永久残留。同文件的 `_load()` 把任何 `OSError` 转 `RuntimeError`，而 `is_authenticated()` 每请求都读 `auth_store.revision` → 占用窗口内**所有**请求 500 | `tiklocal/services/auth.py:32-71`、`tiklocal/web/settings.py:85-99` | 已修：`_save` 改用 `json_storage.write_json_atomic`（唯一临时名 + 8 次退避重试 + finally unlink），`_load` 在 OSError 时退回上次成功缓存，路由侧补 `except OSError → 503`。复审改正两处：①`chmod` 原本在 `replace` 之后，中间文件是 umask 默认 0644 → 给 `write_json_atomic` 加 `mode` 参数、在临时文件上 chmod 后再 replace；②`_load` 的 `Path.exists()` 前置判断会吞掉共享冲突（`exists()` 内部 `os.stat` 失败即返回 False），使缓存回退分支几乎不可达 → 删掉该判断，改 `except FileNotFoundError` 单独分支 |
| 3 | P2 | 三个 collections 写路由对存储 `OSError` 无处理 → 500，而同一文件的 POST `/api/collections` 早已是 503（`tests/test_collections.py:148` 只覆盖 POST） | `tiklocal/web/library.py:471-475`、`:481-492`、`:517-523` | 已修：三处补 `except OSError → 503 common.error.storage_unavailable` |
| 4 | P2 | 登录限流 `defaultdict(deque)` 的键永不回收：`retry_after()` 每个请求都调 `_active(key)`，读即建键，只有成功才 `clear()` pop。默认 `host=0.0.0.0`，暴露公网时扫描器从大量源 IP POST `/login`（以及未认证的 `/api/v1/pair`，共用同一 limiter）即单调增长 | `tiklocal/auth.py:16-43` | 已修：`retry_after` 改 `_active(key, create=False)` 不建键；新增 `MAX_KEYS=1024` + `_prune()`（只淘汰窗口已过的桶，表满且全是生效块时不记录新失败）。**注意**：不能在 `_active` 里 pop——`record_failure` 会追加到已脱离 dict 的 deque，计数清零、限流可绕过。复审指出初版按「最久未失败」淘汰会删掉仍在生效的桶（等于解除限流），已改为只淘汰过期桶 |
| 5 | P3 | 临时文件名计数器非原子：`global _TEMP_COUNTER; _TEMP_COUNTER += 1`（LOAD_GLOBAL/BINARY_ADD/STORE_GLOBAL 间可切换线程）→ 两线程取到同名临时文件，后者 `os.replace` 抛 `FileNotFoundError`（`json_storage.py:51-59` 只 `except PermissionError`）。`tests/test_json_storage.py:63-89` 已有 6 线程×40 轮并发写测试，说明这是活跃并发面。同款写法在 `url_list.py`、`radio_energy.py`、`thumbnail.py` 各一份 | `tiklocal/services/json_storage.py:33-34`、`url_list.py:132-134`、`radio_energy.py`、`thumbnail.py:403-405` | 已修：四处统一 `_TEMP_COUNTER = itertools.count(1)` + `next(...)` |
| 6 | P3 | `DeviceAuthStore` 仍是固定 `.tmp` 名 + 裸 `os.replace`；`tiklocal auth set-password`（`run.py:432-453` 自建 `AuthStore(get_auth_path())`）不持实例锁，服务运行中执行该 CLI 会与进程争同一临时名 | `tiklocal/services/device_auth.py:148`、`tiklocal/run.py:432-453` | 转 backlog（AuthStore 已改，DeviceAuthStore 同一改法留待下一轮；CLI 加锁需要动实例锁语义） |
| 7 | P3 | `DeviceAuthStore.verify()` 每个 `/api/v1/*` 请求整份读盘解析（无 mtime 缓存，`AuthStore` 有）；`/api/v1/radio/media` 一首歌多个 Range 请求 → 每请求一次文件读 + JSON 解析（文件仅数 KB，影响小） | `tiklocal/services/device_auth.py:55`、`:132-144` | 转 backlog |
| 8 | P3 | `CollectionStore._read()` 把任何 `Exception`（含 OSError）当损坏返回空 payload，与 `json_storage.py:117-139` 的契约相反：瞬时独占时 `/api/collections` 返回空列表、`/favorite` 与 `/library` 的合集卡片短暂消失（不丢数据，写路径都 `strict=True`） | `tiklocal/services/collections.py:83-94` | 转 backlog（应只隔离 `JSONDecodeError`/`UnicodeDecodeError`）。复审建议「读路由也补 503」，经核实**不可达**——`strict=False` 时这个 `except Exception` 已经吞掉读失败，读路由永远拿不到 OSError，故未加（见 §十三） |
| 9 | P3 | 集合摘要每张卡片整份读 `collections.json`：`list_for_media(include_items=False)` 仍触发 `list_item_uris()`，`_build_library_page(collection_id=...)` 再一次；`/api/collections` GET 已改用 `list_with_items()` | `tiklocal/web/library.py:128`、`:169-173`、`:531-537` | 转 backlog |

## 二、数据库与索引（1×P1 + 6×P2 + 4×P3）

| # | 级别 | 问题 | 位置 | 处置 |
|---|------|------|------|------|
| 10 | P1 | `records()` 无投影、无 LIMIT，`SELECT *` + `fetchall()` + 逐行转 dict，压在两条最热路径上：①`/detail/<name>` 与 `/image?uri=` 每次渲染都物化整类媒体，只为取前后各一条；②`/flow` 一次请求对 video、image 各调一次 `records()`，而 `CANDIDATE_TTL_SEC = 2.0` 让缓存几乎每请求失效。成本随库线性增长，waitress 线程被占满 | `tiklocal/services/library_index.py:249-254`；调用点 `tiklocal/web/media.py:135-143`、`tiklocal/services/recommendation.py:64-71` | 已修：新增 `MediaIndexStore.neighbors(media_type, name)`，两次键集查询取代整类物化（详情页侧） |
| 11 | P2 | 尺寸探针在退避条目 >400 时活锁：`skip` 被 `[:400]` 硬截断，被截断的尾部每轮重新被 `unprobed()` 返回、再 `_defer_probe()`，`results` 仍空 → 每轮空转 5 s 并跑一次 400 项 `NOT IN` 查询；同时 `_warned` 在超 500 时整体 `clear()`，同一批 URI 反复命中 WARNING → 数万条/轮冲掉轮转日志。触发条件：无 ffprobe 也无 ffmpeg 且视频 >400，或 >400 个损坏/占用视频 | `tiklocal/services/library_index.py:1090-1093`、`:1110`、`:1138-1141`、`:1154` | 转 backlog |
| 12 | P2 | `timeline_months` N+1：每个月份一次 `SELECT *` ×120 行，`/library` 首屏 10 条查询、`/api/library/timeline` 最多 38 条、物化 4320 行×23 列后只采样 ≤18 条 | `tiklocal/services/library_index.py:479-504` | 转 backlog（同文件 `on_days` 已有 `ROW_NUMBER()` 写法可循） |
| 13 | P2 | `image_random` 每页全量取回并全量洗牌：`SELECT media_items.*`（无 LIMIT）→ `fetchall()` → `shuffle` → 切片；15k 图即每页 15k 行物化，`total=len(rows)` 也依赖全量 | `tiklocal/services/library_index.py:407-411`、`tiklocal/web/library.py:35-45` | 转 backlog（域 D 的 #32 是同一处） |
| 14 | P2 | `time_states()` 在每次 `register_uris` 全表读（无 WHERE，19k 库 ≈ 20k 行 + 20k dict），且整段在 `_sync_lock` 内与扫描串行；调用方是每个下载 job 完成回调 | `tiklocal/services/library_index.py:586-596`、`:899-903`、`tiklocal/app.py:498` | 转 backlog（`existing_uris`/`profiles_for` 已有分块写法） |
| 15 | P2 | `sync()` 与 `register_uris()` 对同一把锁语义相反：前者 `acquire(blocking=False)` 返回 busy，后者 `with self._sync_lock:` 阻塞 → 回收站恢复请求、下载回调会等满整轮扫描（19k 冷索引注释 8.19 s） | `tiklocal/services/library_index.py:853`、`:899`、`tiklocal/web/media.py:303` | 转 backlog（改 `acquire(timeout=…)`，超时走「下次扫描自愈」分支） |
| 16 | P2 | 全量索引在监听端口前/请求线程内同步跑：`create_app()` 里 `library_indexer.sync()`，同一函数还被 `POST /api/library/sync` 与设置页在请求线程直接调用；backlog 记的位置（`library.py` 构造器）已过时 | `tiklocal/app.py:428`、`tiklocal/web/library.py:395`、`tiklocal/web/settings.py:174` | 转 backlog（需后台线程 + 进度接口，`MediaProbeWorker.start()/wake()` 已有现成模式） |
| 17 | P3 | `page()` 每页跑两次全表条件扫描（`COUNT(*)` + `LIMIT/OFFSET`），`media_type IN (...)` 无索引前缀，两次都逐行走 `idx_media_items_mtime_uri` 再回表过滤 | `tiklocal/services/library_index.py:413-417`、`:658-665` | 转 backlog（`has_more` 用 `LIMIT limit+1`，`total` 只在 offset=0 时取） |
| 18 | P3 | `blog_rows()` N+1：`GROUP BY source_id` 后对每个 source 单独 `SELECT uri ... LIMIT 1`；每次打开博主切换器都跑 | `tiklocal/services/library_index.py:566-576`、`tiklocal/web/library.py:387` | 转 backlog |
| 19 | P3 | `_record_many_once` 每批印象事件都全表 prune（`DELETE ... NOT IN (子查询)`），表未满 20k 时也全扫子查询且持写锁，每批（≤50 条）触发 | `tiklocal/services/database.py:548-557` | 转 backlog |
| 20 | P3 | backlog 里「`skipped_prefixes` 没回给前端」需要修正口径：后端已写入结果、`POST /api/library/sync` 直接返回整个 result、`tests/test_round07_regressions.py:71` 已断言；`static/*.js` 无任何读取 → 后端与 API 已闭环，剩余缺口纯在前端提示 | `tiklocal/services/library_index.py:889-890`、`tiklocal/web/library.py:401` | 转 backlog（降级为纯前端事项） |

## 三、重 IO 与外部进程（4×P2 + 5×P3）

| # | 级别 | 问题 | 位置 | 处置 |
|---|------|------|------|------|
| 21 | P2 | 取消任务丢弃已下载产物：取消检查命中时 `output_rel_list` 已含落库文件，但直接 `_mark_canceled_locked` 返回，`_record_job_sources_on_success`/`on_outputs` 全部跳过 → 文件在库根，任务显示「canceled / 0 文件」，索引与来源映射都缺，直到手动扫描。触发：多文件任务（gallery-dl 相册、direct 多 URL、播放列表）下完第 1 个后点取消 | `tiklocal/services/downloader.py:972-984` | 已修：取消分支按「部分成功」记录 `output_files_rel/file_count/output_path_rel/partial` 并调用 `on_outputs`，与失败分支同构 |
| 22 | P2 | 请求线程里同步起 `--version` 子进程且无缓存：入队一次 `probe_binary(engine)[1]`，`probe_dependencies()` 连跑 3 次，`for timeout in (15, 20)` 失败还加长重试 → 正常机器 0.3-1.5 s/次，工具卡住时设置页最坏 105 s，占死 waitress 固定线程 | `tiklocal/services/downloader.py:580`、`:496-498`、`tiklocal/services/download_tools.py:195` | 转 backlog（模块级 memo + TTL） |
| 23 | P2 | 视频封面固定多起一次 ffprobe：`_probe_color_transfer(video_path)` 无按文件缓存（只有全局 `_FILTER_SUPPORT`）；正常视频 2 个进程，黑场/暗场最多 1+3 个，全程持 `_GENERATE_SLOTS` | `tiklocal/services/thumbnail.py:521`、`:535-573` | 转 backlog（与 duration 合并成一次 ffprobe，按 `(path, st_mtime_ns)` 缓存）。注：backlog 里「先起一次 ffmpeg 读 Duration」与 `/thumb` 实际路径不符（`auto_timestamp` 才探时长） |
| 24 | P2 | 源被移出配置后回收站条目永久滞留：`entries()` 只遍历 `self.library.sources`，`expired()` 由 `entries()` 派生 → 删除/改路径一个源后，该源 `.tiklocal-trash` 下文件不再列出、`restore` 抛 `KeyError`、30 天保留期永不生效（磁盘永不回收）。backlog 只记了 KeyError，漏了「永不过期」这一半 | `tiklocal/services/trash.py:89`、`:102`、`:108` | 转 backlog（`expired()` 需同时扫磁盘上已无对应 source 的 trash 目录） |
| 25 | P3 | 每个任务结束都无条件跑一次 `taskkill`：`_kill_process_tree_windows(process.pid)` 不在 `poll() is None` 保护内，而 `_run_job` 的 finally 对每个 job（含 success）都调 `_terminate_process` → 每次下载多起一个 `taskkill.exe`（约 40 ms），取消/超时路径 2-3 次，并对已退出 pid 做 `/T /F` 树杀 | `tiklocal/services/downloader.py:1450-1455`、`:1061-1065` | 转 backlog |
| 26 | P3 | 缩略图临时文件名可能撞车 → 发布错误封面：计数器非原子（见 #5），而锁池按 `hash(str(output)) % 8` 分桶，不同 uri 可并发进同一段 → 两线程同名 `.thumb-<pid>-N.jpg`，先 `_replace_with_retry` 的一方把另一方的帧发布成自己的封面（永久错误封面，直到清缓存） | `tiklocal/services/thumbnail.py:392`、`:403-405` | 已修（#5 的计数器原子化即消除撞名） |
| 27 | P3 | `output.stat()` 落在 try 之外 → 清缓存并发时 `/thumb` 500：`if not overwrite and output.is_file() and output.stat().st_size > 0:` 的第二次 stat 在 try 之前，`clear_cache()` 并发 unlink 抛 `FileNotFoundError`，一路逃到 `web/media.py:508`（无 try/except）→ 500 破图 | `tiklocal/services/thumbnail.py:395-400` | 已修：两段 stat 合并进 `try/except OSError` |
| 28 | P3 | 子进程未加 `CREATE_NO_WINDOW`（全仓 grep 无 `creationflags`）：从 pythonw/快捷方式（无控制台宿主）启动时，每次 yt-dlp、ffmpeg、`--version`、`taskkill` 都会弹控制台窗口；批量生成缩略图时持续闪窗 | `tiklocal/services/downloader.py:1432`、`download_tools.py:194`、`thumbnail.py:462`/`480`/`505`/`550` | 转 backlog（需逐站点验证，且终端启动时不可见、难自动测） |
| 29 | P3 | 来源映射每次读取都整文件重解析：`_normalized_payload()` 无缓存，`get_many` 在 `self._lock` 内 open + `json.load` 全量文件；`/flow` 每渲染一次执行一次，条目随下载量线性增长 | `tiklocal/services/download_sources.py:169`、`:198-210`、`tiklocal/web/flow.py:35` | 转 backlog（进程内缓存 + mtime 校验） |

## 四、路由层（6×P2 + 5×P3）

| # | 级别 | 问题 | 位置 | 处置 |
|---|------|------|------|------|
| 30 | P2 | 索引损坏时 `/settings/` 整页 500：`library_stats()` 对 `sqlite3.DatabaseError` 显式 `raise`（注释「报 0 会掩盖真故障」），`settings_view` 直接调用 → 改目录的**唯一入口**打不开；同一页的 `/api/library/sources` 却专门兜住（注释「A damaged index must not hide the folder list」），自相矛盾 | `tiklocal/web/settings.py:32-40`、`:242-245`、`:193-201` | 转 backlog（stats 失败回退 `index_corrupt` 标志照常渲染 + 页面提示条，API 保持 500） |
| 31 | P2 | 来源变更后索引状态不一致：persist→apply→MEDIA_ROOT→缩略图→下载器全部生效后 `sync()` 只捕 locked/busy 的 `OperationalError`，其余 `raise` → 500 且无回滚；busy 分支 `sync()` 返回 `{'busy': True}` → 被移除来源的 `media_items` 行不清，`page()` 不过滤 source → 幽灵行出现在 `/library`，点开 404。全仓仅三处触发 sync，无周期重扫 | `tiklocal/web/settings.py:157-187`、`tiklocal/services/library_index.py:850-864` | 转 backlog |
| 32 | P2 | （与 #13 同一处）`image_random` 每页全量取回 + 全量洗牌 | `tiklocal/services/library_index.py:407-411`、`tiklocal/web/library.py:35-45` | 转 backlog，与 #13 合并处理 |
| 33 | P2 | （与 #10 同一处）`neighbors()` 用 `records()` 全表物化 | `tiklocal/web/media.py:135-143` | 已随 #10 修复 |
| 34 | P2 | 作者来源列表只为数个数就全量读：`'local_count': len(local_work_names(media_index, source_id))`，而 `local_work_names` 全量 `records(source_id=...)`；每源还 `resolve_profile` + `load_profile` 两次读文件 | `tiklocal/web/author.py:369`、`:104-115`、`:360`、`:367` | 转 backlog |
| 35 | P2 | 作者页无分页：`tiles = build_tiles(media_index.records(source_id=..., album_covers_only=True))`，模板裸 `{% for tile in tiles %}` | `tiklocal/web/author.py:201`、`tiklocal/templates/author.html:51` | 转 backlog |
| 36 | P3 | 来源读-改-写无锁：两个标签页并发加目录，写者整文件覆盖 | `tiklocal/web/settings.py:130-145` | 转 backlog |
| 37 | P3 | 默认来源改路径时只迁移缩略图缓存身份，`@default/rel` 形态的收藏/合集/播放进度/回收站条目全部改指新目录，同名文件静默错配 | `tiklocal/web/settings.py:163-166` | 转 backlog |
| 38 | P3 | `set_profile_url` 无 try（磁盘失败 500），而同文件 `_sync_profile` 捕 Exception → 502 | `tiklocal/web/author.py:299`、`:314`、`:228-277` | 转 backlog |
| 39 | P3 | `/api/media/info` 把服务器绝对路径回给浏览器（前端展示 + 「复制路径」），与同文件 `/delete` 注释「`str(e)` 会泄漏绝对路径」政策相反；默认 `AUTH_ENABLED=False` + `host=0.0.0.0` 时局域网无凭据可读 | `tiklocal/web/media.py:483`、`tiklocal/static/flow_page_controller.js:637`、`:663` | 转 backlog |
| 40 | P3 | 搜索：命中来自文件名/目录名时仍挂 `&caption=`（`flow.py` 收窄序列为空会回落推荐流）；general tab 每页都算 users 且第 2 页起客户端丢弃 | `tiklocal/web/search.py:213-215`、`:324-333`、`tiklocal/static/search_page_controller.js:390-393` | 转 backlog |

## 五、电台 / 推荐 / 文案 / 应用装配（5×P2 + 5×P3）

| # | 级别 | 问题 | 位置 | 处置 |
|---|------|------|------|------|
| 41 | P2 | 电台列表冷启动逐条探测且**声称的上限从未实现**：`grep MAX_PROBE` 只命中 `docs/audit/backlog.md:26` 与 `docs/audit/round-04.md:81`，`git log -S` 无提交——`limit` 可到 500，`metadata_for_many` 用 8 线程逐条跑 ffprobe（失败再落 `banner_duration` 的 `timeout=10`），最坏 200/8×10 s ≈ 250 s 占满 waitress；`download_tools.py` 只取 ffmpeg 不取 ffprobe，导致每条先 spawn 一个注定 `FileNotFoundError` 的 ffprobe | `tiklocal/web/radio.py:79-83`、`tiklocal/services/radio.py:250-259`、`:264`、`:296`、`tiklocal/services/thumbnail.py:109-125` | 转 backlog（这是本轮 P2 里最该先做的一条：文档承诺了一个不存在的上限） |
| 42 | P2 | `collect_candidates()` 无缓存：每次换台全扫 `records(media_type='audio')` + 每条 `resolve_path()`（含 2 次 `Path.resolve()`）+ `is_file()`；同仓 `recommendation.py:79-87` 早有 `CANDIDATE_TTL_SEC=2.0` 的同款缓存 | `tiklocal/services/radio.py:202-227`、`tiklocal/static/radio_controller.js:282-290` | 转 backlog |
| 43 | P2 | 能量包络无同 URI 去重/无锁/无负缓存：并发 N 个请求 = N 个 ffmpeg 全曲解码；`worker.join(60)` 超时分支 `_kill_process` 后**不 close `process.stdout`**（句柄泄漏）；`_write_cache` 把 OSError 全吞 | `tiklocal/services/radio_energy.py:49-63`、`:155-188`、`:81-96` | 转 backlog |
| 44 | P2 | caption 收窄路径每页全库读 + 双次 collapse：无 `source_id` 时 `records = media_index.records()`（全库），随后无条件 `collapse_media_entries(...)`，命中收窄序列后再 collapse 一次才切片，前一次结果在命中时被整体丢弃 | `tiklocal/web/flow.py:383-393`、`:403`、`:406-407`、`:413-419` | 转 backlog |
| 45 | P2 | `getaddrinfo` 被调 N+1 次：`candidates.extend(socket.getaddrinfo(host, None)[i][4][0] for i in range(len(socket.getaddrinfo(host, None))))`——每次解析先算长度再逐个索引，等于 1+2N 次解析；调用点是「保存配置」与每次文案生成 | `tiklocal/services/captions.py:166-170`、`:203`、`:371` | 已修：改为一次 `getaddrinfo` 后遍历结果 |
| 46 | P3 | 来源映射每次整文件重解析（与 #29 同一处） | `tiklocal/web/flow.py:27-35`、`tiklocal/services/download_sources.py:198-210` | 转 backlog，与 #29 合并 |
| 47 | P3 | TLS 续期阈值固定 30 天而 `days` 允许 1..3650 → `--tls-days ≤ 30` 每次启动都重生成、指纹每次变（`OPENSSL_TIMEOUT_SECONDS=120`） | `tiklocal/services/tls.py:424`、`:49`、`:379-380` | 转 backlog（建议 `min(RENEW_BEFORE_DAYS, days // 3)`；与第 21 轮 A3 同一条） |
| 48 | P3 | 证书与私钥两次 `os.replace` 非成对原子；第二次失败 → 本次启动 `TlsError` 退出，下次 `validate_certificate_pair` 失败即重生成（能自愈，仅文档措辞过强） | `tiklocal/services/tls.py:494-495`、`:420` | 转 backlog（与第 21 轮 A4 同一条） |
| 49 | P3 | 电台 `_metadata_cache` 无上限无淘汰（`str(path)` 为键，mtime 变了不删旧键） | `tiklocal/services/radio.py:146`、`:241-248` | 转 backlog |
| 50 | P3 | `_select_favorites` 在列表推导条件里每轮重建 `{chosen.name for chosen in selected}` → O(n×limit) | `tiklocal/services/radio.py:307-336` | 转 backlog |

## 六、前端 feed / 详情 / 图库（1×P1 + 2×P2 + 7×P3）

| # | 级别 | 问题 | 位置 | 处置 |
|---|------|------|------|------|
| 51 | P1 | 视频详情页删除按钮 100% 抛错：模板 `onclick="confirmDelete()"`，而该函数体用的 `fileName` 只声明在 `DOMContentLoaded` 回调作用域内 → `ReferenceError: fileName is not defined`，`TikLocalUI.confirm` 从未执行，删除入口完全不可用（图片详情页用顶层 boot 数据故正常）。测试零覆盖 | `tiklocal/templates/detail.html:67`、`tiklocal/static/detail_page_controller.js:279-288`、`:22` | 已修：改读 `detailBoot.file`（模板注入的 boot 数据），并在注释里写明作用域原因 |
| 52 | P2 | 媒体弹层无请求令牌 + 键盘穿透（同一根因）：`showInfoSheet` 捕获 `item`/`display` 后异步取 `/api/media/info`，落地只判 `mediaSheetIsOpen()`，不判「当前展示的还是不是这一件」→ 作品 A 的响应把 path/尺寸/日期写进正在展示 B 的弹层，「复制路径」复制到上一件；`:2302` 的全局 keydown 前置条件缺 `mediaSheetIsOpen()` → 弹层打开时 ArrowDown/Up/Space 仍在驱动底层 feed | `tiklocal/static/flow_page_controller.js:613-673`、`:2302` | 转 backlog（`sheetToken` + 落地校验 `currentItem() !== item`） |
| 53 | P2 | 相册首帧在 build 阶段 eager 拉原图（对比普通图片走 `loading='lazy'`） | `tiklocal/static/flow_page_controller.js:1336-1344`、`:1474`、`:1577-1592` | 转 backlog |
| 54 | P3 | 媒体弹层无焦点归还、Escape 只在弹层内有效（监听挂在 `#media-sheet` 自身，点视频区后焦点离开即失效，document 级 handler 无 Escape 分支），`role="dialog" aria-modal="true"` 却无焦点陷阱 | `tiklocal/static/flow_page_controller.js:560-577`、`:2249-2254`、`:2291-2330` | 转 backlog |
| 55 | P3 | 「全部下载」用 N 个 `setTimeout` 触发 `<a download>`，全部丢失 user activation → Chrome 弹「允许多个文件下载」许可，拒绝时只落第一个；重复点击无 busy 门 | `tiklocal/static/flow_page_controller.js:596-604` | 转 backlog |
| 56 | P3 | Quick Viewer 的 `play()` 竞态把暂停图标画到新项 | `tiklocal/static/library_page_controller.js:816-818` | 转 backlog |
| 57 | P3 | 前端缓存只增不清：`captionCache`/`sourceState`/`collection_picker.cache` | `tiklocal/static/flow_media_actions_controller.js:19-26`、`tiklocal/static/collection_picker.js:79-91` | 转 backlog |
| 58 | P3 | 瀑布流只增不减、`dropHead` 在 library 从未调用 | `tiklocal/static/library_page_controller.js:440-486` | 转 backlog |
| 59 | P3 | 时间线滚动无节流且每次读 `offsetTop` | `tiklocal/static/library_timeline_controller.js:374-376` | 转 backlog |
| 60 | P3 | `collection_picker.open()` 无陈旧守卫 | `tiklocal/static/collection_picker.js:148-169` | 转 backlog |

## 七、前端基础层（1×P1 + 3×P2 + 4×P3）

| # | 级别 | 问题 | 位置 | 处置 |
|---|------|------|------|------|
| 61 | P1 | 下载页有任务在跑时每 2.2 s 整体重建历史列表：`jobsSignature` 含 `progress_percent`，任一 job 进度变化就不走早退，`renderJobs()` 无条件 `renderHistoryPage()` → `#history-list` 的 innerHTML 整体替换 + 全文档 `feather.replace()`。后果：焦点若在历史列表内被销毁、键盘用户每 2.2 s 被踢回页首；`<details>` 展开的错误详情被折叠；容器 `aria-live="polite"` 让读屏每轮重播整份历史 | `tiklocal/static/download_page_controller.js:355-360`、`:362-389`、`:398-414`、`tiklocal/templates/download.html:94`、`:320` | 已修：历史列表单独算 signature（不含 `progress_percent`），未变则跳过重建 |
| 62 | P2 | `refreshJobs` 无请求代际号：轮询响应后到会覆盖删除/取消结果，被删 job 回到列表（最长 2.2 s，无活动任务时 12 s 后自愈）。同仓 `search_page_controller.js` 已有 `requestSeq` 范式 | `tiklocal/static/download_page_controller.js:579-604` | 转 backlog |
| 63 | P2 | 设置页作者同步走裸 `fetch`，无超时兜底 → 服务端抓取接口长时间不返回时 `runButton` 的 finally 永不执行，该行按钮永久 `disabled`、文案停在「同步中」，`updateAllAuthorVideos` 整批卡在第一个作者。同文件其它请求都有 `AbortSignal.timeout`（20 s / 30 s / 120 s），唯独最慢的抓取型接口没有 | `tiklocal/static/settings_page_controller.js:940-948`、`:211-216`、`:1123`、`:90-106` | 已修：`signal: AbortSignal.timeout(60000)` |
| 64 | P2 | 电台页 `localStorage` 裸读写：模块作用域 `getItem` 抛错（存储被禁/配额满）→ `init()` 从不运行，整页哑；`setItem` 抛错 → 状态已改但 UI 与场景停在旧值且再点也回不来。同文件 `readJson()` 与 `base.js` 都有 try/catch | `tiklocal/static/radio_controller.js:71`、`:92`、`:98`、`:658-662`、`:787-793`、`:1087-1094` | 转 backlog |
| 65 | P3 | 睡眠定时器每秒重建 `btnSleep.innerHTML` 且输出恒定（文案取 `SLEEP_OPTIONS[sleepIndex]` 而非剩余时间），每秒一次 `getElementById` | `tiklocal/static/radio_controller.js:510-553` | 转 backlog（用单个 `setTimeout(deadline)`，或渲染剩余秒数） |
| 66 | P3 | 下载页 Escape 双触发：确认弹窗的 `onKeydown` 未 `stopPropagation` → 关闭弹窗的同时退出多选、清空已选（同文件 `chooseHistoryDeleteMode` 有 `stopPropagation` 并注释说明） | `tiklocal/static/download_page_controller.js:724`、`:1072-1074`、`:507-513` | 已修：加 `stopPropagation()` |
| 67 | P3 | 搜索 tab 的 ARIA 不完整：面板 `role="tabpanel"` 缺 `aria-labelledby`，三个 tab 无 `id`（补之前必须先加 id）；键盘只映射左右箭头，无 Home/End | `tiklocal/templates/search.html:43-57`、`tiklocal/static/search_page_controller.js:568-577` | 转 backlog |
| 68 | P3 | `playback_resume.js` 后台仍累计观看时长（`timeupdate` 采样无 `document.hidden` 判定）→ 服务端 `visible_ms` 虚高 | `tiklocal/static/playback_resume.js:47`、`:84-88` | 转 backlog |

## 八、本轮修复取舍

P0 = 0，P1 = 3，P2 = 30，P3 = 35（域报告）；独立复审再加 9 条（1 阻塞 / 3 条纠正 / 5 条新 P3）。合计修 18 条。

- **P1 三条全部当轮修**：#10（`records()` 全表物化压在详情页与 `/flow`）、#51（详情页删除按钮 ReferenceError）、#61（下载页每 2.2 s 重建历史列表）。三条都是「最常见操作直接坏掉」的量级：#51 是功能完全不可用，#10 与 #61 是每次导航/每次轮询都发生。
- **P2 修 7 条**（约定是 2-3 条，超额 4 条，理由逐条给出）：
  - #1 `pull()` 无锁、#2 `AuthStore._save` 固定临时名、#3 collections 路由 500、#4 登录限流泄漏——这四条与已修的 P1 **同源**（都是「并发/持久化没做对」），单独留着会让本轮主题不闭环；每条 ≤ 20 行，且都能加确定性回归测试。
  - #21 取消丢弃产物、#63 作者同步无超时——这两条是**用户可见的数据不一致**（文件在库根但索引里没有；按钮永久卡死），修法与既有失败分支/既有超时范式完全同构。
  - #45 `getaddrinfo` 被调 1+2N 次——一行改动，且调用点在每次文案生成上。
- **P3 修 4 条**：#5（计数器原子化，4 个文件同一处）、#26（随之消除缩略图撞名）、#27（`stat()` 移进 try）、#66（Escape 双触发）。全部 ≤ 5 行、零行为变更风险。
- **复审提出的 9 条**：1 条阻塞（`neighbors()` 漏活照片）与 3 条「修复本身有问题」（`_load` 的 `exists()`、`_prune` 清活块、`chmod` 时序）当轮改正——前三条如果不改，本轮的两处 P1/P2 修复等于没修对；5 条新 P3 里 4 条（`snapshot()` 空队列窗口、`set_password` 读失败 500、`neighbors` 的 OR 谓词排序、计数器/权限位相关项）成本各 ≤ 10 行一并修掉；1 条（collections 读路由 503）核实不可达，不采纳。
- **其余 54 条转 backlog**：要么需要改数据模型（#31 的来源身份、#37 的跨目录迁移）、要么需要新增后台线程/进度接口（#16）、要么需要跨平台验证（#28 `CREATE_NO_WINDOW`、#24 的 trash 目录扫描）、要么属于「性能优化但当前库规模下收益有限」（#12/#17/#18/#19）。#41（电台探测上限从未实现）与 #30（索引损坏时设置页 500）是 backlog 里最该优先的两条，已在 backlog 里标注。

## 九、修复实现要点

1. `tiklocal/services/library_index.py`：新增 `neighbors(media_type, name)`——先按 `types` 查目标行的 `mtime`，再用键集查询取「前一条/后一条」。排序语义与 `page()` 一致（`mtime DESC, uri`），因此「前一条」= `mtime` 更大、或 `mtime` 相等且 `uri` 更小。**关键细节**：`image` 必须展开成 `('image','live')`（活照片与图片在图片形视图里是同一条流，旧路径 `_filters` 就是这么做的）；每侧拆成「同 mtime 的 uri 决胜查询」+「相邻 mtime 区间的索引范围查询」两步——把两者写成一条 `OR` 谓词会让 SQLite 走 `USE TEMP B-TREE FOR LAST TERM OF ORDER BY`（复审用 EXPLAIN 指出）。`tiklocal/web/media.py` 的 `neighbors()` 改成一行转发。
2. `tiklocal/static/download_page_controller.js`：新增 `historySignature(jobs)`（字段 `id/status/finished_at/file_count/error_message`，刻意不含 `progress_percent`）与 `lastHistorySignature`；`renderJobs()` 里只有 signature 变化或 `force` 才调 `renderHistoryPage()`。已核对 4 处「清空 `lastJobsSignature` 后强刷」的路径都带 `force: true`，删除/取消/分页/多选仍会重画。
3. `tiklocal/services/downloader.py`：取消分支与失败分支同构——先把已产出文件写进 job（`output_files_rel`/`file_count`/`output_path_rel`/`partial=True`），再 `_mark_canceled_locked`；锁外 `if canceled: on_outputs(...); return`。取消路径的 `source_context` 保持 `None`，所以不会写来源映射（取消的任务不该认领来源）。
4. `tiklocal/services/auth.py`：`_save` 改用 `write_json_atomic(..., mode=0o600)`（唯一临时名 + 8 次退避重试 + finally unlink + **在临时文件上就 chmod**，不留 umask 0644 的中间态）；`_load` 的 `except OSError` 分支在已有缓存时返回缓存，避免「占用窗口内所有请求 500」；路由侧 `except (OSError, RuntimeError)` → 503 `common.error.storage_unavailable`。
5. `tiklocal/auth.py`：`retry_after()` 用 `_active(key, create=False)`（读不建键）；`MAX_KEYS=1024` + `_prune()` 在 `record_failure` 前执行，**只**淘汰窗口已过的桶；表满且全是生效块时直接不记录新失败（宁可放弃一条新 IP 的计数，也不能清掉正在生效的限流）。`_active` 里绝不 pop——否则 `record_failure` 会追加到脱离 dict 的 deque，计数清零、限流可绕过。
6. 计数器：`json_storage.py`、`url_list.py`、`radio_energy.py`、`thumbnail.py` 统一 `_TEMP_COUNTER = itertools.count(1)` + `next(_TEMP_COUNTER)`（`itertools.count` 的 `next()` 在 CPython 里是原子的，不再有「取到同名」窗口）。
7. `tiklocal/services/thumbnail.py`：`if not overwrite and output.is_file():` 之后再 `try: output.stat() ... source.stat() ... except OSError: pass`——两段 stat 都在保护范围内，清缓存并发 unlink 不再让 `/thumb` 500。
8. `tiklocal/services/captions.py`：一次 `getaddrinfo` 后遍历结果（原式 `getaddrinfo(...)[i] for i in range(len(getaddrinfo(...)))` 是 1+2N 次解析）。
9. `tiklocal/static/detail_page_controller.js`：`confirmDelete()` 改读 `detailBoot.file`（模板注入），注释写明 `fileName` 在 DOMContentLoaded 回调作用域内、在此读取会抛 ReferenceError。
10. `tiklocal/static/settings_page_controller.js`：`postAuthor` 加 `signal: AbortSignal.timeout(60000)`。
11. `tiklocal/static/download_page_controller.js`：确认弹窗 `onKeydown` 加 `stopPropagation()`。
12. `tiklocal/web/library.py`：collections 的 DELETE/PATCH/items 三个写路由补 `except OSError → 503`，顺序在 `ValueError → 400` 之后，异常类型不混。读路由**没有**加——`CollectionStore._read(strict=False)` 自己吞掉所有异常，加进去是死代码。
13. `tiklocal/services/url_list.py`：`_write(backup=True)` 的备份从「把 store 改名成 `.bak`」改为 `shutil.copyfile`，读者不再有「文件不存在」的窗口（`snapshot()`/`lines()` 因此不需要额外加锁）。
14. `tiklocal/services/json_storage.py`：`write_json_atomic(path, value, *, mode=None)`——在临时文件上 chmod 后再 `os.replace`，让「只有属主可读」与「原子发布」同时成立。
15. `tiklocal/web/settings.py`：改密码前的 `verify()` 包进 `try/except RuntimeError`，与 `set_password` 的 `except (OSError, RuntimeError)` 对称——`auth.json` 存在但读不出时，回 503 而不是 500。

## 十、验证

- 新增 `tests/test_round22_regressions.py`（12 条）：
  - `test_detail_neighbours_follow_the_index_order`：5 条记录（含并列 mtime、跨类型、未知 uri），断言 `neighbors()` 的四种边界。修复前 `neighbors()` 不存在，详情页走的是整类物化。
  - `test_image_neighbours_include_live_photos`：`a.jpg`(image) / `b.jpg`(live) / `c.jpg`(image) 三条，断言三种位置都能连上活照片，且视频走查不会串类型。复审发现漏活照片后补的用例——它正是初版实现的回归测试。
  - `test_a_cancel_keeps_the_files_that_already_landed`：假引擎返回 `(1, 取消文案, ['clip.mp4'])` 并在 `_cancel_events[job_id]` 上等待取消，断言 job 的 `file_count`/`output_files_rel`/`partial` 与 `on_outputs` 回调。修复前 job 报 `canceled / 0 文件` 且回调从未发生。
  - `test_a_password_store_that_cannot_be_written_keeps_the_old_password`：让 `os.replace` 对 `auth.json` 抛 `PermissionError`，断言抛 OSError 后目录里只剩 `auth.json`（没有残留 `.tmp`，里面有 `password_hash`/`secret_key`）且旧密码仍有效。
  - `test_a_password_change_that_cannot_be_saved_answers_503`：`set_password` 抛 `PermissionError`，断言 `/api/auth/password` 回 503 + `common.error.storage_unavailable`。修复前是 500。
  - `test_a_password_change_that_cannot_read_the_store_answers_503`：`verify` 抛 `RuntimeError`，断言同样回 503。复审第 5 条意见的回归测试。
  - `test_a_locked_auth_file_falls_back_to_the_cached_copy`：只对 `auth.json` 让 `Path.stat` 抛 `PermissionError`，断言 `verify()` 与 `revision` 仍可用。复审第 1 条意见的回归测试（初版的 `exists()` 判断会让它失败）。
  - `test_a_login_limiter_read_never_grows_the_table`：50 次 `retry_after` 后表仍为空、失败 2 次后仍能限流、`MAX_KEYS + 50` 次 `record_failure` 后表不超过 `MAX_KEYS`。
  - `test_a_full_limiter_never_clears_a_live_block`：先让某个地址达到 limit，再灌 `MAX_KEYS + 50` 个新键，断言它仍被限流。复审第 2 条意见的回归测试（初版会在这里失败）。
  - `test_the_password_file_is_private_before_it_is_published`：记录 `os.replace` 时源文件的权限位，断言 `[0o600]`。POSIX only（Windows 上 `os.chmod` 只切只读位）。
  - `test_pulling_the_queue_waits_for_a_concurrent_add`：一个线程持 `_LIST_LOCK`，主线程起 `pull()` 线程，0.2 s 后断言它仍在阻塞。修复前 `pull()` 立即返回（`_write` 不在锁内）。
  - `test_a_bulk_overwrite_never_hides_the_queue`：monkeypatch `url_list.os.replace`，在替换瞬间断言 store 文件仍存在。修复前备份用改名，这一刻读者看到的是「没有队列」。
- 受影响的既有模块：`tests/test_auth.py`、`test_collections.py`、`test_download_urls.py`、`test_json_storage.py`、`test_media_sources.py`、`test_library_upgrade.py`、`test_thumbnails.py`、`test_radio_client.py`、`test_douyin_features.py`、`test_author_page.py`、`test_media_state.py` → 167 passed, 1 skipped。
- 全量：**602 passed, 8 skipped, 0 failed in 317.17 s**（起点 591P/7S，净增 12 条用例，其中 1 条在 Windows 上跳过）。
- 平台说明：Windows 上 `os.replace` 的共享冲突靠 monkeypatch 模拟（真实 Defender 占用不可控）；并发类用例一律用 `threading.Event` 而非 `sleep` 竞速，不受机器负载影响。

## 十一、覆盖率缺口

1. 前端修复（#51/#61/#63/#66）只有代码级论证，没有 DOM 级测试——本仓没有 JS 测试框架，历史上也不做（与第 20/21 轮同口径）。#51 的回归最容易再次发生（作用域类缺陷），已在 backlog 里建议加一条「模板内联 handler 只允许调用顶层函数」的约定。
2. #2 的 Windows 共享冲突是 monkeypatch 模拟的，未在真实 Defender 占用下复现；`write_json_atomic` 的重试语义已有 `tests/test_json_storage.py` 覆盖。
3. #1 的并发窗口用「持锁阻塞」确定性复现，没有真的跑两个 HTTP 请求并发写。
4. 域 B 的性能数字（0.2 s / 400 ms）引自仓库注释，未在本机重测；本轮未对 #12/#17/#18 做基准。
5. `--dev`/`--tls` 与下载器真机流程本轮未冒烟（改动未触及这两条路径，但 #21 属于下载器）。

## 十二、转 backlog

54 条（P2×23 / P3×31）已登记到 `docs/audit/backlog.md` 的「第 22 轮新增」段，其中标注为「优先」的两条是 #41（电台列表的探测上限从未实现，`docs/audit/backlog.md:26` 与 `round-04.md:81` 记的那个 `MAX_PROBE_PER_REQUEST` 全仓不存在）与 #30（索引损坏时 `/settings/` 500，改目录的恢复入口打不开）。复审提出的「collections 读路由补 503」没有单列条目，而是并进已有的 `CollectionStore._read` 那一行（同一个根因：它把 OSError 当损坏吞掉）。

## 十三、独立复审

复审由另一个上下文独立的 reviewer 完成（只读 + 允许跑测试），逐条核对本轮 14 处修复的「声称目标是否达成」「是否引入新缺陷」。它的测试末行是 `115 passed in 68.21s`（8 个相关模块）。

**判定：10 条正确，1 条阻塞，3 条「修复本身有问题」，另提出 5 条新 P3。** 全部 8 条意见都处理了：

1. **阻塞：`neighbors()` 漏掉活照片。** 旧路径 `records(media_type='image')` 经 `_filters` 展开成 `IN ('image','live')`，新实现只查 `= 'image'`。有抖音活照片的库里，图片详情页的上下一条会跳过所有活照片，活照片自身的两侧箭头也消失。→ 已修（`types` 展开 + 跨类型取最近），并补 `test_image_neighbours_include_live_photos`。
2. **`AuthStore._load()` 的 `exists()` 判断吞掉共享冲突。** `Path.exists()` 内部 `os.stat` 失败时返回 False，Windows 的 `PermissionError` 会被当成「文件不存在」，使「回退到缓存」的分支几乎不可达——也就是说 #2 的修复有一半没生效。→ 已改为 `except FileNotFoundError` 单独分支 + `except OSError` 回退缓存，并补 `test_a_locked_auth_file_falls_back_to_the_cached_copy`。
3. **`_prune()` 会清掉仍在生效的限流块。** 初版按「最久未失败」淘汰，表满后新键涌入时会删掉一个仍在窗口内且已达 limit 的桶，等于把那个 IP 解封。→ 改为只淘汰过期桶，表满且全是生效块时不记录新失败；补 `test_a_full_limiter_never_clears_a_live_block`。
4. **`chmod` 在 `replace` 之后。** 临时文件在 replace 前是 umask 默认（0644），含 `password_hash`/`secret_key` 的副本在那段时间对同机其他用户可读。→ `write_json_atomic` 加 `mode` 参数，在临时文件上 chmod 后再 replace；补 POSIX-only 的权限位用例。
5. **`snapshot()` 无锁 + 备份用改名。** `GET /api/download/urls` 与 `pull()`/`push()` 并发时可能读到「没有队列」，下载页显示空列表。→ 备份改 `shutil.copyfile`，窗口消失；补 `test_a_bulk_overwrite_never_hides_the_queue`。
6. **`set_password` 内 `_load()` 读失败仍 500。** 只捕了 `OSError`，而 `_load` 把读失败包成 `RuntimeError`。→ 两处都捕，回 503；补 `test_a_password_change_that_cannot_read_the_store_answers_503`。
7. **`neighbors()` 的 docstring 高估复杂度。** 写成一条 `OR` 谓词时 EXPLAIN 显示 `USE TEMP B-TREE FOR LAST TERM OF ORDER BY`（排序整个分区，不是 O(log n)）。→ 改成两步查询后 docstring 与实际一致。
8. **collections 读路由仍 500 → 不采纳。** 复核后确认不可达：`CollectionStore._read(strict=False)` 的 `except Exception` 已经把读失败转成空 payload，读路由拿不到 `OSError`。加 try/except 是死代码，只会让人以为这条路会抛。理由记进 backlog 对应条目。

（见下节，由独立 teammate 复核后补写）
