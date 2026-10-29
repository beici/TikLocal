# 审查修复循环索引

每轮固定五阶段：A 并行审查（5 个只读 subagent，文件域不重叠）→ B 甄别复现 → C 检索最优解 → D 修复 → E 验证 → F 文档。

基线（2026-10-27 03:42 实测）：`pytest -q` = **6 failed / 354 passed / 6 skipped**（54.79s）。
6 个失败均为既有环境问题，不是回归：

- `tests/test_auth.py::test_auth_store_hashes_password_and_increments_revision`
- `tests/test_library_upgrade.py::test_library_images_use_bounded_cached_thumbnails`
- `tests/test_media_state.py::test_dimensions_are_probed_in_background_and_refreshed_after_change`
- `tests/test_radio_client.py::test_device_auth_store_only_persists_token_hash`
- `tests/test_thumbnails.py::test_cli_cache_is_reused_by_web_and_isolated_between_sources[False]`
- `tests/test_thumbnails.py::test_cli_cache_is_reused_by_web_and_isolated_between_sources[True]`

任何新增失败都是 P0 阻塞项。

| 轮次 | 日期 | 起点基线 | 发现(P0/P1/P2/P3) | 修复提交 | 文档 |
|------|------|----------|--------------------|----------|------|
| 01 | 2026-10-27 | 6F/354P/6S | 1/4/8/0（确认 24，驳回 5） | `9b9c4ef`，27 文件 `+435/-155` | [round-01](round-01.md) |
| 02 | 2026-10-27 | 2F/358P/6S | 0/4/12/3（确认 17，驳回 2） | `d11b0fc`，29 文件 `+489/-99` | [round-02](round-02.md) |
| 03 | 2026-10-27 | 2F/365P/6S | 0/2/10/13（确认 25，修复 21，记录 4） | `fix: harden thumbnail cache…`，30 文件 `+650/-64` | [round-03](round-03.md) |
| 04 | 2026-10-27 | 2F/381P/6S | 1/3/12/1（确认 17，误报 4，转 backlog 4） | `08fc782`，10 文件 `+184/-18` | [round-04](round-04.md) |
| 05 | 2026-10-27 | 2F/383P/6S | 0/2/10/5（确认 17，全部修复） | `bd72b78`，19 文件 `+666/-75` | [round-05](round-05.md) |
| 06 | 2026-10-27 | 398P/6S/0F | 0/4/7/9（确认 20，修复 15，转 backlog 5） | `fix: localize error surfaces…` | [round-06](round-06.md) |
| 07 | 2026-10-27 | 412P/6S/0F | 0/0/9/7（确认 16，修复 15，转 backlog 1） | `34e53e3`，26 文件 `+759/-38` | [round-07](round-07.md) |
| 08 | 2026-10-27 | 414P/6S/0F | 0/0/14/5（确认 19，修复 17，转 backlog 2） | `db91d71`，18 文件 `+681/-30` | [round-08](round-08.md) |
| 09 | 2026-10-27 | 419P/6S/0F | 0/1/15/6（确认 22，修复 20，转 backlog 1，驳回 1） | `0201d36`，19 文件 `+262/-58` | [round-09](round-09.md) |
| 10 | 2026-10-27 | 427P/6S/0F | 2/10/6（确认 18，修复 18，转 backlog 2） | `02bc700`，22 文件 | [round-10](round-10.md) |
| 11 | 2026-10-27 | 432P/6S/0F | 0/0/12/8（报告 20，修复 4，复核无问题 3，转下轮 13） | `26d8bc0`，6 文件 | [round-11](round-11.md) |
| 12 | 2026-10-27 | 432P/6S/0F | 0/2/16/6（确认 24：新审 11 + 第 11 轮遗留 13） | `34822e8`，31 文件 `+552/-47` | [round-12](round-12.md) |
| 13 | 2026-10-27 | 439P→448P/6S/0F | 0/2/5/20（确认 27，修复 14，backlog 13） | `13bcd04`，18 文件 `+564/-45` | [round-13](round-13.md) |
| 14 | 2026-10-27 | 448P→450P/6S/0F | 0/0/3/10（确认 13，修复 13） | `1d199f3`，17 文件 `+212/-91` | [round-14](round-14.md) |
| 15 | 2026-10-27 | 450P/6S/0F | 1/0/4/7（确认 12，修复 10，转 backlog 2） | `dfee70e`，9 文件 `+356/-8` | [round-15](round-15.md) |
| 16 | 2026-10-27 | 455P/6S/0F | 3/6/11（确认 20，修复 14，转 backlog 6） | `fix: serialize instance access and stop silent store wipes`，19 文件 | [round-16](round-16.md) |
| 17 | 2026-10-27 | 462P/6S/0F | 1/10/8（确认 19，修复 19） | `8b6900e`，28 文件 `+485/-68` | [round-17](round-17.md) |
| 18 | 2026-10-27 | 469P/6S/0F | 1/6/5（确认 12，修复 12） | `fix: compress pages, validate ranges and normalise search`，10 文件 `+209/-28` | [round-18](round-18.md) |
| 19 | 2026-10-27 | 477P/6S/0F | 1/6/8（确认 15，修复 12，转 backlog 3） | `fix: surface unavailable sources and bound media decoding`，20 文件 `+384/-25` | [round-19](round-19.md) |
| 20 | 2026-10-27 | 484P/6S/0F | 0/0/7/3（确认 10，全部修复；data-layer 域未交付） | `fix: own legacy thumbnails, expire device tokens and keep ranged media off-thread` | [round-20](round-20.md) |
| 21 | 2026-10-29 | 579P/7S/0F | 0/0/12/24（五域 35 + 独立复审 1 = 36，驳回 1，成立 35，修复 9，转 backlog 26） | `fix: align the port probe, unlock --dev and bound bad request bodies`，14 文件 `+331/-44` | [round-21](round-21.md) |
| 22 | 2026-10-29 | 591P/7S/0F | 0/3/30/35（七域 68 + 独立复审 9 = 77，修复 18，驳回 1，转 backlog 54） | `fix: keep canceled downloads indexed, bound the password store and stop the history rebuild`，17 文件 | [round-22](round-22.md) |
| 23 | 2026-10-29 | 602P/8S/0F | 0/7/25/21（七域 53，去重 51：P1 6 + P2 24 + P3 21；修复 24，复审另提 4 条阻塞项并当轮修完，转 backlog 27） | `fix: hand the keyboard back to buttons, dialogs and the feed`，17 文件 `+415/-31` | [round-23](round-23.md) |
| 24 | 2026-10-29 | 602P/8S/0F | 0/4/26/19（七域 49，去重 46；修复 28：P1 4 + P2 19 + P3 5，转 backlog 18，并入既有 3） | `fix: log startup failures, keep partial downloads and stop the silent 500s`，21 文件 `+530/-113` | [round-24](round-24.md) |
| 25 | 2026-10-29 | 602P/8S/0F | 0/6/27/17（七域 50，去重 50；修复 15：P1 5 + P2 7 + P3 3，其中 5 条为复审追加，P1 中 1 条第 23 轮已修，转 backlog 40） | `fix: keep the source map, the big-image slot and the log file alive`，20 文件 `+636/-79` | [round-25](round-25.md) |
| 26 | 2026-10-29 | 602P/8S/0F | 0/5/10/17（五域 32，去重 32；修复 15 + 缓解 1，撤销 2，驳回 1，转 backlog 13） | `fix: guard the outbound edges and the default credential`，16 文件 `+411/-74` | [round-26](round-26.md) |
| 27 | 2026-10-29 | 602P/8S/0F | 1/2/21/28（八域 52，去重后 38；修复 19，转 backlog 19） | `fix: take the derived state with the deleted file`，20 文件 `+455/-27` | [round-27](round-27.md) |
| 28 | 2026-10-29 | 604P/8S/0F | 0/0/19/30（五域 49，去重后 45：P2 15 + P3 30；修复 14：P2 12 + P3 2，转 backlog 17） | `fix: let the second writer wait its turn`，11 文件 `+377/-18` | [round-28](round-28.md) |
| 29 | 2026-10-29 | 606P/8S/0F | 0/4/21/18（五域 49 + 自查 2 = 51，去重 43；修复 14：P1 4 + P2 9 + P3 1，转 backlog 29） | `fix: say why the library is empty and why the file is gone`，19 文件 `+651/-36` | [round-29](round-29.md) |
| 30 | 2026-10-29 | 606P/8S/0F | 3/13/22/17（五域 56，去重 54；修复 9：P0 1 + P1 1 + P2 6 + P3 1，转 backlog 45） | `fix: stop resolving the same path twice`，10 文件 `+280/-40` | [round-30](round-30.md) |


第 01 轮结束后：`pytest -q` = **2 failed / 358 passed / 6 skipped**。剩余 2 个（`test_auth.py:61`、`test_radio_client.py:77`）都是断言 `stat.S_IMODE(...) == 0o600`，Windows 返回 `0o666`，属平台差异，不是产品 bug。

第 02 轮结束后：`pytest -q` = **2 failed / 365 passed / 6 skipped**（74.52s），失败项与第 01 轮相同。

第 03 轮结束后：`pytest -q` = **2 failed / 381 passed / 6 skipped**（新增 16 个用例），失败项不变。

第 04 轮结束后：`pytest -q` = **2 failed / 383 passed / 6 skipped**，失败项不变（本轮修复集中在前端状态机，另新增 2 个认证用例）。

第 14 轮结束后：`pytest -q` = **450 passed / 6 skipped / 0 failed**（182.03s）。

第 05 轮结束后：`pytest -q` = **2 failed / 394 passed / 6 skipped**，失败项不变（新增 13 个回归用例：`tests/test_round05_regressions.py`）。

第 05 轮收尾（2026-10-27）：把那两个 `stat.S_IMODE(...) == 0o600` 断言改为仅在非 Windows 上执行（Windows 的 `os.chmod` 只切换只读位，模式位恒为 `0o666`，属平台差异而非产品缺陷）。当前基线：**398 passed / 6 skipped / 0 failed**，此后任何失败都是回归。

第 06 轮结束后：`pytest -q` = **399 passed / 6 skipped / 0 failed**（75.38s，新增 1 个用例）。

第 07 轮结束后：`pytest -q` = **412 passed / 6 skipped / 0 failed**（79.59s，新增 13 个用例：`test_download_tools.py` 4、`test_json_storage.py` 4、`test_round07_regressions.py` 3、feed/collection 各 1）。

第 08 轮结束后：`pytest -q` = **419 passed / 6 skipped / 0 failed**（145.76s，新增 7 个用例：`tests/test_round08_regressions.py`）。

第 09 轮结束后：`pytest -q` = **427 passed / 6 skipped / 0 failed**（162.72s，新增 8 个用例：`tests/test_round09_regressions.py`）。性能修复同时让套件从 203s 降到 163s。

第 12 轮结束后：`pytest -q` = **439 passed / 6 skipped / 0 failed**（183.41s，新增 7 个用例：`tests/test_round12_regressions.py`）。

第 13 轮结束后：`pytest -q` = **448 passed / 6 skipped / 0 failed**（167.03s，新增 9 个用例：`tests/test_round13_regressions.py`）。

第 15 轮结束后：`pytest -q` = **455 passed / 6 skipped / 0 failed**（167.63s，新增 5 个用例：`tests/test_round15_regressions.py`）。

第 16 轮结束后：`pytest -q` = **462 passed / 6 skipped / 0 failed**（190.57s，新增 6 个用例：`tests/test_round16_regressions.py`）。

第 17 轮结束后：`pytest -q` = **469 passed / 6 skipped / 0 failed**（151.95s，新增 7 个用例：`tests/test_round17_regressions.py`）。

第 18 轮结束后：`pytest -q` = **477 passed / 6 skipped / 0 failed**（157.64s，新增 8 个用例：`tests/test_round18_regressions.py`）。

第 19 轮结束后：`pytest -q` = **484 passed / 6 skipped / 0 failed**（153.47s，新增 7 个用例：`tests/test_round19_regressions.py` 5 条 + `test_thumbnails.py` 2 条）。

第 20 轮结束后：`pytest -q` = **494 passed / 6 skipped / 0 failed**（234.08s，新增 10 个用例：`tests/test_round20_regressions.py` 7 条 + `test_thumbnails.py` 2 条 + `test_downloader_lifecycle.py` 1 条）。

第 21 轮结束后：`pytest -q` = **591 passed / 7 skipped / 0 failed**（307.71s，新增 12 个用例，全部并入既有测试文件：`test_tls.py` 5 条 + `test_search.py` 3 条 + `test_auth.py` 2 条 + `test_radio_client.py` 2 条）。第 20 轮之后新增 HTTPS/媒体来源/统一搜索三块功能，套件规模从 494 涨到 579，本轮再 +12。

第 22 轮结束后：`pytest -q` = **602 passed / 8 skipped / 0 failed**（317.17s，新增 12 个用例：`tests/test_round22_regressions.py`）。本轮把审查视角从「新代码 + 安全」轮换到「数据完整性 / 并发与资源泄漏 / 性能」，因此 P0 仍为 0 但 P1 有 3 条（详情页删除按钮 ReferenceError、下载页每 2.2 s 重建历史列表、`records()` 全表物化压在详情页与 `/flow`），全部当轮修完。独立复审另指出 4 条「本轮修复本身有问题」（`neighbors()` 漏活照片、`AuthStore._load` 的 `exists()` 吞掉共享冲突、`_prune()` 会清掉仍在生效的限流块、`chmod` 在 `replace` 之后），也当轮改正——这类「修完了但没修对」只有独立复审能抓到。

第 23 轮结束后：`pytest -q` = **602 passed / 8 skipped / 0 failed**（213.71s，本轮未新增用例）。视角再换到「前端状态机 + 可访问性与多语言」，域的划分也改成按功能切。P0 为 0，P1 有 6 条（flow 空格键同时触发点赞与暂停、explore 网格卡片不可聚焦、图片详情页按钮被全局 `preventDefault` 吞掉、下载页「进行中」列表每 2.2 s 整块重建、关闭的合集弹层仍可 Tab 进入、换账号后串上一个用户的收听记录），全部当轮修完。因为没有 JS 运行时测试，验证改为**真实 Chromium 冒烟**（断言 DOM 与事件行为，不断言源码文本），覆盖 `/flow`、`/flow?tag=…`（375×812）、`/library?view=explore`、`/image`、`/settings`，控制台零 error；独立复审提出的 4 条阻塞项（键盘守卫过宽吞掉方向键、收藏键空格处理器落在抖音分支、图片详情同一处、tag 流隐藏页头丢掉移动端菜单入口）也当轮修完并复验。

第 24 轮结束后：`pytest -q` = **602 passed / 8 skipped / 0 failed**（232.98s，本轮未新增用例）。视角换成「后端错误路径与边界」，七个域按代码层切。P0 为 0，P1 有 4 条（无控制台宿主上启动失败零日志、失败任务重试时 `_persist_locked` 抛错留下永久「排队中」、`register_uris` 在请求线程里无超时等索引锁、`download_avatar` 固定临时名并发撕裂），全部当轮修完。另有两条「改了又回退」值得记下：把缩略图缓存键扩到全部来源会让 CLI 与 web 的缓存复用契约失效（`tests/test_thumbnails.py` 当场抓到），作者搜索 503→400 属产品语义变更（两条既有用例失败）——两者都转 backlog 并写明前置条件。还修了 2 条自测发现的连带问题：`_quarantine_database()` 的 WAL/SHM 只 `unlink` 会让新库带着旧 WAL 打开、`migrate()` 读到旧 `schema_migrations` 跳过全部迁移；缩略图候选帧循环没有总预算（时长探测 + 色彩探测 + 最多 4 次 ffmpeg 串起来能把 waitress worker 钉住数分钟，而槽位只有 2 个）。

第 25 轮结束后：`pytest -q` = **602 passed / 8 skipped / 0 failed**（241.13s，本轮未新增用例）。视角换成「内存与资源预算 + 长时运行稳定性」，七个域按资源归属切，域级明细另存 `round-25-r1.md`…`round-25-r5.md`。P0 为 0，P1 有 6 条（读失败把来源映射整表覆盖成单条、4 槽位并发解码大图峰值近 3 GB、`If-Range` 不匹配时全文件走生成器把 worker 钉死、话题流每翻一页重建全库并逐专辑查成员、两进程共写 `tiklocal.log` 时轮转失败后日志静默死亡，以及第 23 轮已修、本轮复核确认的电台偏好跨账号泄漏），当轮修完 5 条。修完 P2 5 条，其余 40 条转 backlog 并写明「为什么暂不修」。刻意不修一条：限流表满时新键不记录是第 21 轮加 `MAX_KEYS` 时的有意取舍（驱逐仍在封禁窗口内的地址比少记一次失败更糟），要改需换前缀聚合或全局令牌桶。下一轮建议视角是「外部依赖与网络边界」，那是唯一还没单独覆盖过的维度。

第 26 轮结束后：`pytest -q` = **602 passed / 8 skipped / 0 failed**（本轮未新增用例，两个测试缝调整了替身写法）。视角换成「外部依赖与网络边界」——凡是越过进程边界的地方：外部二进制（yt-dlp / gallery-dl / ffmpeg / taskkill）、主动出站 HTTP（sidecar、头像、embedding）、被外部访问的入站面（`0.0.0.0` 绑定、反代头、TLS、`--dev`）、前端第三方资源。P0 为 0，P1 有 5 条：yt-dlp 从不传 `--ffmpeg-location`（只装 `imageio-ffmpeg` 时音视频分离站点静默丢音轨）、批量入队每条 spawn 一次 `--version`（5000 行时单请求半小时以上）、直链下载无累计字节上限（可写满媒体盘）、sidecar 作者 `avatar_url` 无主机校验（盲 SSRF + 把任意响应存成作者头像）、首次启动固定口令 `admin666` 配默认 `0.0.0.0`。前四条当轮修完，第五条按产品决策口径只做警告缓解并进 backlog。另外新增 `tiklocal/services/net_guard.py` 收口「拒绝回环/链路本地/元数据地址」与「逐跳校验重定向」，让直链下载和头像下载共用一套规则；`taskkill` 改成先杀进程树再回收父进程；`--dev` 在非回环绑定时改绑 `127.0.0.1`。两条复核后撤销（`output.css` 无漂移、模板全部走 `static_asset()`）、一条驳回（用户自配的 `embedding.base_url` 跟随 302，与已驳回的「yt-dlp 重定向内网」同类），其余 13 条转 backlog。下一轮建议视角是「数据生命周期与删除语义」。

第 27 轮结束后：`pytest -q` = **604 passed / 8 skipped / 0 failed**（224.23s，新增 2 个回收站用例）。视角换成「数据生命周期与删除语义」，域不再按代码层切，而是按「删除一个文件会经过哪几个环节」切（移动文件、写回收站清单、删索引行、清派生数据），因此八域里 F1/F3 与 G1/G3 是同一批代码的两次独立复访。P0 有 1 条、P1 有 2 条：大小写不敏感文件系统上 `dedupe` 把同一文件同时算进 keep 与 delete，`--delete` 会把所有副本删光；「移除媒体源」会连同目录里的回收站一起删掉；删除正在播放的媒体时播放器没释放句柄，Windows 上服务端 `shutil.move` 拿到 `WinError 32`，用户看到「删除失败」而文件其实没动。P0/P1 全部当轮修完，另修 P2 8 条、P3 8 条（回收站 sidecar 孤儿、purge 不清元数据与索引状态表、元数据损坏静默清空、purge 后同名新文件继承旧收藏、合集页把网络错误显示成空合集、恢复被占用文件返回 500 等），转 backlog 19 条。这轮最值得记的教训是：**删除是一条链，不是一次调用**——同一个「文件没了」的事实有四个落点，任何一个漏掉都会在下一次同名文件落进来时变成幽灵状态。

第 28 轮结束后：`pytest -q` = **604 passed / 8 skipped / 0 failed**（226.90s，本轮未新增用例，全部用手工探针验证并发行为）。视角换成「并发与多进程一致性」——不问「哪里写错了」，而问「谁在和谁同时动同一份状态」：waitress 的 8 个线程之间、CLI 与运行中的服务之间、下载 worker 与其子进程之间、SQLite 的多个连接之间。**P0 与 P1 都是 0**，这是前几轮把「单请求就能触发的错误路径」扫干净的直接结果：剩下的问题几乎都要第二个并发主体才会显形。P2 里最要紧的四条：索引重建期间完成的下载被 `register_uris()` 的 0.5 s 超时静默丢登记却仍报成功（改抛 `IndexBusyError`，job 落 `partial` 可重试）；进度写盘失败会把在飞下载整个判失败并删掉已下载字节（进度簿记不该有权力杀死传输）；`worker.start()` 抛异常会永久占住一个并发槽（`max_concurrent=1` 时此后全部排队到重启）；设置页两个标签页同时保存媒体目录会互相覆盖并让目录从运行中的索引消失（加 `_SOURCES_LOCK` 把读-改-写整段做成临界区）。另修 cookie 上传固定临时名互相截断、`MediaIndexStore.delete()` 两段事务、缩略图 follower 超时后重复抢锁、改文件夹后的重扫被静默跳过、下载目录运行中改写导致产物落在两个根之间等 8 条 P2，以及队头阻塞与 `_terminate_process()` 末尾无超时 `wait()` 两条 P3。三条手工探针（并发 cookie 上传、worker 启动失败、派发旋转）确认新逻辑成立。转 backlog 17 条，其中 `/api/activity` 在长写事务期间阻塞 15 s 后 500、读路径不在同一快照、`write_json_atomic` 不 `fsync` 三条优先。下一轮建议视角是「可观测性与故障诊断」。

第 29 轮结束后：`pytest -q` = **606 passed / 8 skipped / 0 failed**（新增 2 个用例：`/api/client-log` 的上报入日志与换行注入防护）。视角换成「可观测性与故障诊断」——不问「代码对不对」，而问「出问题时，日志和界面能不能告诉用户与运维发生了什么、下一步做什么」：日志与错误信息、前端错误呈现、诊断端点、启动自检、慢请求与前端异常上报五域。P0 为 0，P1 有 4 条：认证子系统全程零日志（限流 429、密码错 401、CSRF 403 全静默，开了认证监听 `0.0.0.0` 等于没有登录审计）、删除失败的 500/409 在 `except` 里返回元组而绕过全局 500 handler 且不记日志、首页把四条请求的失败 `catch` 成空 payload 渲染成「这里暂时很安静」的成功空态、文件在回收站/来源离线/从未入库三种情况共用一句 404。四条当轮修完，另修 P2 9 条（慢请求 >2 s 日志、前端未捕获异常上报端点、索引同步结果落日志、缩略图失败日志、JSON 隔离日志、下载任务生命周期日志、500 响应带关联 ID、重扫超时与服务端原因、`/api/media/info` 索引异常日志）并顺带修掉 1 条 P3，转 backlog 29 条。这轮最值得记的教训是：**空态文案绝不能兼作失败态文案**——「没有数据」和「读不到数据」在界面上长得一样，用户会以为库被清空了。

第 30 轮收尾：`pytest -q` = **606 passed / 8 skipped / 0 failed**，无新增用例（本轮是性能改造，行为由既有用例锁住）。视角是「一万条、十万条时还对不对」。四个子代理分别查索引与 SQL、JSON 存储与服务层、缩略图与媒体探测、前端渲染，主审对可疑点做了实测：`Path.resolve()` 在本机 **275µs/文件**，而电台每首候选要跑两次 realpath 加一次 `is_file()`——1 万首音频的开台/切台/refill 各要 **7.9s**，这是本轮唯一被当成 P0 修掉的项（`existing_media_path()` + 缓存解析根）。其余 P0 更贵：`records()` 仍是全表 `SELECT *` + fetchall（tag feed、深链 caption feed、作者页、电台都用它），`feed` 每页按全库规模建两份 dict 并读三次 `favorites.json`；两者都因为要动分页与选择算法而留给第 31 轮。已修 9 条的共同形状是「同一请求内把同一件事做了两三遍」：合集摘要为每个 item 建 dict 后全部丢弃、收藏判断为一个布尔值解析整份文件、`/thumb` 的 304 先读完整张图再丢弃、`ORDER BY mtime DESC, uri` 缺第三列索引。最值得记的教训是：**性能问题要先量再改**——`image_random` 分页实测 17ms（分页不慢，慢的是全表取行），如果照直觉去优化分页就白改了。

## 目录


- `round-NN.md`：每轮审查报告（发现、复现、处置、检索、修复、验证）
- `round-NN-rX.md`：某一轮里单个域的审查明细（本轮起用于保留域级表格）
- `backlog.md`：未修问题（按严重度排序，含暂不修理由与前置条件）
- `proposals/`：需要跨模块重构的方案（评审后才动代码）
