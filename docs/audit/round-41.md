# 第 41 轮 · 长时运行的数值稳定性

- 日期：2026-10-29
- 起点：`audit-round-41-start`（第 40 轮收尾 `a0dcdf2`）
- 基线：`653 passed / 8 skipped / 0 failed`（348.93s）
- 视角：计数器、时间基准（`time.time()` 与 `monotonic` 混用）、进度与 ETA 的累积误差、退避与 TTL 的时钟、自增 id 与 `ROWID` 复用、浮点百分比与单位换算。前几轮修的是「一次做错」，这一轮找的是「做对一次、做上一万次才错」。

## 一、方法与范围

| 域 | 覆盖 | P0 | P1 | P2 | P3 |
| --- | --- | --- | --- | --- | --- |
| V1 时钟基准 | `thumbnail.py`、`radio.py`、`radio_energy.py`、`device_auth.py`、`author_profiles.py`、`metadata.py`、`client_log.py`、`web/media.py` | 0 | 0 | 1 | 6 |
| V2 计数器与持久化数值 | `database.py`、`library_index.py`、`downloader.py`、`trash.py`、`collections.py`、`playback_resume.py`、`web/library.py`、`web/flow.py` | 0 | 0 | 5 | 4 |
| V3 数值边界与格式化 | `app.py`、`library.py`、`media_info.py`、`media_payloads.py`、`search_text.py`、`web/media_payloads.py`、`web/search.py`、`web/settings.py`、`i18n*.py` | 0 | 0 | 1 | 4 |
| V4 前端数值与定时器 | 下载页、`flow_page_controller.js`、`radio_controller.js`、`radio_scene.js`、`library_page_controller.js`、`settings_page_controller.js`、`home_page_controller.js` | 0 | 1 | 3 | 6 |
| V5 我的自查 | 全仓 `time.*` / `perf_counter` 37 处、`_byte_range`、`save_position`、`preference_dimensions`、`pairing_grants` | 0 | 0 | 0 | 2 |
| **合计** | | **0** | **1** | **10** | **22** |

去重 33 条；修复 20 条（P1 1 + P2 10 + P3 9）；转 backlog 13 条。

## 二、P1 详述

### V4-1 睡眠定时器只在 `setInterval` 里执行，且读数不读 deadline

`radio_controller.js:517-518,522-532`：`setInterval(tickSleep, 1000)` 是唯一的到期检查点；`onTimeUpdate`（音频在后台照常触发）不判 `sleepEnd`。桌面 Chrome 对隐藏标签页把定时器节流到 1 次/分钟（省电模式更低），因此「30 分钟后停止」在后台标签页里可能延后到 30 分钟 + 节流窗口；窗口关闭则完全不执行。

同一函数的 `updateSleep()` 每秒渲染 `sleepMinutes`（档位原值），从不读 `sleepEnd - Date.now()`：30/60/120 分钟档位里读数恒定不变，读屏用户被告知剩余时间比真实值最多多 119 分钟。

修法：到期判定移进 `onTimeUpdate`（每个 `timeupdate` 都判一次），读数改为 `Math.ceil((sleepEnd - Date.now()) / 60000)`，同时把每秒 `innerHTML` 重建降为「文本变了才写」。

## 三、P2 详述（全部当轮修复）

| 编号 | 位置 | 机制 | 修复 |
| --- | --- | --- | --- |
| V1-1 | `thumbnail.py:334` | `.fail` 标记年龄 `time.time() - st_mtime` 未守卫负值；时钟回拨后差值恒为负 → `> TTL` 永假 → 标记永久「新鲜」，`get_thumbnail()` 直接返回 1×1 占位 PNG | `age < 0 or age > TTL` 才算过期（读不出年龄就重试） |
| V1-5 | `device_auth.py:119` | `except ValueError: return False` 把「解析失败」当成「已过期」的反面——实际是 `expires_at` 解析失败时 `return False`（= 未过期），180 天 TTL 变 ∞ | 解析失败按**已过期**处理（fail-closed） |
| V2-1 | `web/library.py:55`、`:529-530` | `read_int_arg('offset', 0, minimum=0)` 无上限 → `?offset=1e19` 直接绑进 `LIMIT ? OFFSET ?`，超 int64 抛 `OverflowError` | 新增 `MAX_OFFSET = 1_000_000`，两处都夹取 |
| V2-2 | `library_index.py:1231-1235` | 冷扫描期间被 `register_uris()` 要求重扫 → 跑两轮但只返回第二轮 dict（通常 0 新增），设置页报「0 个新增」 | 多轮结果按字段求和 |
| V2-3 | `downloader.py:674-682`、`:405-406` | 内存裁剪遇非终态尾部就 `break`（列表可超 200），落盘却 `jobs[:200]` 硬截 → 最老的排队任务从未写进 `jobs.json`，重启后既不在历史也不会变 `interrupted`，静默消失 | 裁剪跳过未完成任务继续找更老的终态项；`save()` 改为「未完成全留 + 终态补足预算」 |
| V2-4 | `web/library.py:214-218` | `position / duration` 无守卫；`playback_positions.duration` 是 REAL 且无 CHECK，回收站 manifest 里一条 `duration=0` 让 `/library`、`/favorite`、`/collection` 全部 500 | `duration > 0` 才算进度；`restore_state` 校验 `float(duration) > 0` |
| V2-5 | `web/flow.py:38-40`、`:555-566` | `bucket` 倍增到 `request_window`（`/flow?page=5000&size=200` → 1,000,001），`get_weighted_selection` 对整池生成随机键并排序，整份进 8 槽缓存 | `request_window` 夹到 `min(limit, 4096)` |
| V3-1 | `app.py:274` | `static_asset` 的版本戳用 `st_mtime`（秒），同一秒内改两次 CSS 得到同一个 `?v=` | 改用 `st_mtime_ns` |
| V4-2 | `flow_page_controller.js:1554`、`:1581-1590` | `loop` 视频每圈回卷都命中 `previous > 0.85*duration && current < 1.5` → 每圈一条 `replay` POST（10 s 短片 1 小时 = 360 次请求 + 360 行活动记录），并把 `_completedInView` 永久置真 | 每次进入条目最多报一次（`_replayRecorded` 闩锁，`showItem` 时重置） |
| V4-3 | `flow_page_controller.js:146`、`:160`、`:165-170` | `performance.now()` 页面隐藏时照常前进，全文件无 `visibilitychange` → 后台 10 分钟被记成 `visible_ms=600000`，图片条目一律判 `complete` | 累加 `hiddenMs`（含「此刻仍隐藏」的开口区间），`finishActivity` 扣除 |

## 四、P3 已修（低成本顺带）

| 编号 | 位置 | 修复 |
| --- | --- | --- |
| V1-2 | `thumbnail.py:343` | 跨文件 mtime 比较改 `st_mtime_ns`（FAT32 秒级粒度下「副本已写完」与标记同刻） |
| V1-4 | `radio.py:126` | 画像裁剪按墙钟字符串排序 → 回拨时新记录最先被裁；改为 `PROFILE_LIMIT` + 当前曲目钉住 |
| V2-6 | `downloader.py:1515-1520` | `Content-Length: -1`/非数字：`int()` 抛 `ValueError` 逸出，或 `-1` 为真导致完整文件被删并报「连接中断」；改为 `isdigit()` 才转、判定 `> 0` |
| V3-2 | `app.py` `filesizeformat` | 1048575 字节渲染成 `1024.0 KB`（与 1 MB 同数字不同单位）；先舍入再进位到下一单位 |
| V3-3 | `web/client_log.py:...` | `line: 1e400` 被 JSON 读成 `inf`，`int(inf)` 抛 `OverflowError` → 「永不失败」的诊断端点 500；改为 `isfinite` 守卫 |
| V3-5 | `i18n.py` `detect_locale` | `q=0` 表示「不接受」，原来当最低优先级处理（`zh-CN;q=0, en;q=0.9` 选 zh）；`q<=0` 直接跳过 |
| V4-5 | `radio_controller.js:534-553` | 睡眠读数恒定（同 V4-1），改读 `sleepEnd` |
| V5-1 | `trash.py` | `entries()` 排序与 `expired()` 判定都用字符串字典序，不同 UTC 偏移（旧版本写入/换过时区）会排错、错判过期；新增 `parse_deleted_at()` 统一解析成 aware UTC |
| V5-2 | `device_auth.py:112-131` | `expires_at` 为 naive 值时与 aware `current` 比较抛 `TypeError`（只捕 `ValueError`）→ 每个已配对设备检查都 500；解析后 naive 视为 UTC |

## 五、未修清单（12 条）

| 编号 | 位置 | 为什么暂不修 |
| --- | --- | --- |
| V1-3 | `thumbnail.py`/`radio.py` 三处缓存有效性判据（`<`/`>=`/`==`） | 统一语义要重定 `THUMB_VERSION` 与波形缓存键，属设计决策；当前三种各自自洽 |
| V1-6 | `downloader.py` 进程等待用 `monotonic` | Windows 上 `monotonic` 含休眠时间，恰好是这里想要的语义（等真实时间）；换 `perf_counter` 反而会在休眠后误判超时 |
| V2-7 | `library_index.py:68-85` 计数口径（跳过不计数、并发删除不计 `deleted`） | 纯统计文案，改它要动扫描主循环的返回结构 |
| V2-8 | `database.py:702-711` 每批活动都物化 2 万 id 反连接 | 20k 行时每批 1-2 ms，收益小于改动面；可改为水位删除 |
| V2-9 | `database.py:806-838` `total_visible_ms` 被同一段时长累加 2-3 次 | 该列当前只被 `snapshot_state`/`restore_state` 搬运，不参与计算；接入「总观看时长」前必须修 |
| V3-4 | `web/search.py` 用户计数截断 | 需要改搜索聚合口径，与本轮视角关系弱 |
| V4-4 | `download_page_controller.js:103-104`、`:294-296`、`:356`、`:406-409` | `99.5…99.99%` 四舍五入成 100%，任务仍在跑；要三个显示点共用一个归一化函数，涉及下载页状态机，转下轮 |
| V4-6 | `settings_page_controller.js:417-431` | 配对码倒计时用 `Date.now()` 折绝对时刻，NTP 校正后误判过期/虚高；改 `performance.now()` 要同步改服务端 `expires_in` 语义 |
| V4-7 | `home_page_controller.js:253-259`、`:293`、`:298-305` | 问候语与「那年今日」日期只在初始化时算一次，长期驻留页面跨零点后错一天；加定时器属新行为 |
| V4-8 | `settings_page_controller.js:911-914` | `formatBytes` 先按未舍入值选单位 → 1023.95 MB 显示 `1024.0 MB`；与本轮 `filesizeformat` 同型，但该函数只在设置页用 |
| V4-9 | `settings_page_controller.js:929`、`:934-935` | 见报告：数值拼接未做类型收敛 |
| V4-10 | 见报告 | 前端格式化零散重复，需抽公共函数 |

## 六、实现要点

1. **「读不出来」和「读出来是坏的」要分开**：`device_auth` 把解析失败当「未过期」（fail-open），`trash` 把解析失败当字典序最小（排到最前）。同一个异常，两种相反的默认值，都是错的。
2. **舍入之后再判断单位**：`filesizeformat` 与 `formatBytes` 都是「先按未舍入值选单位、再对结果舍入」，于是 1023.999 显示成 `1024.0`。顺序反过来（先舍入、再看是否进位）才对。
3. **单调时钟也会走**：`performance.now()` 在隐藏标签页里照常前进，`setInterval` 却会被节流。凡是「用户看了多久」「还剩多久」，判定点要挂在事件上（`timeupdate`/`visibilitychange`），不能挂在定时器上。
4. **截断要区分「旧的」和「未完成的」**：`jobs[:200]` 与「遇到非终态就停」是两个相反的错，合起来才丢数据。正确规则是「未完成全留，终态补足预算」。
5. **字符串不是时间**：`trash` 的排序键与 `device_auth` 的比较都用过 ISO 字符串原样比较；只要写入方换过偏移量（旧版本、手工编辑、换时区），字典序就不再等于时间序。

## 七、验证

- 新增 `tests/test_round41_numerics.py`（6 条，全绿 5.02s）：offset 上限、`duration=0` 的网格、`jobs.json` 保留未完成任务、`filesizeformat` 进位、`client_log` 的 `inf` 行号、`Accept-Language: q=0`。
- 聚焦回归：`test_i18n`、`test_download`、`test_downloader_lifecycle`、`test_web_runtime`、`test_round39_locking`、`test_round40_lifecycle`、`test_recommendation` = **68 passed / 2 skipped**。
- 全量：**659 passed / 8 skipped / 0 failed**（基线 653，新增 6 条）。

## 八、覆盖率缺口

1. 没有任何测试模拟**时钟回拨**（`time.time()` 被 monkeypatch 到过去），本轮三条修复（V1-1、V1-4、V5-1）只有单测层面的构造，没有端到端。
2. 前端四条（V4-1、V4-2、V4-3、V4-5）没有自动化覆盖，靠静态推演 + 代码结构；`hiddenMs` 的开口区间尤其需要浏览器验证。
3. `library_index` 的多轮扫描求和只在单测里用桩验证，未在 19k 真实库上跑过两轮并发。
4. `st_mtime_ns` 在 FAT32/exFAT 上的实际粒度未实测（Windows 只测了 NTFS）。

## 九、下轮建议

**跨进程与多实例**：两个 TikLocal 进程指向同一个 `TIKLOCAL_INSTANCE`（或同一媒体根）时会发生什么——实例锁覆盖了哪些路径、`jobs.json`/`favorites.json`/`collections.json` 的读改写有没有跨进程保护、SQLite 的 WAL 在两进程下是否安全、缩略图缓存与回收站目录的并发写入。
