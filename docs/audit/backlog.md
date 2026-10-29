# 审查 backlog

未在本轮修复的问题，按严重度排序。每条写清：位置、复现条件、为什么暂不修、需要什么前置条件。

> 规则：默认驳回，除非有证据表明用户会遇到。P3 一律不修（除非错字/文案错误）。

## P0 / P1

（暂无）

## P2

| 问题 | 位置 | 为什么暂不修 |
|------|------|--------------|
| 未设置 CSP（`Content-Security-Policy`） | `tiklocal/auth.py:150-157` 只发 `X-Content-Type-Options` / `Referrer-Policy` / `X-Frame-Options` | 页面大量内联 `<script>` 引导数据，上 `script-src 'self'` 需先改模板与所有 controller 的注入方式；收益低于改造风险，留到统一前端构建时做 |
| Cookie 导入上传会把整个文件读进内存 | `tiklocal/services/downloader.py` cookie 上传路径 | 正常 cookie 文件只有几 KB；`MAX_CONTENT_LENGTH` 已兜底 8 MiB，够用 |
| 分页器动画中 `dropHead()` 会错位 | `tiklocal/static/flow_page_controller.js` `dropHead` 与 pager 动画竞态 | 中等置信度、需真机视频序列才能稳定复现；改动涉及 DOM 复用顺序，先记录 |
| `endGesture()` 在边界不 `await loadFeed()`，快速滑动被吞 | `tiklocal/static/flow_page_controller.js` | 需要连续快速滑动才触发，影响小；修法要把手势链改成 Promise 链，等下一轮统一处理 |
| 页面不可见时音频继续播放、`visible_ms` 虚高 | `tiklocal/static/playback_resume.js` / 活动统计 | 属产品行为取舍（后台继续播放常被期望），需产品确认后再改 |
| `douyin_mode.js:52` 用 `location.replace()` 切模式会丢来源页 | `tiklocal/static/douyin_mode.js` | 只影响「从详情页切模式后无法返回」，低概率；改需引入 history state |
| 缩略图跨进程生成无锁 | `tiklocal/services/thumbnail.py`（进程内 in-flight 去重已加） | 多进程部署才会重复计算同一张图，结果仍正确（原子 `os.replace`），只是浪费 CPU |
| flow 预载下一视频用 `preload='auto'` 整段拉流且不取消 | `tiklocal/static/flow_page_controller.js:802,823-831` | 改 `preload='metadata'` 会让快滑时的首帧变慢，需要真机 A/B 才能定；当前只在快速连滑时产生并发大文件请求 |
| `<head>` 内 8 个解析阻塞脚本 + 47 KB 不可缓存内联 i18n | `tiklocal/templates/base.html:12-24` | 需引入前端构建/外链化，改造面覆盖所有 controller 的引导数据注入，收益低于风险 |
| `CollectionStore.replace_snapshot()` 与索引快照可能短暂不一致 | `tiklocal/services/collections.py` | 需要跨进程锁（外部进程写库后需重开 app 才能看到一致快照）；当前只有 CLI 与 Web 同时运行时才可能遇到 |
| 登录失败锁定的剩余时间未通过 `Retry-After` 暴露 | `tiklocal/auth.py`（限流响应） | 前端已能按 429 提示「稍后再试」；精确倒计时需前端状态机，收益低 |
| 电台冷启动时 `items` 逐条 ffprobe | `tiklocal/web/radio.py`（已加 `MAX_PROBE_PER_REQUEST` 上限） | 彻底解决需后台预热线程与进度反馈，属新功能；上限已把单次请求的探测成本封住 |
| flow 页边界首滑必然回弹且无提示 | `tiklocal/static/flow_page_controller.js`（`endGesture` 边界分支） | 回弹本身是防误触设计；加提示需非模态 toast，且不能在手势中插入全屏 loading（会锁手势） |
| `create_app()` 在端口监听前同步重建全量索引（8000 文件 8.6 s） | `tiklocal/services/library.py` 构造器里的 `sync()` | 第 09 轮已把每文件的 `Path.resolve()` 换成 `path.relative_to(resolved_root)` 快路径（8000 次 4173ms → 272ms），但 `sync()` 本身仍是同步的；把它挪到后台线程会改变启动语义（首屏可能看到空库/进度态），需要单独设计就绪标志 |
| 每个页面内嵌 905 条 / 48 KB i18n 目录 | `tiklocal/templates/base.html:28` + `tiklocal/i18n.py` 的 `js_catalog()` | 需按页面拆分 key 或产出 `i18n.<locale>.<hash>.js` 走浏览器缓存；改动覆盖所有模板与控制器，单独一轮做 |
| 盲 SSRF：重定向目标请求仍会发出（只在读 body 前拦） | `tiklocal/services/downloader.py::_direct_fetch` | 已用 `response.geturl()` 复验最终地址并丢弃结果；彻底修复需要出口地址过滤（socket 级），属于部署层能力 |
| 缩略图冷缓存无进程内 single-flight | `tiklocal/services/thumbnail.py:144-152` | 10 个并发请求会起 10 个 ffmpeg；结果正确（原子 `os.replace`），只是浪费 CPU。加 in-flight 字典需处理异常与超时回收 |
| yt-dlp / gallery-dl 引擎没有重定向内网约束 | `tiklocal/services/downloader.py`（子进程引擎） | 子进程内的 HTTP 客户端不受本进程控制，只能靠部署层出口过滤 |
| Windows 保留名与超长路径会让下载失败 | 下载命名层（`tiklocal/services/media_naming.py` 与 `download_tools.py` 的模板） | `CON.mp4` / `aux.jpg` 需要改写文件名，>259 字符需要 `\\?\` 前缀；两者都会改变已有文件的命名与去重键，必须单独一轮做迁移与回归 |
| 索引扫描的 `skipped_prefixes` 没有回给前端 | `tiklocal/services/library_index.py:738`（`result['skipped_prefixes']`） | 后端已能收集权限不足的前缀并保留索引行，但 `/api/library/sync` 的响应还没带出来，用户不知道有目录被跳过；补一个字段即可，留到下一轮做 UI 提示时一起改 |
| 注销不吊销服务端会话（旧 cookie 在有效期内仍可用） | `tiklocal/auth.py`（签名 cookie，无会话表） | 吊销需要 token family / session 存储 + 迁移，覆盖登录、配对、CSRF 三处；前端已清 cookie 并跳登录，风险限于有效期内。第 08 轮记录，需单独一轮设计 |
| 登录限流按 IP 单桶，不区分用户 | `tiklocal/auth.py`（失败计数） | 单机部署按 IP 桶足够；按用户 + 指数退避需先引入失败计数存储。当前已有 429 + `Retry-After` |
| 搜索只折叠 ASCII 大小写，非 ASCII 文件名搜不到（`q=ärger` 无结果） | `tiklocal/services/library_index.py:673` 的 `LIKE` 查询 | 根治需要 `media_items.search_key` 列（NFC + casefold + 空白折叠）+ 迁移回填 + 查询改写，影响 `_filters()`/`page()`/`q` 截断，单独一轮做；CJK 文件名不受影响 |
| 搜索不做 Unicode 归一化（NFC/NFD 互不匹配）与空白折叠 | 同上 | 与上一条同一修法，合并进 search_key 方案 |
| Pillow 12.3.0 仍命中 41 条公告 | `pyproject.toml:21`（已放宽为 `>=12.3.0,<14`） | 本机 Python 3.14 尚无 13.0.0 轮子，装 13 会退化为源码编译；等 cp314 轮子发布后 `poetry update pillow` 并复跑缩略图相关测试 |

## P3

| 问题 | 位置 | 为什么暂不修 |
|------|------|--------------|
| 下载页活动任务期每 2.2 s 重建两个列表 + 全文档 `feather.replace()` | `tiklocal/static/download_page_controller.js:363,389,540` | 只在有活动任务时发生，列表规模小；改成增量更新要重写渲染函数 |
| 瀑布流旋转/换列宽时整表销毁重建 | `tiklocal/static/library_page_controller.js:376-379` | 重建后图片走缓存，可感知卡顿但无功能问题 |

## 待确认（推断成立但未复现）

| 问题 | 位置 | 待验证点 |
|------|------|----------|
| `sendBeacon` 在 iOS Safari 上可能因 body 类型限制被丢弃 | `tiklocal/static/playback_resume.js` | 需真机验证；即便丢弃也只丢最后一次进度（每 5 秒另有 `timeupdate` 保存） |
| `test_author_sync.py` 两个用例在全量跑中偶发失败 | `tests/test_author_sync.py:425`、`:449`（都直接调用模块级 `author_profiles.set_profile_url`，不经过 app fixture） | **已定位（第 02 轮）**：`test_author_sync.py` / `test_author_page.py` 用固定路径 `.tmp-inst-sync` / `.tmp-inst-author` 作 instance 目录，与并行/重复运行撞库；改为按 PID 隔离后全量稳定 |

## 流程备注

- `web_search` 在本机不可用（缺 `DEEPSEEK_API_KEY`，会直接报错）；检索最优解改用 `web_fetch` 抓官方文档（Flask/Jinja/yt-dlp/Werkzeug）并引用链接。

## 明确驳回（不再重复上报）

| 问题 | 驳回理由 |
|------|----------|
| `DELETE /api/trash` 在两个 media source 指向同一目录时 500 | 边角配置；修它会动回收站存储格式，风险高于收益 |
| `/api/trash` 响应无 `success/data` 信封 | 前端按现有形状读取；改需同时改前端，收益低 |
| 后台 worker 线程 cookie 报错固定中文 | 无请求上下文，已在 `docs/phase-3-verification.md` §7 记录为设计取舍 |
| 下载历史 `error_message` 不随语言切换重译 | 持久化字段，同上 |
| 2 个既有失败测试（`test_auth.py:61`、`test_radio_client.py:77`） | **已解决（第 05 轮收尾）**：断言改为仅在非 Windows 上执行，Windows 的 `os.chmod` 只切换只读位，模式位恒为 `0o666` |
| `/api/download/jobs?limit=80` 之外历史不可达 | 「最近下载」区块的设计上限，改分页需重做 UI |
| `AUTH_ENABLED=False` 时不下发安全响应头 | 该模式是自担风险的完全内网配置，加头反而误导 |

## 第 12 轮复核结果（原「待第 12 轮重新取证」清单）

第 11 轮 subagent 报了 20 条，但修复批次写入后工作区被回滚，只有 4 条完成复核与落地。第 12 轮把剩下 13 条逐条对回代码：**12 条成立并已修复**（见 `round-12.md` §三），仅以下 3 条继续留在 backlog：

| 条目 | 理由 |
|------|------|
| 启动日志级别可配（`--log-level`） | `configure_logging` 固定 `INFO`；没有真实「谁会改」的场景，先不加开关 |
| 空态/加载态 `aria-live` 播报 | 需要逐页设计播报文案，否则会形成朗读风暴，属体验设计 |
| 会话过期时各页 401 跳转统一 | 已由 `base.js` 的 fetch 补丁兜底，逐页差异未造成实际故障 |

## 第 13 轮新增（未修，按严重度）

| 问题 | 位置 | 为什么暂不修 |
|------|------|--------------|
| [P1] 首次建索引同步读 EXIF，20k 库冷启动 11 s | `tiklocal/services/library_index.py`（`_record_for_path`） | 正确修法是「先入库 + 后台补时间元数据」（`time_metadata_version=0` → 探测线程回填），要同时改写入路径、探测队列与按时间排序的回退，属结构性改动 |
| [P2] 缩略图解码并发只有 2 路且无 single-flight，冷缓存 200 张 p95 7.4 s | `tiklocal/services/thumbnail.py:22` | 提高并发会顶高 Pillow 内存峰值（第 09 轮才为 222 MB 峰值加过 `draft()`），需按 CPU 数设上限 + 同路径 single-flight 并配套压测 |
| [P3] 取消落在「工具已退出、`on_outputs` 执行中」窗口 → `canceled` 但 `file_count=1` | `tiklocal/services/downloader.py:938-979` | 需把产物登记与状态写入放进同一事务，牵动 job 状态机 |
| [P3] 中途取消在库根留 `.info.json` 孤儿 | `tiklocal/services/download_tools.py:116`、`downloader.py:1072` | 需按 job 记录本次产生的 info 文件并在取消时回收，收益低 |
| [P3] 字面量端点被 `<job_id>`/`<collection_id>` 遮蔽（`/api/download/jobs/clear` 404 而非 405） | `tiklocal/web/downloads.py:273`、`:287`、`tiklocal/web/library.py:434` | 需调整路由顺序或改用 uuid 校验器，牵动前端调用，当前无真实调用方 |
| [P3] 多源指向同一目录 → 全部归第一个源 | `tiklocal/web/library.py:202`、`:210` | 需先定义「同目录多源」语义（重复行还是按序归属），属配置约定问题 |
| [P3] 源移出配置后回收站条目失联（`restore` 抛 `KeyError`） | `tiklocal/services/trash.py:86-97` | 回收站条目需冗余保存 `media_root`，涉及存储格式迁移 |
| [P3] i18n 6 条：`media_common` 英文键、`sub_locale` 类型、`_js_catalog_guard` 只扫模板、`search.js` 混排、JS 内拼串、`<html lang>` 静态 | `tiklocal/i18n*.py`、`tiklocal/static/*.js`、`templates/base.html` | 第 13 轮报告后改动未落到工作区（连续第二次回滚）；下一轮改为「改一条 → 立刻跑 `test_i18n.py`」的小步模式 |

## 第 16 轮新增（按严重度）

| 问题 | 位置 | 为什么暂不修 |
|------|------|--------------|
| [P3] 缩略图/索引只按 mtime 判新鲜，同 mtime 替换内容后封面与入库宽高永久陈旧 | `tiklocal/services/thumbnail.py:146`、`library_index.py:219` | 需要 `content_hash` 列 + 回填迁移，且缩略图与索引两处判定要一起改 |
| [P3] 下载无 URL 幂等：同一链接两标签各提交一次 → 两条任务、两份文件 | `tiklocal/services/downloader.py::enqueue` | 需先定产品语义（重复即合并 vs 允许重下） |
| [P3] 跨标签数据变更无广播：收藏/删除/集合在别的标签发生，本标签只同步主题 | `tiklocal/static/base.js:27-33` | `BroadcastChannel` + 每页订阅，单独立一轮做 |
| [P3] 列表页在别的标签删除后不重校验（详情页已修） | `tiklocal/static/library_page_controller.js` | 需要保留滚动位置与筛选状态的重载路径 |
| [P3] `config.yaml` 的 `download:` 段无读取入口，改了没反应也无日志 | `tiklocal/app.py:391`、`tiktok_downloader.py:511-514` | 需先定 YAML 与 store 的优先级语义 |
| [P2] 服务管理器强杀后立即重启，日志轮转仍撞 WinError 32 | `tiklocal/services/logging_setup.py:89-94` | 单实例锁只挡正常双开；需按 pid 命名日志文件并清理旧文件 |

## 第 18 轮新增（按严重度）

| 问题 | 位置 | 为什么暂不修 |
|------|------|--------------|
| [P3] 重叠源归属语义（两个源指向同一目录时后配置者整体消失） | `tiklocal/web/library.py:202`、`:210` | 第 15 轮已加 `overlap_warnings` 告警收口；彻底解决需要「源优先级」配置项与前端展示，属产品决策 |
| [P2] 备份/导出/恢复功能整体缺失（无 backup/export/restore 路由与 service） | 全仓 | 属功能缺口而非缺陷，需单独立项，且导出内容要先做敏感字段审查（token/路径） |
| [P3] `tiklocal/web/settings.py` 的 `safe()` 把统计异常吞成 0，页面上看不出「统计不可用」 | `tiklocal/web/settings.py` | 当前以日志留痕替代；要做用户可见提示需前端配合 |
| [P3] 首次运行/空态引导复查本轮未执行（派发的 agent 未收到本轮任务） | `tiklocal/app.py`、`templates/home.html` | 已在第 19 轮复查（空态 CTA 死胡同见第 19 轮 #7），本条关闭 |

## 第 19 轮新增（按严重度）

| 问题 | 位置 | 为什么暂不修 |
|------|------|--------------|
| [P2] 首次索引 EXIF 8.19 s（19,222 文件，占冷索引 76%） | `tiklocal/services/library_index.py::_extract_time_metadata_batch`、`_record_for_path` | 第 20 轮已把逐文件 EXIF 读取改为 8 线程池（`CAPTURE_WORKERS` + `_records_for_paths`），但仍同步阻塞 `_sync_locked`；剩下的结构性修法是「扫描先写 `time_metadata_version=0` + mtime 兜底，后台探测线程回填 `captured_at`」，会改变首屏时间线排序语义（探测完成前按 mtime 排），需单列一轮设计与压测 |
| [P2] 视频封面每张 1.36 s：先起一次 ffmpeg 读 Duration，再起一次抽帧 | `tiklocal/services/thumbnail.py::_probe_duration` / `_extract_frame` | 需要把时长持久化进 `media_items.duration` 并由扫描写入，否则只能固定时间点抽帧、牺牲封面质量 |
| [P2] 空态「添加媒体来源」CTA 跳到 `/settings/`，但设置页没有来源管理入口，用户无路可走 | `tiklocal/static/home_page_controller.js`、`templates/settings.html` | 来源由 CLI `--source` / `config.yaml` 决定，网页只能做只读列表 + 指引，属新功能（需先定「网页能否改来源」的产品语义） |

## 第 20 轮新增（按严重度）

本轮 10 条发现全部修复，未新增 backlog 条目。两项说明：

- 第 19 轮 P1（首次索引 EXIF）已在第 20 轮并行化，条目见上，仍保留结构化修法。
- `data-layer` 域（跨标签广播、下载 URL 幂等、数据层并发）本轮 subagent 中途失败、未交付报告，该域结论停留在第 16 轮的 backlog 条目，未被本轮重新验证。

## 第 21 轮新增（按严重度）

本轮 34 条成立发现中修 8 条（4×P2 + 4×P3），其余 26 条如下。C 域 #2 经复核实为伪阳性，已驳回（理由见 `round-21.md` §三）。

| 问题 | 位置 | 为什么暂不修 |
|------|------|--------------|
| [P2] 媒体来源存储读不出来时被整表覆盖：`StoreUnreadableError` 被吞成 `stored=None` 后照常 `save()`，完好的文件被重写、`removed_ids` 丢失 | `tiklocal/web/settings.py:139-145`、`tiklocal/services/json_storage.py:117-138` | `_apply_change` 已有 `except OSError → 500 source_error_save`，最小修法是删掉这里的 try/except；但要与下一条「运行态回写」同批改，否则会出现「能保存但语义错」的中间状态 |
| [P2] 启动目录「仅本次」顶替不可达的默认源后，任何一次增删改都把它永久写成用户配置 | `tiklocal/web/settings.py:147-158`、`tiklocal/services/media_sources_store.py:248-286` | 需给回退来源打标记，而 `MediaSource`/`StoredMediaSources` 都是 frozen dataclass，要扩字段 + 存储版本迁移 |
| [P2] 可把 `<home>`、`<home>/.tiklocal`、盘符根加为来源，`/media/@home/.tiklocal/auth.json` 与 `~/.ssh/id_rsa` 可下载（`validate_folder` 无保留目录判断，`serve_media` 无后缀白名单） | `tiklocal/services/media_sources_store.py:163-181`、`tiklocal/web/media.py:421-441` | 需在来源校验里拒绝等于/包含 `get_data_dir()`、`instance_path` 的路径；同时要决定「用户确实想挂载 home 时怎么放行」，属策略决策 |
| [P2] 索引损坏时 `/settings/` 直接 500，改目录的唯一入口打不开（`library_stats()` 的 `safe()` 对 `sqlite3.DatabaseError` 显式 re-raise） | `tiklocal/web/settings.py:32-40`、`:235-245` | 与第 18 轮「`safe()` 把统计异常吞成 0、页面上看不出统计不可用」合并处理：需要用户可见提示，前端配合 |
| [P2] 非抖音模式下从搜索进入 `/flow?start=…&caption=…`：服务端已窄化，但 chip/清除/返回全在默认 `display:none` 的 `.douyin-topbar` 内 → 时间线无声变少或空，无提示无出口，空态还复用「添加媒体并刷新索引」文案 | `tiklocal/static/flow_page_controller.js:719-728`、`tiklocal/static/flow.css:992`、`:1003-1004`、`tiklocal/web/flow.py:405-412` | 需新增 `caption-feed` body class + CSS 分列 + 专用空态文案，属 UI 改版 |
| [P2] 弹层打开时 ArrowDown/Up/Space 仍作用于底层 feed；作品 A 的 `/api/media/info` 响应把 A 的 path/尺寸/日期写进正在展示 B 的弹层 | `tiklocal/static/flow_page_controller.js:2302`、`:635`、`:658`、`:661-668` | 键盘守卫补 `mediaSheetIsOpen()` 是一行，但 `showInfoSheet` 的请求序号/AbortController 改造要连带作者页的同类调用一起做 |
| [P3] TLS 续期阈值固定 30 天而 `--tls-days` 允许 1..3650 → ≤30 天时每次启动都重生成、指纹每次都变 | `tiklocal/services/tls.py:424` | 阈值应随 `days` 缩放（如 `min(RENEW_BEFORE_DAYS, days // 3)`），但 `_reusable_material` 拿不到 `days`，需扩签名 |
| [P3] 证书与私钥两次 `os.replace` 非成对原子；`docs/https.md:47-48`「cannot leave a half-written pair」措辞强于实际保证 | `tiklocal/services/tls.py:494-495`、`docs/https.md:47-48` | 成对原子需「写临时目录 + 一次替换目录」，Windows 不支持目录原子替换，实际只能收窄文档措辞 |
| [P3] `tls generate` 不读 `config.yaml` 的 `hostnames`（只有 serve 路径读）→ 预生成证书下次 serve 被 `_metadata_covers` 判为不覆盖而重生成 | `tiklocal/run.py:432-439` | 需把 `config_tls_hosts` 的读取提前到 generate 分支，且要先定 CLI 与 config 的合并语义 |
| [P3] 出网探测拿到的源地址未经 `_is_lan_ipv4` 过滤就进 SAN → 公网 IP 进证书，与 `tls.py:195-197` docstring 承诺相反 | `tiklocal/services/tls.py:247-248` | 加一行过滤即可，但会改变「多网卡机器自动 SAN」的覆盖面，需实测各平台 UDP 探测返回值 |
| [P3] 文档教 Android「Install a certificate → CA certificate」，但自签 leaf 是 `CA:FALSE`，该入口会被系统拒绝 | `tiklocal/services/tls.py:543`、`docs/https.md:104-108` | 要么把证书改成 `CA:TRUE` + 正确 keyUsage，要么改文档引导走「VPN 和应用 → 安装证书」；需真机验证 |
| [P3] `validate_certificate_pair` 只验可读与配对，不查 notAfter/SAN → 过期自定义证书照常启动、握手才失败、启动零提示 | `tiklocal/services/tls.py:305-307`（经 `run.py:614`） | 需先定「过期就拒绝启动 vs 只警告」，后者要一条启动横幅提示通道 |
| [P3] 容器内 `--tls`（默认 8443）时健康检查仍打 `http:8000` → 恒 unhealthy；`EXPOSE 8000`、compose 只发布 8000 | `Dockerfile:38-39`、`:35`、`compose.yaml` | 需按 `TIKLOCAL_TLS` 分支健康检查与端口发布，属部署配置改版 |
| [P3] 两已登录会话同时改密码：都过 verify、都读 revision=N、各写 N+1，后写覆盖且都 200；`_save` 临时名固定，并发 `os.replace` 在 Windows 可能 PermissionError | `tiklocal/services/auth.py:90-101`、`tiklocal/web/settings.py:85-99` | 需 `AuthStore` 加 `threading.RLock` + `tempfile.mkstemp`（照 `DeviceAuthStore` 的模式），与来源存储的并发条目一起做 |
| [P3] 登录限流的 `defaultdict` 为每个失败过的源 IP 建键且永不回收（`retry_after()` 每个请求都调 `_active()`，只有成功才 pop） | `tiklocal/auth.py:20`、`:38-43` | 改 `self._attempts.get(key)` 一行即可，但**不能**在 `_active` 里 pop：`record_failure` 会追加到已脱离 dict 的 deque，计数清零、限流可绕过。需连带确认 `record_failure`/`clear` 语义 |
| [P3] 搜索结果每个 general/user 请求都跑 `blog_rows()`（1 条 GROUP BY + 每源 1 条 `LIMIT 1`）+ 每源 `resolve_profile()`，而 general 第 2 页起客户端已丢弃 users | `tiklocal/web/search.py:338-345`、`tiklocal/services/library_index.py:552-584` | 加「offset>0 且 tab=general 跳过作者块」会改变 `counts['users']` 语义，需先定「后续页是否仍显示作者总数」 |
| [P3] 命中来自文件名/目录名时仍挂 `&caption=`，而 `flow.py` 的 caption 收窄序列为空时会回落到推荐流 | `tiklocal/web/search.py:226`、`tiklocal/web/flow.py:208-213`、`:405-412` | 改用 `media_naming.post_title(uri)` 判定即可，但要连带确认「目录名命中」是否也该带 caption |
| [P3] OFFSET 分页在索引刷新期间会漏条（`ORDER BY mtime DESC, uri` + `LIMIT/OFFSET`） | `tiklocal/services/library_index.py:392`、`:414-417` | 需 keyset 游标（`WHERE (mtime, uri) < (?, ?)`），涉及三处查询与前端游标传参 |
| [P3] 搜索 tab 的 ARIA 不完整（panel 无 `aria-labelledby`），键盘只映射左右箭头、无 Home/End | `tiklocal/templates/search.html:42-57`、`tiklocal/static/search_page_controller.js:554-566` | 与第 20 轮弹层 ARIA 条目同批做，需真机读屏验证 |
| [P3] store 已落盘、运行态已切换后 `sync()` 抛非 busy 异常 → 500，前端显示失败且对话框不关，重试撞 duplicate/not_found | `tiklocal/web/settings.py:172-187` | 需把「已保存但索引未同步」表达成警告而非错误，前端要新增状态分支 |
| [P3] 来源读-改-写无锁：两标签页并发加目录后写者整文件覆盖 | `tiklocal/web/settings.py:147-158` | `_apply_change` 加模块级 `threading.Lock` 即可，但与上面两条回写语义条目同批改更安全 |
| [P3] 移除来源后 `<source>/.tiklocal-trash` 仍在磁盘，回收站列表与保留期清理只遍历现役来源 → 文件永久占盘且不可见 | `tiklocal/services/media_sources_store.py:228-245`、`tiklocal/services/trash.py:86-112` | 与第 13 轮「源移出后回收站失联」同源，需存储格式迁移（记录已移除来源的回收站路径） |
| [P3] 默认来源改路径时只迁移缩略图缓存身份，`@default/rel` 形态的收藏/合集/播放进度/回收站条目全部改指新目录，同名文件静默错配 | `tiklocal/web/settings.py:161-166` | 需「改路径 = 新源 + 迁移引用」的整体设计，涉及 favorites/collections/progress 三处存储 |
| [P3] 媒体弹层无焦点归还、无焦点陷阱；焦点离开弹层后 Escape 完全失效 | `tiklocal/static/flow_page_controller.js:571-577`、`:2249-2254` | 需 `previousFocus` + Tab 循环 + document 级 Escape 分支，与第 20 轮弹层焦点条目同批做 |
| [P3] 「全部下载」用 N 个 `setTimeout` 触发 `<a download>`，全部丢失用户手势（Chrome 只落第一个），重复点击无 busy 门 | `tiklocal/static/flow_page_controller.js:596-604`、`:678-706` | 需先实测浏览器多文件下载策略；恢复服务端流式 zip 是备选但属新功能 |
| [P3] `/api/media/info` 把服务器绝对路径回给浏览器（前端展示 + 复制），与同文件 `/delete` 的「不泄漏绝对路径」政策相反 | `tiklocal/web/media.py:483` | 改回 canonical uri 或 `source_id + rel_path` 会改变前端展示与「复制路径」语义，需产品决策 |

## 第 22 轮新增（按严重度）

本轮 68 条域报告发现 + 9 条独立复审意见中修 18 条（3×P1 + 7×P2 + 8×P3），驳回 1 条，其余 54 条如下。P1 全部当轮修完，故下表从 P2 开始。**优先做**：电台列表的探测上限（第一行）与索引损坏时的设置页（第十一行）。

| 问题 | 位置 | 为什么暂不修 |
|------|------|--------------|
| **[P2] 优先** 电台列表冷启动逐条探测，而「每请求探测上限」这个约束**从未实现**：`grep MAX_PROBE` 只命中 `docs/audit/backlog.md:26` 与本文件同条 `round-04.md:81`，`git log -S` 无提交。`limit` 可到 500，8 线程逐条 ffprobe（失败再落 `banner_duration` 的 `timeout=10`），最坏 200/8×10 s ≈ 250 s 占满 waitress | `tiklocal/web/radio.py:79-83`、`tiklocal/services/radio.py:250-259`、`:264`、`:296`、`tiklocal/services/thumbnail.py:109-125` | 要么真的实现上限（先返回、后台补时长），要么改成「时长缺失时前端按需拉」，两种都要动 `radio.py` 的响应契约与前端回落逻辑 |
| [P2] `records()` 剩下的调用点仍在整类物化：`/flow` 每请求对 video、image 各一次，`CANDIDATE_TTL_SEC = 2.0` 让缓存几乎每请求失效 | `tiklocal/services/library_index.py:249-254`、`tiklocal/services/recommendation.py:64-71` | 本轮只修了详情页侧（新增 `neighbors()`）。`/flow` 侧要给 `_candidates` 做列投影（只需 `uri, mtime`）并重定缓存策略，属推荐算法输入改造 |
| [P2] 尺寸探针在退避条目 >400 时活锁：`skip` 被 `[:400]` 截断，被截断的尾部每轮重新被返回、再退避，`results` 仍空 → 每轮空转 5 s + 一次 400 项 `NOT IN` 查询；`_warned` 超 500 整体 `clear()` → 同一批 URI 反复 WARNING，数万条/轮冲掉轮转日志 | `tiklocal/services/library_index.py:1090-1093`、`:1110`、`:1138-1141`、`:1154` | 要重构索引扫描主循环的退避队列（`if not results and 本轮无新增退避: break` + 有界 LRU），需单独设计可复现的测试 |
| [P2] `timeline_months` N+1：每个月份一次 `SELECT *`×120 行，`/library` 首屏 10 条查询、`/api/library/timeline` 最多 38 条、物化 4320 行后只采样 ≤18 条 | `tiklocal/services/library_index.py:479-504` | 改写成 `ROW_NUMBER() OVER (PARTITION BY capture_month ...)`（同文件 `on_days` 已有写法），但要逐条核对与现有排序/去重语义等价 |
| [P2] `image_random` 每页全量取回并全量洗牌（无 LIMIT 的 `SELECT media_items.*` → `fetchall()` → `shuffle` → 切片），`total` 也依赖全量 | `tiklocal/services/library_index.py:407-411`、`tiklocal/web/library.py:35-45` | 需稳定的伪随机序（rowid 线性变换或 uri 稳定 hash 排序）才能配合 `LIMIT/OFFSET`，且 `total` 语义要一并改，前端「随机」体验不能变 |
| [P2] `time_states()` 在每次 `register_uris` 全表读（无 WHERE，19k 库 ≈ 20k 行 + 20k dict），且整段在 `_sync_lock` 内与扫描串行；调用方是每个下载 job 完成回调 | `tiklocal/services/library_index.py:586-596`、`:899-903`、`tiklocal/app.py:498` | 需 `time_states(uris=...)` 分块（`existing_uris`/`profiles_for` 已有写法），并连带验证取消/失败 job 的回调路径 |
| [P2] `sync()` 与 `register_uris()` 对同一把锁语义相反（前者非阻塞返回 busy，后者阻塞）→ 回收站恢复请求与下载回调会等满整轮扫描（19k 冷索引注释 8.19 s） | `tiklocal/services/library_index.py:853`、`:899`、`tiklocal/web/media.py:303` | 改 `acquire(timeout=…)` 要定「超时后谁负责补索引」，现有「下次扫描自愈」分支未覆盖恢复路径 |
| [P2] 全量索引在监听端口前/请求线程内同步跑：`create_app()` 里 `sync()`，同一函数还被 `POST /api/library/sync` 与设置页在请求线程直接调用 | `tiklocal/app.py:428`、`tiklocal/web/library.py:395`、`tiklocal/web/settings.py:174` | 需后台线程 + 进度接口（`MediaProbeWorker.start()/wake()` 是现成模式），属启动流程改版 |
| [P2] 请求线程里同步起 `--version` 子进程且无缓存：入队一次、`probe_dependencies()` 三次、失败还加长重试 → 工具卡住时设置页最坏 105 s 占死 waitress 线程 | `tiklocal/services/downloader.py:580`、`:496-498`、`tiklocal/services/download_tools.py:195` | 加模块级 memo 要处理「用户刚装好 yt-dlp」的失效场景（TTL 或手动刷新入口） |
| [P2] 视频封面固定多起一次 ffprobe（`_probe_color_transfer` 无按文件缓存），黑场/暗场最多 1+3 个进程，全程持 `_GENERATE_SLOTS` | `tiklocal/services/thumbnail.py:521`、`:535-573` | 需把 duration 与 color_transfer 合并成一次 ffprobe 并按 `(path, mtime_ns)` 缓存，`auto_timestamp` 路径要一起测 |
| **[P2] 优先** 索引损坏时 `/settings/` 整页 500：`library_stats()` 对 `sqlite3.DatabaseError` 显式 re-raise，而 `settings_view` 直接调用 → 改目录的**唯一入口**打不开；同页 `/api/library/sources` 却专门兜住，自相矛盾 | `tiklocal/web/settings.py:32-40`、`:242-245`、`:193-201` | 需前端配合的「统计不可用」提示条（与第 18 轮 `safe()` 条目合并），stats 失败时回退 `index_corrupt` 标志照常渲染 |
| [P2] 来源变更后索引状态不一致：`sync()` 只捕 locked/busy 的 `OperationalError`，其余 `raise` → 500 且无回滚；busy 分支返回 `{'busy': True}` → 被移除来源的 `media_items` 行不清，`page()` 不过滤 source → 幽灵行出现在 `/library`，点开 404 | `tiklocal/web/settings.py:157-187`、`tiklocal/services/library_index.py:850-864` | 需定「已保存但未同步」的状态语义 + 幽灵行清理，前端要新增状态分支 |
| [P2] 作者来源列表只为数个数就全量读：`len(local_work_names(media_index, source_id))`，而 `local_work_names` 全量 `records(source_id=...)`；每源还两次读 profile 文件 | `tiklocal/web/author.py:369`、`:104-115`、`:360`、`:367` | 需新增 `count_works(source_id)` 聚合查询，并确认 `local_work_names` 的其它调用点是否也要换 |
| [P2] 作者页无分页：`build_tiles(media_index.records(source_id=..., album_covers_only=True))`，模板裸 `{% for tile in tiles %}` | `tiklocal/web/author.py:201`、`tiklocal/templates/author.html:51` | 属 UI 改版（分页或无限滚动 + 缩略图懒加载），要连带作者页的搜索/排序状态 |
| [P2] `collect_candidates()` 无缓存：每次换台全扫 `records(media_type='audio')` + 每条 `resolve_path()`（含 2 次 `Path.resolve()`）+ `is_file()` | `tiklocal/services/radio.py:202-227`、`tiklocal/static/radio_controller.js:282-290` | 照 `recommendation.py:79-87` 加 TTL 缓存即可，但要定失效条件（下载完成后 / 库变更） |
| [P2] 能量包络无同 URI 去重、无锁、无负缓存：并发 N 请求 = N 个 ffmpeg 全曲解码；超时分支 `_kill_process` 后**不 close `process.stdout`**（句柄泄漏）；`_write_cache` 把 OSError 全吞 | `tiklocal/services/radio_energy.py:49-63`、`:155-188`、`:81-96` | 需按 URI 做 single-flight + 负缓存 + 超时关管道，属并发重构 |
| [P2] caption 收窄路径每页全库读 + 双次 collapse：无 `source_id` 时 `records()` 全库，随后无条件 `collapse_media_entries`，命中收窄序列后再 collapse 一次才切片 | `tiklocal/web/flow.py:383-393`、`:403`、`:406-407`、`:413-419` | 需先按 source 过滤再 collapse，且要确认「跨源 caption 搜索」的产品语义 |
| [P2] 媒体弹层无请求令牌 + 键盘穿透（同一根因）：`showInfoSheet` 落地只判 `mediaSheetIsOpen()`，不判「当前展示的还是不是这一件」→ 作品 A 的响应写进展示 B 的弹层，「复制路径」复制到上一件；全局 keydown 前置条件缺 `mediaSheetIsOpen()` | `tiklocal/static/flow_page_controller.js:613-673`、`:2302` | 键盘守卫是一行，但请求令牌（`sheetToken` + 落地校验 `currentItem() !== item`）要连带作者页同类调用一起做 |
| [P2] 相册首帧在 build 阶段 eager 拉原图（普通图片走 `loading='lazy'`） | `tiklocal/static/flow_page_controller.js:1336-1344`、`:1474`、`:1577-1592` | 需区分「首帧可见」与「预加载」，否则会引入滑动时白屏 |
| [P2] `refreshJobs` 无请求代际号：轮询响应后到会覆盖删除/取消结果，被删 job 回到列表（最长 12 s 后自愈） | `tiklocal/static/download_page_controller.js:579-604` | 照 `search_page_controller.js:337,399` 的 `requestSeq` 加即可，但要与本轮新加的 `historySignature` 一起测 |
| [P2] 电台页 `localStorage` 裸读写：模块作用域 `getItem` 抛错（存储被禁/配额满）→ `init()` 从不运行，整页哑；`setItem` 抛错 → 状态已改但 UI 与场景停在旧值 | `tiklocal/static/radio_controller.js:71`、`:92`、`:98`、`:658-662`、`:787-793`、`:1087-1094` | 需统一 `readJson/writeJson` 包装并定写失败时的回滚策略（先应用状态还是先落盘） |
| [P3] `DeviceAuthStore` 仍是固定 `.tmp` 名 + 裸 `os.replace`；`tiklocal auth set-password` 自建 `AuthStore` 且不持实例锁 → 服务运行中执行会争同一临时名 | `tiklocal/services/device_auth.py:148`、`tiklocal/run.py:432-453` | `AuthStore` 本轮已改 `write_json_atomic`，`DeviceAuthStore` 同一改法留待下一轮；CLI 加锁要动实例锁语义 |
| [P3] `DeviceAuthStore.verify()` 每个 `/api/v1/*` 请求整份读盘解析（无 mtime 缓存，`AuthStore` 有）；音频流一首歌多个 Range 请求 = 每请求一次读 + 解析 | `tiklocal/services/device_auth.py:55`、`:132-144` | 加 mtime 缓存即可，但设备令牌是安全边界，改动要连带 `tests/test_radio_client.py` 全量复核 |
| [P3] `CollectionStore._read()` 把任何 `Exception`（含 OSError）当损坏返回空 payload，与 `json_storage` 契约相反：瞬时独占时合集列表短暂消失 | `tiklocal/services/collections.py:83-94` | 应只隔离 `JSONDecodeError`/`UnicodeDecodeError`、OSError 走 `StoreUnreadableError`；但 `_read` 有 6 个调用点，需逐个确认「读不到」时前端该显示什么。**注意**：第 22 轮复审建议「读路由补 503」，核实后不可达——`strict=False` 时这个 `except Exception` 已经把读失败吞成空 payload，读路由永远拿不到 OSError；只有改掉 `_read` 本身才需要给读路由加兜底 |
| [P3] 集合摘要每张卡片整份读 `collections.json`：`list_for_media(include_items=False)` 仍触发 `list_item_uris()`，`_build_library_page(collection_id=...)` 再一次 | `tiklocal/web/library.py:128`、`:169-173`、`:531-537` | 需让 `list_for_media` 走 `list_with_items()` 的同一份快照，属数据层接口调整 |
| [P3] `page()` 每页跑两次全表条件扫描（`COUNT(*)` + `LIMIT/OFFSET`），`media_type IN (...)` 无索引前缀 | `tiklocal/services/library_index.py:413-417`、`:658-665` | `has_more` 改 `LIMIT limit+1`、`total` 只在 offset=0 时取；但 `total` 被前端进度条依赖，要先定「后续页 total 从哪来」 |
| [P3] `blog_rows()` N+1：`GROUP BY source_id` 后对每个 source 单独 `SELECT uri ... LIMIT 1` | `tiklocal/services/library_index.py:566-576`、`tiklocal/web/library.py:387` | 可用 `ROW_NUMBER()` 合并；改动小但收益随来源数（≤24）有限，优先级低 |
| [P3] `_record_many_once` 每批印象事件都全表 prune（`DELETE ... NOT IN (子查询)`），表未满 20k 时也全扫且持写锁 | `tiklocal/services/database.py:548-557` | 改 `id <= MAX(id) - ?` 或仅超限时执行即可，但属写入热点路径，要连带压测 |
| [P3] backlog 里「`skipped_prefixes` 没回给前端」口径需修正：后端已写入、`POST /api/library/sync` 直接返回、测试已断言；`static/*.js` 无任何读取 → 后端与 API 已闭环 | `tiklocal/services/library_index.py:889-890`、`tiklocal/web/library.py:401` | 剩余缺口纯在前端提示（扫描跳过了哪些目录），降级为前端事项 |
| [P3] 每个任务结束都无条件跑一次 `taskkill`（不在 `poll() is None` 保护内），每次下载多起一个进程、取消/超时路径 2-3 次 | `tiklocal/services/downloader.py:1450-1455`、`:1061-1065` | 先判 `poll() is None` 即可，但属进程生命周期路径，要连带 `test_downloader_lifecycle.py` 复核 |
| [P3] 子进程未加 `CREATE_NO_WINDOW`（全仓无 `creationflags`）：从 pythonw/快捷方式启动时每次 yt-dlp/ffmpeg/`--version`/`taskkill` 都弹控制台窗口，批量缩略图持续闪窗 | `tiklocal/services/downloader.py:1432`、`download_tools.py:194`、`thumbnail.py:462`/`480`/`505`/`550` | 需逐站点验证（某些工具在无控制台时行为不同），且终端启动时不可见、难自动测 |
| [P3] 来源映射每次读取都整文件重解析（`get_many` 锁内 open + `json.load`），`/flow` 每渲染一次执行一次，条目随下载量线性增长 | `tiklocal/services/download_sources.py:169`、`:198-210`、`tiklocal/web/flow.py:35` | 加进程内缓存 + mtime 校验即可，但要处理「另一进程写入」的失效 |
| [P3] 电台 `_metadata_cache` 无上限无淘汰（`str(path)` 为键，mtime 变了不删旧键） | `tiklocal/services/radio.py:146`、`:241-248` | 改有界 LRU 即可，收益随曲库规模，优先级低 |
| [P3] `_select_favorites` 在列表推导条件里每轮重建 `{chosen.name for chosen in selected}` → O(n×limit) | `tiklocal/services/radio.py:307-336` | 一行提到循环外即可，属纯热点微优化，等 #16 的缓存落地后一并做 |
| [P3] 媒体弹层无焦点归还、Escape 只在弹层内有效（监听挂在 `#media-sheet` 自身），`role="dialog" aria-modal="true"` 却无焦点陷阱 | `tiklocal/static/flow_page_controller.js:560-577`、`:2249-2254`、`:2291-2330` | 需 `previousFocus` + Tab 循环 + document 级 Escape 分支，与第 20 轮弹层焦点条目同批做 |
| [P3] 「全部下载」用 N 个 `setTimeout` 触发 `<a download>`，全部丢失 user activation（Chrome 只落第一个），重复点击无 busy 门 | `tiklocal/static/flow_page_controller.js:596-604` | 需先实测浏览器多文件下载策略；恢复服务端流式 zip 是备选但属新功能 |
| [P3] Quick Viewer 的 `play()` 竞态把暂停图标画到新项 | `tiklocal/static/library_page_controller.js:816-818` | 需给播放状态加代际号，改动小但难自动测（无 JS 测试框架） |
| [P3] 前端缓存只增不清：`captionCache`/`sourceState`/`collection_picker.cache` | `tiklocal/static/flow_media_actions_controller.js:19-26`、`tiklocal/static/collection_picker.js:79-91` | 需按「会话内可见条目数」定上限，属内存策略决策 |
| [P3] 瀑布流只增不减、`dropHead` 在 library 从未调用 | `tiklocal/static/library_page_controller.js:440-486` | 与第 20 轮 `dropHead` 条目同源（backlog 已驳回动画部分）；DOM 回收要连带滚动位置恢复一起设计 |
| [P3] 时间线滚动无节流且每次读 `offsetTop` | `tiklocal/static/library_timeline_controller.js:374-376` | 加 rAF 节流即可，但要实测长列表下的粘性标题定位 |
| [P3] `collection_picker.open()` 无陈旧守卫 | `tiklocal/static/collection_picker.js:148-169` | 与弹层令牌条目同批做 |
| [P3] 设置页来源读-改-写无锁：两标签页并发加目录后写者整文件覆盖 | `tiklocal/web/settings.py:130-145` | `_apply_change` 加模块级 `threading.Lock` 即可，但与第 21 轮两条回写语义条目同批改更安全 |
| [P3] 默认来源改路径时只迁移缩略图缓存身份，`@default/rel` 形态的收藏/合集/播放进度/回收站条目全部改指新目录，同名文件静默错配 | `tiklocal/web/settings.py:163-166` | 需「改路径 = 新源 + 迁移引用」的整体设计（与第 21 轮同条合并） |
| [P3] `set_profile_url` 无 try（磁盘失败 500），而同文件 `_sync_profile` 捕 Exception → 502 | `tiklocal/web/author.py:299`、`:314`、`:228-277` | 补 `except OSError → 502` 即可，但要统一作者页所有写路由的错误码 |
| [P3] 搜索：命中来自文件名/目录名时仍挂 `&caption=`；general tab 每页都算 users 且第 2 页起客户端丢弃 | `tiklocal/web/search.py:213-215`、`:324-333`、`tiklocal/static/search_page_controller.js:390-393` | 与第 21 轮两条同源（`post_title` 判定 + offset>0 跳过作者块），合并处理 |
| [P3] TLS 续期阈值固定 30 天而 `days` 允许 1..3650 → ≤30 天时每次启动都重生成 | `tiklocal/services/tls.py:424`、`:49`、`:379-380` | 与第 21 轮同条合并（阈值应随 `days` 缩放，但 `_reusable_material` 拿不到 `days`） |
| [P3] 证书与私钥两次 `os.replace` 非成对原子 | `tiklocal/services/tls.py:494-495`、`:420` | 与第 21 轮同条合并（Windows 不支持目录原子替换，实际只能收窄文档措辞） |
| [P3] 搜索 tab 的 ARIA 不完整：panel 无 `aria-labelledby`、三个 tab 无 `id`；键盘只映射左右箭头，无 Home/End | `tiklocal/templates/search.html:43-57`、`tiklocal/static/search_page_controller.js:568-577` | 与第 21 轮同条合并（补 `id` 是前置条件），需真机读屏验证 |
| [P3] 睡眠定时器每秒重建 `btnSleep.innerHTML` 且输出恒定（文案取配置值而非剩余时间），每秒一次 `getElementById` | `tiklocal/static/radio_controller.js:510-553` | 改单个 `setTimeout(deadline)` 即可；若要显示剩余时间则是 UI 决策 |
| [P3] `playback_resume.js` 后台仍累计观看时长（`timeupdate` 采样无 `document.hidden` 判定）→ 服务端 `visible_ms` 虚高 | `tiklocal/static/playback_resume.js:47`、`:84-88` | 一行 `if (document.hidden) return;`，但会改变观看统计口径，需先确认产品预期 |

## 第 23 轮新增（按严重度）

本轮 51 条去重发现中修 24 条（6×P1 + 10×P2 + 8×P3），其余 27 条如下。P1 全部当轮修完，故下表从 P2 开始。**优先做**：CSRF token 跨标签冻结（第一行）与七个 `aria-modal` 弹层的焦点陷阱（第七行）。

| 问题 | 位置 | 为什么暂不修 |
|------|------|--------------|
| **[P2] 优先** CSRF token 在脚本加载时冻结进内存：另一标签重新登录后 token 轮转，本标签所有写请求 403，而统一处理只拦 401 → 收藏/删除/进度静默失败直到手动刷新 | `tiklocal/static/csrf_fetch.js:4-5`、`tiklocal/static/base.js:157` | 需要「403 且已知 token 轮转过」的判定与自动刷新策略，直接 reload 会丢当前滚动位置与播放进度；先要确定哪些页面允许刷新 |
| [P2] `hideFlowStatus()` 只移除 `is-visible`，不复位 retry/next/library/actions 的 `hidden` → 不可见按钮仍可 Tab 聚焦、回车会导航离开 | `tiklocal/static/flow_page_controller.js:233-238` | 与「弹层焦点归还」同批做更安全：复位 `hidden` 后焦点可能落在已隐藏元素上，需要同时安排焦点落点 |
| [P2] 切换条目时 `onSourceChange` 丢弃 name 参数，`updateSourceButton` 也不校验 → 慢响应把「来源」按钮指向上一条的 URL | `tiklocal/static/library_page_controller.js:636-647`、`:689-690` | 一行加 `currentItem()?.name === name` 守卫即可，但要在真实慢网络下验证（本地响应太快无法复现） |
| [P2] 提示词弹层 `role="dialog" aria-modal="true"` 但打开不移动焦点、无 Tab 陷阱、关闭不归还焦点 | `tiklocal/static/image_detail_page_controller.js:293-301`、`tiklocal/templates/image_detail.html:172` | 需要通用焦点陷阱工具（本轮只做了 6 条 P1 里的可见性修复），一次性覆盖 7 处弹层 |
| [P2] 全屏层无 role/aria-modal、打开不移动焦点、Esc 后焦点落在已隐藏的关闭键上 | `tiklocal/static/image_detail_page_controller.js:172-203`、`tiklocal/templates/image_detail.html:145` | 同上，且全屏层与浏览器原生 Fullscreen API 的焦点行为要一起验 |
| [P2] 首页「那年今日」链接用 `firstChild.nodeValue` 覆盖文案，链接与无障碍名只剩日期 | `tiklocal/static/home_page_controller.js:119` | 需要新增 i18n 键（带日期占位符），而本轮约束是「不改 i18n 键」；文案措辞需产品确认 |
| **[P2] 优先** 七个 `aria-modal` 弹层只切 `hidden`、无 Tab 焦点陷阱（设置页 5 个、作者页 2 个、下载删除确认 1 个、媒体信息弹层、Quick Viewer）；同一批文件里作者页还有裸 `localStorage` 未包 try | `tiklocal/static/settings_page_controller.js`、`static/author_page_controller.js`、`static/download_page_controller.js:714-750`、`templates/flow.html:183`、`templates/library.html:232` | 做成一个 `trapFocus(container, {onClose, returnFocus})` 工具最省事，但要先统一 7 处弹层的打开/关闭回调形状；作者页裸 `localStorage` 属同一批改动 |
| [P2] 电台 `role="listbox"` 只处理 Escape，方向键无效 | `tiklocal/static/radio_controller.js:214-219` | 需要按 listbox 语义补 roving tabindex + Home/End，属可访问性专项 |
| [P2] `#station-options` 自 `fc4551a` 起恒为 `class="hidden"`，切换入口不存在 | `tiklocal/templates/radio.html:160` | 需产品决策：恢复电台切换入口，还是删掉这段 DOM |
| [P2] 作者同步后整列重建，覆盖另一行未提交的输入 | `tiklocal/static/settings_page_controller.js:963-995`、`:1045` | 与第 22 轮「同步后重建列表」同源，需要「按行更新 + 脏值保护」的列表渲染改造 |
| [P2] 电台页 `height: 100dvh; overflow: hidden; align-content: center`，横屏手机底部控制条被裁掉一半且整页不可滚 | `tiklocal/static/radio.css:36-39`、`:48-60` | 需要真机/视口模拟验证（本轮只验了 1280×900）；`100dvh` 与移动浏览器工具栏的交互也要一并测 |
| [P2] Quick Viewer 的 5 个 46px 按钮在 ≤420px 高视口被 `overflow: hidden` 裁掉最上面的倍速键 | `tiklocal/static/library.css:503-516` | 同上，需多视口验证；可能要把按钮组改为可换行或横向滚动，属布局决策 |
| [P3] 图片详情页的键盘守卫放在按钮守卫之后（Escape 分支在按钮上失效） | `tiklocal/static/library_page_controller.js:941-945` | 一行调整顺序即可，但与 #3 的焦点落点问题一起改更稳 |
| [P3] 合集弹层 `restoreFocus` 不判可见性，焦点落到 body | `tiklocal/static/collections_page_controller.js:85-89`、`:117`、`:137` | 需 `isVisible(el)` 工具，与焦点陷阱同批做 |
| [P3] 年份弹层选中后焦点掉回 body、无 Esc、无 `aria-expanded` | `tiklocal/static/library_timeline_controller.js:202-206` | 与焦点陷阱同批；`aria-expanded` 要同步到触发按钮 |
| [P3] 缩放按钮的 aria-label 覆盖百分比且数值变化不播报 | `tiklocal/static/image_viewer_controller.js:31-34` | 需要 `aria-live` 或 `aria-valuetext` 方案，需真机读屏验证 |
| [P3] `.mode-tabs` 是普通 div 带 aria-label（被忽略），无 `aria-pressed` | `tiklocal/templates/library.html:60-64` | 需要把 div 改成 tablist 或按钮组，会牵动 CSS 选择器 |
| [P3] live region 与内容同帧插入，首个提示被静默 | `tiklocal/static/ui_feedback.js:17-22` | 需 `requestAnimationFrame` 或延时插入，属可访问性细节，需读屏实测 |
| [P3] `/favorite` 的底部补偿为 0，`.organize-bar` 固定悬浮压住最后一排卡片 | `tiklocal/static/library.css:1-3`、`:1125-1141` | 需多视口验证；补偿值与 organize-bar 实际高度耦合 |
| [P3] 设置页写 `tiklocal:lang` 但全站无人读取（i18n 读 `<html lang>`） | `tiklocal/static/settings_page_controller.js:471` | 删一行即可，但要确认没有第三方脚本或未来计划依赖该键 |
| [P3] `ui_feedback.js` 的 `removeItem` 在 `JSON.parse` 之后，损坏值永久驻留 | `tiklocal/static/ui_feedback.js:83-85` | 一行调整顺序；属极低频路径 |
| [P3] `tiklocal:vt-from` 在守卫之前写入且从不删除，产生与操作无关的 hero 过渡 | `tiklocal/static/page_transitions.js:34` | 需理清 View Transition 的生命周期，属动画细节 |
| [P3] 语言切换 else 分支的 `setItem` 无 try，存储被禁时跳过主题按钮同步 | `tiklocal/static/settings_page_controller.js:80` | 与已修的抖音开关同类，漏了这一处；一行即可 |
| [P3] `pagehide` 上报活动后置空 `activeActivity`，bfcache 恢复时无 `pageshow` 重建 → 当前条目 visible_ms 丢失 | `tiklocal/static/flow_page_controller.js:2384-2386` | 需要与 `flow_media_actions_controller.js:35` 的 pageshow 处理对齐；观看统计口径要先确认 |
| [P3] 导入 textarea 无 label / aria-label | `tiklocal/templates/download.html:66` | 需要新增 i18n 键（本轮约束不改键），或复用现有文案 |
| [P3] 后台仍累计观看时长（`timeupdate` 采样无 `document.hidden` 判定） | `tiklocal/static/playback_resume.js:47`、`:84-88` | 与第 22 轮同条合并（会改变观看统计口径） |


