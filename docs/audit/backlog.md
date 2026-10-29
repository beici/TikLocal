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

## 第 24 轮新增（按严重度）

本轮 49 条发现（去重后 46 条）中修 28 条（4×P1 + 19×P2 + 5×P3），其余 18 条如下。P1 全部当轮修完，故下表从 P2 开始。**优先做**：缩略图缓存键不含非默认来源路径（第一行）与 `max_concurrent=0` 等于无限并发（第二行）。

| 问题 | 位置 | 为什么暂不修 |
|------|------|--------------|
| **[P2] 优先** 缩略图缓存键只含默认根 + `@source_id/rel`，且只在默认源路径变化时重算：非默认来源改到别的目录、或删源后新建同名文件夹复用同一 id（`_unique_id` 只看当前列表）→ 新目录里 mtime 更旧的文件命中旧缓存，网格显示上一个目录的画面 | `tiklocal/services/thumbnail.py:167`、`tiklocal/web/settings.py:183-186` | 本轮试过「把全部来源路径排序后混进根键」，`tests/test_thumbnails.py::test_cli_cache_is_reused_by_web_and_isolated_between_sources` 两条参数化用例立刻失败——CLI 的 `generate_thumbnails()` 只认识默认根，键一扩 CLI 生成的缓存就不再被 web 复用（19k 图库启动会全部重生成）。要修必须同时把完整来源列表传给 CLI，属跨进程契约变更 |
| **[P2] 优先** 校验允许 `max_concurrent=0`，而 `_dispatch_locked` 把 0 当「无上限」→ 一次起整个 pending 队列（上限 5000）的线程与 yt-dlp 进程 | `tiklocal/services/downloader.py:204`、`:925` | 0 是文档化的「不限并发」语义（校验区间 0-16，UI 只给 1/2/4）。要改需先决定是禁止 0 还是给它一个真实上限，属产品决策 |
| [P2] 移除来源后该源 `.tiklocal-trash` 下的文件不再列出、restore 抛 KeyError、30 天保留期永不生效 | `tiklocal/services/trash.py:89-90`、`:106`、`tiklocal/services/media_sources_store.py:228-245` | 与第 22 轮同条合并：`entries()` 需扫历史 id/路径，或 `remove_source` 拒绝删除非空回收站；后者是产品决策 |
| [P2] 回收站目标路径比原路径长约 39 字符，全路径落在 221..260 时 `shutil.move`（MoveFileExW，未开 LongPathsEnabled）抛 OSError(winerror 3) → 这类文件永远删不掉，重试无效 | `tiklocal/services/trash.py:26`、`tiklocal/web/media.py:268` | 修法二选一：用 `\\?\` 长路径前缀构造目标，或把回收站移到数据目录。后者会改变「删除即同盘移动」的原子性假设，需迁移既有回收站 |
| [P2] `source.path.is_dir()` 与 `_coerce_path` 的 `.resolve()` 对 NAS/离线映射盘（`\\nas\media`、`Z:\`）无超时（Windows 默认 20 s 起）：打开设置页或轮询 `/api/library/sources` 会占住 waitress 线程，多来源可占满线程池；启动时对每个已存来源做同样调用 | `tiklocal/services/media_sources_store.py:350`、`:116` | 需把探测搬进线程池 + 超时 + `available: null` 三态，前端也要接受「未知」；启动路径同样要改 |
| [P2] 配置类型校验在构造 parser 之前执行，`port: "8000"`（带引号）会让 `--help`/`tls status`/`auth status`/`thumbs` 全部 exit 2，报错也不含实际读取的文件路径 | `tiklocal/run.py:259-260` | 校验要移到 `parse_args` 之后、只在 serve 路径做，但 `load_config()` 的返回值同时被 serve 与 7 个诊断子命令使用，改动会牵动全部入口 |
| [P2] compose 的 `user: "1000:1000"` + 宿主目录挂载：新克隆按文档 `docker compose up -d --build` 时 Docker 以 root 建 `/data`(0755)，容器内 `auth_store.ensure()` PermissionError → 裸 traceback | `compose.yaml:10`、`:18`、`tiklocal/app.py:251` | 需在本机 Docker 环境实测（当前没有），并决定用命名卷还是 `${TIKLOCAL_UID}` |
| [P3] 未配 sidecar 时 `/api/author/<id>/search` 返 503，而同条件的 `_sync_profile`/update-videos/`author_sync` 返 400 | `tiklocal/web/author.py:333` | 试过改 400，`tests/test_author_page.py` 两条用例失败（断言 503）。属产品语义变更，要同时改前端重试语义与用例 |
| [P3] `_backoff` 上限 400 且 `skip` 截断：未装 ffprobe 时第 401 个之后的 uri 永远进不了 skip，`while True` 空转到 5 s 预算耗尽 | `tiklocal/services/library_index.py:1193-1196`、`:1224-1227` | 需把「跳过原因」改成可增长的结构（或按原因聚合计数），牵动索引统计口径 |
| [P3] `"indexed": len(records)` 把因 ffprobe 缺失被跳过的行也计入，UI 报成功但文件永不可见 | `tiklocal/services/library_index.py:145` | 改口径会让「扫描完成」的既有前端文案与测试一起变，需先定 UI 展示 |
| [P3] `--dev` 时父进程先完整 `create_app()` 再起 reloader 子进程重跑 `main()`：启动扫描做两次，父进程留常驻 probe 线程抢 WAL 写锁 | `tiklocal/run.py:706`、`:739` | 需要在 reloader 父进程里跳过建库，但 `app.extensions` 与实例锁都依赖它；属启动流程重构 |
| [P3] 关闭时先串行终止所有子进程（每个 2 s wait + taskkill ≤15 s）之后才起算 5 s join deadline → 3 个并发 job 最坏约 50 s | `tiklocal/services/downloader.py:487-491` | 需改成「并行发终止信号 + 统一等 deadline」，要连带 `close()` 的返回值语义 |
| [P3] `finally` 先还原 SIGTERM 再 `download_manager.close()` → 收尾期间的第二次 Ctrl+C 直接终止，yt-dlp/ffmpeg 变孤儿继续往 `.tiklocal-download-tmp` 写 | `tiklocal/run.py:775-777` | 顺序调换即可，但「收尾期间忽略 SIGINT」是可用性决策（用户可能想强制退出） |
| [P3] `create_app()` 在监听端口前同步全量扫描；`MediaProbeWorker` 只有 `start()`，没有 `stop()/join()` | `tiklocal/app.py:428`、`:433-434` | 需要「先 bind 再扫」的启动顺序调整（会改变首屏可用时间语义），以及 probe 线程的优雅停止 |
| [P3] 相对 `TIKLOCAL_INSTANCE` 的 warning 在 root logger 有 handler 之前触发，只落 last-resort stderr，永远进不了 `tiklocal.log` | `tiklocal/paths.py:31-37` | 本轮已把 `configure_logging` 提前到 `main()` 开头，但这条 warning 更早——路径解析本身就是日志目录的来源，要修得重排二者的先后 |
| [P3] 纯 HTTP 在 `create_app()`（含全库索引）之后才 bind，端口被占时要等几分钟才报错，期间已建库、写 auth.json | `tiklocal/run.py:745-751` | 需把 `probe_listen` 提到 `create_app` 之前，但 `--tls` 分支的 probe 依赖证书生成结果，两条路径要重新排序 |
| [P3] 配置只校验已知键，`prot: 9000`/`medai_root:` 这类拼错静默忽略并按默认值启动，零 warning | `tiklocal/run.py:104-159` | 加顶层键白名单会与「未来新增键」冲突，需先定兼容策略（警告 vs 报错） |
| [P3] `docs/https.md` 称 `https: yes` 是 startup error，但 PyYAML 把 `yes` 解析成 True 放行、静默以 HTTPS 起在 8443（compose 只发布 8000）；`run.py:134-136` 的注释把同一事实说反 | `docs/https.md:32-34`、`tiklocal/run.py:134-136` | 改示例与注释即可，但要同时决定 `https: yes` 是「接受」还是「报错」——若报错需加类型白名单（与上一行同批） |

## 第 25 轮新增（按严重度）

本轮视角是**内存与资源预算 + 长时运行稳定性**：7 个域共 50 条发现，修 15 条（5×P1 + 7×P2 + 3×P3，其中 5 条由复审追加），其余 40 条如下（含复审新增 1 条）。P1 全部当轮修完，故下表从 P2 开始。**优先做**：下载任务对象只增不减（前两行，长跑进程的主要增长源）与缩略图缓存无淘汰（第三行）。

| 问题 | 位置 | 为什么暂不修 |
|------|------|--------------|
| **[P2] 优先** `_jobs`/`_job_order`/`_cancel_events`/`_progress_persist_at` 只在手动删除任务时回收，200 条上限只作用于落盘 → 一个常跑实例处理万级任务后常驻数十 MB，且 `_job_order` 越长每次遍历越慢 | `tiklocal/services/downloader.py:433-437` | 要在「保留历史给 UI 看」与「进程内只留活跃任务」之间划线：需要给已完成任务加 TTL 或改成按需从 jobs.json 读，牵动下载页历史列表与测试 |
| **[P2] 优先** 任务终态后 `download_payload` 仍随 job 常驻，而 jobs.json 每秒全量重写 → 2 并发时约 2-6 MB/s 的写放大并全程持锁 | `tiklocal/services/downloader.py:1466-1468` | 清空字段会让「已完成任务详情」变空，需先决定详情页是否改从产物目录反查；属产品取舍 |
| **[P2] 优先** 缩略图缓存无条目上限与淘汰：媒体被外部删除后 `.jpg` 永不回收，长期可涨到数 GB | `tiklocal/services/thumbnail.py:353-390` | 要引入 LRU 或按 mtime 清理，必须先解决「CLI 与 web 共用同一缓存目录」的并发写（两进程同时淘汰会互删对方正在写的文件） |
| [P2] 批量入队对每条 URL 各做一次全量落盘与 `probe_binary`，5000 条时单请求占死一个 waitress 线程数分钟 | `tiklocal/web/downloads.py:246-261` | 需改成「先校验全部、再一次性落盘」，并把探测结果缓存到进程内；会改变部分失败时的返回语义 |
| [P2] `/api/source` 未命中即整表重写来源映射，随库规模 O(n²) I/O（临时文件已改为唯一名 + 重试） | `tiklocal/services/downloader.py:1639-1646` | 写放大本身要改成追加日志或分片；本轮只修了「读失败覆盖整表」与固定 `.tmp` 两个真故障 |
| [P2] sidecar 响应 `read()` 无大小上限：`source=true` 取百条原始作品约 4 MB JSON 只为读 id | `tiklocal/services/tiktok_downloader.py:302-303` | 需要给 `urlopen` 响应加 `read(limit)` 并定义超限行为（截断会破坏 JSON），属外部接口契约 |
| [P2] `/thumb` 先 `read_bytes()` 再判 304：no-cache 下每次条件请求都白读整份缩略图 | `tiklocal/web/media.py:531-543` | 要改成 `send_file(conditional=True)` 或先比 ETag 再开文件；单次 ≤200KB，属读放大而非内存问题 |
| [P2] 每次 `GET /api/trash` 把回收站目录全量扫两遍（`entries()` + `expired()`），2000 条约占线程 1-2 s | `tiklocal/web/media.py:287-288` | 要合并成一次扫描并把结果同时给两个消费者；需确认前端是否依赖两次独立快照 |
| [P2] 候选缓存键取未校验的 `blog` 查询串且从不删键，进程内键空间只增不减 | `tiklocal/services/recommendation.py:79-87` | 加键校验会改变「非法 blog 退回默认池」的既有行为；上限策略要先定（LRU 还是按来源） |
| [P2] 标注元数据每次读写都整份解析并重写 JSON，20k 条约 10 MB 且全程持锁串行 | `tiklocal/services/metadata.py:15-17` | 要改成增量写（SQLite 或追加日志），牵动导出/备份格式 |
| [P2] 超 4 小时曲目提前断流致 ffmpeg 非零退出、恒返回 None，且每次请求重解 4 小时音频 | `tiklocal/services/radio_energy.py:225-226` | 需要给「超长曲目」定义降级策略（抽样解码或按段缓存），属算法改动 |
| [P2] AI 标注前先全分辨率解码再缩到 1536px，大 PNG 单请求峰值可达 300 MB 以上 | `tiklocal/services/captions.py:430-436` | 要复用缩略图管线的 `draft()`/`reduce()` 逻辑，但标注需要比 640px 更高的分辨率，阈值要单独定 |
| [P2] Quick Viewer 图片直取原图，翻页无守卫无节流：按住方向键约 30 req/s 打满线程池 | `tiklocal/static/library_page_controller.js:840` | 需要 `event.repeat` 守卫 + 请求代际号 + 预取窗口，与第 23 轮键盘守卫同批做更稳 |
| [P2] 隐藏的全屏层 eager 拉原图，控制器又 `new Image` 二次请求并常驻，24MP 峰值 200 MB+ | `tiklocal/templates/image_detail.html:163`、`tiklocal/static/image_detail_page_controller.js:51` | 要改成按需加载并在关闭时释放 `fullImage.src`；需确认打开动画的首帧不闪 |
| [P2] 放大镜关闭不清 `backgroundImage`，原图解码位图与 2.5× 光栅常驻，每换一张钉住一张大图 | `tiklocal/static/flow_ui_shared.js:108` | 一行即可清，但要在 `close()` 与「切换图片」两条路径都清，且不破坏正在放大的实例 |
| [P2] 房间音效 `cache` 只写不删：切过雨→雪→火→夜后四套 PCM 常驻约 70 MB（48 kHz），iOS Safari 有整页被回收风险 | `tiklocal/static/radio_ambience.js:49`、`:243-248` | 需要在 `stop()` 后 `delete cache[key]`，但连续切房间时重算 168 s 噪声会有 1-2 s 卡顿，要加淡出与预取 |
| [P2] 下载页 `api()` 无 `AbortSignal`，`refreshJobs → renderJobs → schedulePolling` 自链在后端半断时断掉，进度条永久冻结且零提示 | `tiklocal/static/download_page_controller.js:24-28` | 加 `AbortSignal.timeout()` 会走已有 catch 退避分支，但要确认 15 s 对慢 NAS 不误判 |
| [P2] 作者页 `postJSON` 无超时，`setBusy(true)` 后靠 `finally` 解锁 → 后端卡住时输入框与三个按钮永久禁用 | `tiklocal/static/author_page_controller.js:107-115` | 与设置页同款改法（`AbortSignal.timeout(120000)`），但要同时改「同步中」提示的文案与重试入口 |
| [P2] 损坏数据库副本无上限：每次启动隔离一份 `*.corrupt-*`，反复启动会把数据目录撑满 | `tiklocal/app.py:132-166` | 需按数量或总大小保留最近 N 份，并决定是否在 UI 提示用户清理 |
| [P2] 登录限流表满且所有桶仍在窗口内时，新键不记录（防的是「驱逐活桶=给攻击者清零」） | `tiklocal/auth.py:38-43` | 这是第 21 轮加 `MAX_KEYS` 时的有意取舍：驱逐一个仍在封禁的地址比少记一次失败更糟。要改需换成按前缀聚合或全局令牌桶 |
| [P2] `media_affinity` 每 uri 一行且只增不减，重下载/改名累积孤儿行，10 万行约 20 MB | `tiklocal/services/database.py:631-684` | 需在索引重建时对账（`replace_snapshot` 里删孤儿），但改名场景无法区分「改名」与「删除+新增」 |
| [P2] `Executor.map` 先建满 19k 个 Future，与 `records`、`time_states` 同刻存活，冷扫峰值 55-60 MB | `tiklocal/services/library_index.py:1105-1111` | 要改成分批 `submit` 或流式消费，牵动扫描进度回调与并发上限语义 |

P3：17 条（R1 3 / R2 3 / R3 1 / R4 3 / R5 2 / R6 1 / R7 4）。值得留意的三条：`tiklocal/services/trash.py` 的 `expired()` 与 `entries()` 各扫一遍（与上表 `/api/trash` 同条）、`tiklocal/static/library_page_controller.js:779` 的 `quickImage.src=''` 与别处释放写法不一致、`tiklocal/static/radio_scene.js:302-306` 换曲时每帧 `blend()`×2 + `uploadArt()`（每曲约 2200 万次数组写与 78 次 256² 纹理重传，约 18 曲/小时）。

### 第 25 轮复审后新增（1 条）

| 问题 | 位置 | 为什么暂不修 |
|------|------|--------------|
| [P3] 第二进程长期占用日志文件时 `shouldRollover` 恒为真，**每条**记录都会跑一遍失败的 `doRollover`（重命名 + 重开）。本轮修的是「失败后 handler 不再静默死亡」，但没有减少无谓 syscall | `tiklocal/logging_setup.py:84-93` | 加 30 s 冷却即可（记住上次轮转失败时刻），但改动落在日志路径上，收益只是 syscall 数量，留到下一轮与「日志级别/容量策略」一起看 |

复审给出的其余 8 条已在当轮补修（槽位顺序、draft 后判定、`acquire` 超时、批量 `get_many`、trash mtime 兜底、`_prune_quarantine` 的 `stat` 守卫、`wsgi.file_wrapper` 回退、超长 uri 前缀+哈希），详见 `round-25.md` 第十三节。

### 第 26 轮新增（13 条）

本轮视角是外部依赖与网络边界，多数条目卡在「要么改契约、要么改产品行为」，因此只登记不修。

| 问题 | 位置 | 为什么暂不修 |
|------|------|--------------|
| **[P1] 优先** 首次启动默认口令 `admin666` + 默认绑 `0.0.0.0`：同网段任何人可登录、删媒体、改口令把主人锁在外面。本轮只在非回环绑定且仍用默认口令时打印警告 | `tiklocal/services/auth.py:17`、`tiklocal/run.py:637-646` | 改随机口令会改变每个老用户的首次启动行为，且 `tests/test_auth.py:75-109` 明确锁定该契约 → 属产品决策（随机口令 + 首启打印到控制台？还是强制首次登录改密？） |
| **[P2] 优先** `OUTPUT_ENCODINGS` 把系统首选编码（zh-CN=cp936）排在 UTF-8 之前，而 cp936 解 UTF-8 字节几乎不抛错 → 中文媒体目录下 yt-dlp 的 `Destination:` 行 mojibake，已成功的下载被判无输出 | `tiklocal/services/downloader.py:78-97` | 两种顺序都有反例（cp936 控制台 vs UTF-8 子进程）。正确修法是「解出的路径不存在时换编码重试」，属结构性改动，需连带测试下载状态机 |
| [P2] 反代部署下 `remote_addr` 恒为 127.0.0.1：登录限流退化为单桶（一人失败全站被锁）、审计日志无真实来源 | `tiklocal/services/auth.py:222` | 需要 `ProxyFix` 或显式的可信代理白名单配置；默认信任 `X-Forwarded-For` 会让直连场景可被伪造，必须先定部署契约 |
| [P2] 一次 `POST /api/author/sync` 最多 200 条、逐条串行且每条最长 150 s → 单请求可占一个 waitress 线程数小时 | `tiklocal/web/author.py:431-444` | 要改成后台任务 + 进度轮询，牵动作者页前端与 `author_sync` 的既有同步语义 |
| [P3] `yt_dlp` 只展示 `--version`、无最低版本门禁；`error_line` 把含 `error:` 的行原样回显，老版本缺 `-S`/`--file-access-retries` 时报错不可诊断 | `tiklocal/services/download_tools.py:192-213` | 需要维护一张「最低版本」表并决定是拒绝还是警告；版本号解析在 nightly/master 上不稳定 |
| [P3] 模板字段上限 24+80+80 字符 + temp 路径 + media_root 可越 260 字符，落盘以 WinError 206 失败 | `tiklocal/services/download_tools.py:41-43`、`:125-131` | 要按 `media_root` 实际长度动态收缩标题预算，会让「同名文件」判定随目录深度变化，需先补长路径测试 |
| [P3] 域名先解析为公网、校验后再解析为 127.0.0.1 的 DNS rebinding TOCTOU；`is_local_host` 与 yt-dlp 各自独立解析、无绑定 | `tiklocal/services/downloader.py:253` | 彻底修法是把解析结果钉进连接（自定义 `HTTPConnection` 或 `--resolve`），yt-dlp 侧无法传递；收益低于改动面 |
| [P3] 作者作品更新逐个作品 N+1 请求，无批量端点 | `tiklocal/services/tiktok_downloader.py`（作者作品更新） | 取决于上游是否有批量接口；当前请求量（每作者每页 20 条）在限流预算内 |
| [P3] `--dev` 直接 `app.run(host=host)` 且默认 host 为 `0.0.0.0`。本轮已改为非回环时绑 `127.0.0.1` | `tiklocal/run.py:759-763` | 已缓解；彻底做法是 `--dev` 强制忽略 `--host`，但那会让「手机调试」场景不可用 |
| [P3] 未校验 `Host` 头；当前无 `url_for(_external=True)` 用于邮件/回调，影响面为零 | `tiklocal/web/*.py` 绝对 URL 生成 | 等真的引入外发链接（分享/回调）时再一起做，现在加白名单只会增加配置负担 |
| [P3] 无全局 `MAX_CONTENT_LENGTH`，上限由各端点自限（cookie 1 MB） | 全局配置 | 当前无大体积上传端点；加了反而会误伤未来的导入功能，等有上传需求时定 |
| [P3] vendor 资源（feather/hammer）无版本登记与升级记录，本地文件、无 CDN、无 SRI 需求 | `tiklocal/static/vendor/` | 需要在 `static/vendor/README` 记版本与来源；属文档债而非缺陷 |
| [P3] `author_page_controller.js:15` 裸 `localStorage.getItem` 在 IIFE 里抛出会让整页只剩静态内容 | `tiklocal/static/author_page_controller.js:15` | 沿用第 23 轮登记（同条），修复需统一 `safeStorage` 工具并在全站替换裸调用 |

驳回 1 条：`embedding.base_url` 的 302 跟随（`tiklocal/experiments/similarity/embedding.py:46`）——端点是用户自己在 `config.yaml` 里配的，与已驳回的「yt-dlp 重定向内网」同类；真正要防的是「响应内容决定 URL」，本轮已修那两条。

### 第 27 轮新增（19 条）

本轮视角是数据生命周期与删除语义，八域上报 52 条，14 条是跨域同一根因（缩略图指纹、
purge 清理范围、回收站目录命名各被两到三个域独立命中），修 19 条后余下 19 条登记在此。

| 问题 | 位置 | 为什么暂不修 |
|------|------|--------------|
| [P2] 改名/移动不迁移收藏与偏好键：`rename()` 只改 `media_items.uri`，收藏、合集成员、`media_state` 仍指旧 uri，改名即「取消收藏」 | `tiklocal/services/library_index.py:78-93` | 需要 uri 级联迁移 + 事务，且 CLI 改名与 web 改名共用同一段逻辑；误迁移会把两个文件的状态混在一起，要先定「同名冲突」语义 |
| [P2] 缩略图指纹只看源文件 mtime：从别处拷来的文件（mtime 被保留）会命中另一张图留下的缓存，永远显示错图 | `tiklocal/services/thumbnail.py:383-395` | 正解是「mtime + size + inode」组合，需要先测扫描期成本；只加一项会让每次扫描多一次 stat，收益不确定 |
| [P2] 实况照片（`.mov` 配对）不随主文件删除移动：iOS 导入的 `IMG_0001.HEIC` + `IMG_0001.MOV` 删一个留一个 | `tiklocal/web/media.py:200-233` | 需要先定义「配对」判定（同名不同扩展名 + 时长接近），再决定是否连带删除；错判会把两个独立视频当成一对 |
| [P2] 下载历史删除不清理 sidecar：`delete_jobs()` 只删任务记录与索引，`*.info.json` 留在目录里 | `tiklocal/services/downloader.py:713-724` | 复用回收站的 `sidecar_paths()` 即可，但删除历史还要触发索引删除，属结构性改动，留到下一轮「删除语义」复访 |
| [P2] 重试任务后 `output_files_rel` 重复累积：同一文件在列表里出现两次，删除时重复 move 触发 `FileExistsError` | `tiklocal/services/downloader.py:786-800` | 需要去重合并语义 + 旧数据迁移（已有 jobs.json 里就有重复） |
| [P2] 取消与 worker 收尾的竞态：`cancel_job()` 置 `cancel_requested` 后 worker 仍可能写回 `running` | `tiklocal/services/downloader.py:900-960` | 需要状态机锁与「终态不可回退」断言，且测试要能稳定复现该竞态 |
| [P2] 删除任务时 `_persist_locked()` 抛 `OSError`：内存已删、磁盘未删，接口 500 而重启后任务复活 | `tiklocal/services/downloader.py:660-725` | 三条删除路径都要快照回滚（`retry_job()` 已有先例），改动面大于当前收益 |
| [P2] `preference_dimensions` 无界增长：每次偏好计算按 user×media 插行，孤儿行只增 | `tiklocal/services/database.py:631-684` | 需要按维度加 TTL 或上限，并决定「用户偏好」保留多久，属产品决策 |
| [P2] `_persist_locked()` 每次全量写 `jobs.json`（含 `download_payload` 与 indent），任务多时每 2 s 一次整文件重写 | `tiklocal/services/downloader.py:433-470` | 改增量写要重做文件格式与恢复逻辑，风险高于当前 IO 成本 |
| [P2] 移除媒体源时 `source.path` 字符串比较未 normcase：大小写或尾斜杠不同就匹配不到，静默无效 | `tiklocal/web/settings.py:60-80` | 需要统一路径比较工具（`os.path.normcase(os.path.abspath())`），牵动所有按路径查源的调用点 |
| [P2] 回收站目录名 `.tiklocal-trash` 与用户自己的同名目录冲突时，该目录被当作回收站参与过期清理 | `tiklocal/services/trash.py:11` | 需要「隐藏目录 + 可配置名」的产品决策，改名还要迁移已有回收站 |
| [P3] 资源超时与解码失败共用同一个 1 h `.fail` 标记：内存紧张时的超时把图钉死一小时 | `tiklocal/services/thumbnail.py:640-648` | 要按失败类型分档存储，需改缓存文件格式 |
| [P3] `clear_cache()` 的 `*.jpg` glob 会匹配到正在生成的 `.thumb-*` 临时文件，清缓存把在飞的缩略图变成 1 h 占位符 | `tiklocal/services/thumbnail.py:520-535` | 需要把临时文件挪进独立子目录，属缓存目录布局调整 |
| [P3] legacy 缩略图收养绕过 `THUMB_VERSION`：旧尺寸图被当作新缓存，直到源文件变动 | `tiklocal/services/thumbnail.py:300-330` | 需要一次性迁移（重命名或删除旧缓存），会让升级后首次打开变慢 |
| [P3] `DecompressionBombError` 的图永远拿不到尺寸，每次扫描都重试一次解码 | `tiklocal/services/media_info.py:120-140` | 需要「失败也缓存」的负面缓存，要定 TTL 与失效条件 |
| [P3] `photo_groups` 的 total 统计包含已删除行，末页可能为空 | `tiklocal/services/library_index.py:520-560` | 需要 total 与 rows 走同一份过滤条件，牵动分页契约 |
| [P3] 字幕的 `prompt_version`/`prompt_hash` 只写不读：提示词改了仍命中旧缓存 | `tiklocal/services/captions.py:430-470` | 需要把版本纳入缓存键并决定旧缓存是否作废（会触发一次全量重生成） |
| [P3] 详情页删除后浏览器「后退」回到 404：没有 `history.replaceState`，也没有 410 页面 | `tiklocal/static/detail_page_controller.js:1-60` | 需要决定后退落点（列表页锚点 or 提示页），属交互设计 |
| [P3] 回收站列表无分页：条目多时一次返回全部并整块重建 DOM | `tiklocal/web/media.py:300-320` | 条目量小时无感；等回收站真正常驻上百条再做 |

### 第 28 轮新增（17 条）

本轮视角是并发与多进程一致性，五域上报 49 条（P2 19 / P3 30），4 条是跨域同一根因（`register_uris` 超时被吞、设置页读-改-写无锁、CLI 不取实例锁、`_persist_locked` 全量写），去重后 15 条 P2；修 12 条 P2 + 2 条 P3，余下 17 条登记在此。

| 问题 | 位置 | 为什么暂不修 |
|------|------|--------------|
| **[P2] 优先** `/api/activity` 在长写事务期间阻塞约 15 s 后 500，播放/跳过信号整批丢失，推荐样本静默缺段 | `tiklocal/web/media.py` + `tiklocal/services/database.py` 写路径 | 要给活动写入单独的短事务连接或内存队列；`busy_timeout=5000` 与索引重建的长事务叠加才触发，需先测重建期间的真实锁持有时长 |
| **[P2] 优先** 读路径的多条语句不在同一快照：`total`/`has_more` 与分页行可能来自不同时刻，边界处出现「页数变了但行没变」 | `tiklocal/web/library.py`、`tiklocal/services/library_index.py` 读方法 | 需要把一次查询的所有语句包进 `BEGIN`（读事务）或改成单条 CTE；牵动所有分页端点 |
| [P2] `write_json_atomic` 只 flush 不 `fsync`：断电后可能留下 0 字节的目标文件（临时名已唯一、重试已有） | `tiklocal/services/json_storage.py:35-77` | 每次写盘多一次 `fsync` 在机械盘上约 5-10 ms，而进度写盘是高频路径；要按文件分级（收藏/合集 fsync，进度不 fsync） |
| [P2] `thumbs.py` 绕过 `write_json_atomic` 直接 `write_text`，且把损坏当空表继续写 | `tiklocal/services/thumbs.py` | CLI 侧独立实现，改成共用工具需连带改 `thumbs` 命令的恢复语义 |
| [P2] 回收站 manifest 用非原子 `write_text`：崩溃后条目目录存在但 manifest 缺失，该条目永久不可见、也永不过期 | `tiklocal/services/trash.py` | 需要 manifest 原子写 + 扫描时按目录兜底重建，后者要处理「目录半建成」的判定 |
| [P2] `PRAGMA journal_mode = WAL` 的返回值被忽略：网络盘/只读挂载上静默退化为 delete 模式，此后并发读会被写阻塞 | `tiklocal/services/database.py:425` | 要检测返回值并降级为「单写者 + 更长 busy_timeout」，属部署契约 |
| [P2] `--dev` reloader 的父进程仍在后台线程写库，且子进程不取实例锁 | `tiklocal/run.py` | 开发模式，影响面限于本机调试；要修得让 reloader 子进程继承实例锁句柄 |
| [P2] `_PROBE_CACHE` 无锁，且探测失败也按「可用」缓存 60 s：并发首请求可能重复起 `--version`，失败后一分钟内仍被当作可用 | `tiklocal/services/download_tools.py:204-243` | 要加锁 + 区分失败缓存；探测本身是 Popen，加锁会让首个请求多等一个进程启动 |
| [P3] 启动清理按目录全删，不区分实例：两个实例共用媒体根时互删在飞临时目录 | `tiklocal/services/downloader.py:421-440` | 需要临时目录带实例标识（pid/实例 id），与已修实例锁配套 |
| [P3] worker 收尾 `stdout.close()` 与 pump 线程竞争，pump 从不 join | `tiklocal/services/downloader.py:1091-1095` | 需要 pump 线程句柄 + 有界 join；当前最坏只是日志尾部丢几行 |
| [P3] `_BIG_IMAGE_SLOT.acquire` 在 per-URI 锁内：leader 持 URI 锁等大图槽位，同图其它请求排在其后 | `tiklocal/services/thumbnail.py:641` | 改成先拿大图槽位再拿 URI 锁会反转锁序，需重新论证死锁面 |
| [P3] `clear_cache` 不与生成中的缩略图协调：清理后仍有缩略图存活 | `tiklocal/services/thumbnail.py:376-401` | 需要与 inflight 表联动，属缓存目录布局调整（与第 27 轮 `.thumb-*` 临时文件同批） |
| [P3] `_claim_legacy_adoption` 无锁双写：并发请求各写一次 `adopted.json` | `tiklocal/services/thumbnail.py:227-247` | 需要跨进程锁；单次写很小，冲突窗口窄 |
| [P3] `RecommendService._candidate_cache` 无锁且删除后 TTL 内仍推荐已删 URI | `tiklocal/services/recommendation.py:62,79-87` | 需要缓存失效钩子（删除时清）或更短 TTL；属产品取舍 |
| [P3] `radio.metadata_for_many` 每请求起 8 线程跑 ffprobe，8 并发最多 64 个进程 | `tiklocal/services/radio.py:250-259` | 需要全局进程池；电台页单用户场景下不触发 |
| [P3] `trash.restore` 的 `exists→move` TOCTOU：输者抛 `FileNotFoundError` 变 500 | `tiklocal/services/trash.py:81-84` | 要改成 `os.replace` + 目标已存在判定；并发恢复同一条目属罕见操作 |
| [P3] `apply_sources()` 就地多步改写共享 `LibraryService`，读侧无快照 | `tiklocal/services/media_sources_store.py:297-310` | 需要「构建新实例再原子替换」，牵动 `create_app` 的装配顺序 |

本轮修掉的 2 条 P3 未进表：队头阻塞（`_dispatch_locked` 遇重试 job 时 `break` 挡住后续可派发任务，改为旋转到队尾）与 `_terminate_process()` 末尾无超时 `wait()`（改为 `wait(timeout=5)` + 捕 `OSError`/`TimeoutExpired`），详见 `round-28.md` 第四节。

### 第 29 轮新增（29 条）

本轮视角是可观测性与故障诊断，五域 + 自查上报 51 条（P1 4 / P2 21 / P3 19），3 条是跨域同一根因（索引同步结果无人读、缩略图失败无日志、500 无关联 ID），去重后 43 条；修 14 条（P1 4 + P2 9 + 顺带 1 条 P3），余下 29 条登记在此。

| 问题 | 位置 | 为什么暂不修 |
|------|------|--------------|
| **[P2] 优先** 回收站/收藏/AI 配置的写入类 `OSError` 一律静默（`except OSError: pass`），与下载页 `downloads.py:401` 记日志的做法不一致 | `tiklocal/web/media.py`、`tiklocal/services/favorites.py`、`tiklocal/web/settings.py` | 同一产品内需要统一的「写失败」上报层；逐点补日志会散在十几处，先定一个 helper 再批量替换 |
| **[P2] 优先** 生产模式无请求级日志：慢请求只覆盖 >2 s，正常请求与 4xx 无痕，排查「用户说打不开」只能靠猜 | `tiklocal/app.py` | 全量 access log 会被缩略图请求淹没（单页几十条），需要先做采样与容量上限，属日志策略而非单点修复 |
| [P2] 英文原始异常直出中文界面（`signal timed out`、`Failed to fetch`、`HTTP 500`） | `tiklocal/static/*.js` 多处 `error.message` | 需要一个统一 `describeError(err)` 把超时/离线/HTTP 状态翻成用户语言，牵动十余处 catch |
| [P2] 离线事件几乎没接线：`tiklocal:offline` 全库零监听，恢复网络后首页仍停在空库态 | `tiklocal/static/base.js`、`home_page_controller.js` | 与「首页错误条」同批做更划算，本轮先保证失败可见，恢复后的自动重取留给下一轮 |
| [P2] flow 丢弃服务端错误体：`throw new Error('HTTP ' + status)` 让「媒体文件已损坏」这类具体原因永远看不到 | `tiklocal/static/flow_page_controller.js` | 需要先统一 flow 的错误对象形状（`{code, message, retryable}`），牵动错误气泡与重试逻辑 |
| [P2] flow 字幕失败只闪红灯 1.4 s，`flow.error.title_load`/`title_generate` 是死文案 | `tiklocal/static/flow_page_controller.js` | 需要设计字幕区的持久错误态（带重试按钮），属交互改版 |
| [P2] 删除/恢复请求无超时，服务端卡住时 Promise 永不 settle，撤销条一直转圈 | `tiklocal/static/ui_feedback.js` | 要区分「删除中」与「删除失败」两个态，与 toast 互斥逻辑一起改 |
| [P2] 撤销窗口仅 10 s 且只在落地页首载给一次，切页回来就没有 | `tiklocal/static/ui_feedback.js` | 需要持久化最近删除（sessionStorage）并跨页重建撤销条，属功能增强 |
| [P2] 库页不告知「来源离线」：幽灵行照常渲染、可点、点开才 404 | `tiklocal/static/library_page_controller.js` | 需要索引层暴露来源可用性（`unavailable_sources`）并在卡片上打标，牵动渲染契约 |
| [P2] `skipped_prefixes` 不落盘、不展示，全仓无消费者：忽略目录的配置错误永远不报 | `tiklocal/services/library_index.py` | 要先定「忽略规则」的用户可见位置（设置页列表），否则只是换个地方存 |
| [P2] 数据库损坏被隔离重建后应用内无告知（日志有、界面无），用户看到库突然变小 | `tiklocal/services/database.py`、`tiklocal/app.py` | 需要启动期把「重建过」写进 `app.extensions` 并由首页展示，与错误条同批 |
| [P2] `/api/search` 无法区分「索引为空」与「没有匹配」 | `tiklocal/web/search.py` | 需要索引层提供 `total_indexed`，且前端空态文案要分三态，属文案 + 契约改动 |
| [P3] 内部英文异常绕过 `t()` 直接进用户文案（如 `f"ffprobe failed: {err}"`） | `tiklocal/web/media.py` 等多处 | 需要错误码化；当前多数是兜底分支，用户很少见到 |
| [P3] 静默降级/丢条目：个别文件解析失败时整批跳过而不计数 | `tiklocal/services/library_index.py`、`metadata.py` | 与「跳过计数」同类，等索引层统一加 `skipped` 统计后一起补 |
| [P3] 日志格式无线程标识，容量会被客户端错误淹没 | `tiklocal/logging_setup.py` | 加 `%(threadName)s` 会改变既有日志格式；客户端错误已由 client-log 限流 |
| [P3] 日志写入本机绝对路径（含用户名），贴日志到公开 issue 会泄露路径 | `tiklocal/logging_setup.py`、各处 `LOGGER.info(str(path))` | 需要统一的路径脱敏 helper，牵动日志点较多 |
| [P3] `requestJson/requestForm` 裸 `response.json()`：非法 JSON 抛 `SyntaxError` 而非可读文案 | `tiklocal/static/base.js` | 与 `describeError` 同批；当前多数端点不会返回非 JSON |
| [P3] 并发保存 `catch(_)` 吞原因，两个标签同时改设置时后者只看到「保存失败」 | `tiklocal/static/settings_page_controller.js` | 设置页错误呈现整体要重做（多来源编辑），属下一轮候选 |
| [P3] client-log 每页只报前 5 条且无截断标记 | `tiklocal/static/base.js` | 有意限流避免刷屏；要加「还有 N 条」需服务端支持计数 |
| [P3] 搜索失败不区分原因，重试按钮文案复用 `search.submit` | `tiklocal/static/search_page_controller.js` | 与 `/api/search` 三态改造同批 |
| [P3] toast 互斥覆盖：连续失败只留最后一条 | `tiklocal/static/ui_feedback.js` | 需要队列或堆叠，属 UI 组件改造 |
| [P3] 扫描状态三元组不完整：扫描中不可见，`last_synced_at` 在零成功时也刷新 | `tiklocal/services/library_index.py`、`web/library.py` | 需要区分「扫描完成」与「扫描完成但全失败」，与 J3-P2-2 同批 |
| [P3] 缩略图失败只留 `.jpg.fail` 标记，无计数、无单条查询端点 | `tiklocal/services/thumbnail.py` | 本轮已加失败日志；计数与端点属诊断功能扩展 |
| [P3] 回收站静默丢弃读不出的条目，恢复时反报「不在回收站」 | `tiklocal/services/trash.py:128-147` | 需要 `entries()` 返回 `unreadable` 计数并在界面提示 |
| [P3] 未捕获异常永远不进 `tiklocal.log`（无 `sys.excepthook`/`threading.excepthook`） | `tiklocal/logging_setup.py` | 与 J5-b 前端上报互补；服务端崩溃多在 Flask 内，已由 500 handler 覆盖 |
| [P3] `InstanceLockError` 文案指向不存在的 `--instance` 参数 | `tiklocal/services/instance_lock.py` | 文案修正即可，但需与 `run.py` 的 `--instance-dir` 对齐 |
| [P3] 启动日志不记录实际生效的配置文件路径 | `tiklocal/run.py:750-761` | 需要把 `load_config()` 的结果带回启动日志，属小改 |
| [P3] `thumbs`/`dedupe` 丢弃统计、失败也返回 0 | `tiklocal/run.py`、`tiklocal/dedupe.py` | CLI 输出契约变更，需要同步改文档与脚本 |
| [P3] `instance/config.py` 的 `MEDIA_ROOT` 永远被覆盖，且语法错误静默 | `tiklocal/instance/config.py` | 属历史遗留加载顺序问题，需要先确认是否还有人用 |
| [P3] 配置类型校验报错不含「实际读取了哪个文件」 | `tiklocal/config.py` | 与「启动日志记录配置路径」同批 |

### 第 30 轮新增（45 条）

| 级别 | 问题 | 位置 | 为什么暂不修 |
| --- | --- | --- | --- |
| [P0] `records()` 无 LIMIT 的全表 `SELECT *` + fetchall（tag feed、深链 caption feed、作者页、电台都直接用） | `tiklocal/services/library_index.py:285-298` | 需 keyset 分页（`after_uri`）；简单加 LIMIT 会让 feed 少条目 |
| [P0] feed 每页按全库规模建两份 dict，且一次请求读三次 `favorites.json` | `tiklocal/services/recommendation.py:64-71`、`:101`、`:239` | 候选池改 tuple + favorites 提到请求级解析，需动选择算法 |
| [P1] 每页都 `COUNT(*)`，与取行是两条独立语句 | `tiklocal/services/library_index.py:543` | 要改 `total` 语义（offset>0 时未知），前端需同步 |
| [P1] 搜索条件 `(album_key IS NULL OR album_index = 0)` + `LIKE '%term%'` 双重不可索引 | `tiklocal/services/library_index.py:796-809` | 需 FTS5 或前缀索引方案 |
| [P1] `/library` 每次一次全表 + 一次全索引 + 8 次整月排序 | `tiklocal/services/library_index.py:574-623` | 需拆 `capture_year` 索引与排序键 |
| [P1] 索引重建单事务 2N 条语句 + 每 source×4 表孤儿全扫，10 万文件 20–60s | `tiklocal/services/library_index.py:42-145` | 分块提交 + 孤儿清扫移出事务 |
| [P1] `media_vectors`/`video_frames` 在请求路径无界读 BLOB | `tiklocal/experiments/similarity/embedding.py:126-152` | 需 fetchmany 流式 top-K |
| [P1] `download_payload` 无尺寸上限，且全量 `json.dumps` 发生在全局锁内 | `tiklocal/services/downloader.py:603`、`:1528-1545` | 入队白名单裁剪 + 锁内取快照、锁外序列化 |
| [P1] `/thumb` 在请求线程排队生成，生成槽位无超时 → 冷缓存首屏可占满 waitress 线程 | `tiklocal/services/thumbnail.py:549` | 要改占位图契约（`acquire(timeout=2)` + `Retry-After`） |
| [P1] 启动期同步全量扫描，零变化也重写全部行 | `tiklocal/app.py:466`、`tiklocal/services/library_index.py:44-91` | 后台扫描 + `DO UPDATE ... WHERE` 条件更新 |
| [P1] 窗口宽度变化 1px 即清空 grid 重建全部卡片 | `tiklocal/static/library_page_controller.js:399-411` | 复用已有节点重排（保留监听器与解码结果） |
| [P1] `?focus=` 深链逐页串行拉全库（最多 200 轮 × 24 条） | `tiklocal/static/library_page_controller.js:501-521` | 生成链接时带 offset，或接口支持 `focus=` |
| [P2] `image_random` 全量 fetchall + Python shuffle | `tiklocal/services/library_index.py:537-541` | 需 SQL 侧抽样 |
| [P2] `OFFSET` 无上限，且 temp 表没有 `position` 索引 | `tiklocal/services/library_index.py:520-536`、`tiklocal/web/library.py:55` | 加 `maximum` 与索引 |
| [P2] `unprobed()` 每 5s 全索引扫（`probed_mtime` 无索引） | `tiklocal/services/library_index.py:249-275` | 加索引或维护待探测队列 |
| [P2] 每批 activity 都做 2 万行 `NOT IN` 剪枝 | `tiklocal/services/database.py:570-578` | 改按时间窗口删除 |
| [P2] 登记 1 个文件也要全表 `time_states()` | `tiklocal/services/library_index.py:1065`、`:716-726` | 加 `time_states_for(uris)` |
| [P2] `on_days()` 用 `substr` 包列 + 窗口函数全表扫 | `tiklocal/services/library_index.py:429-476` | 需预存 day 列 |
| [P2] `blog_rows()` 每 source 一次封面查询（N+1），且 timeline 视图也无条件调用 | `tiklocal/services/library_index.py:682-714`、`tiklocal/services/library.py:287` | 一次查询取全部封面 + 按需调用 |
| [P2] `/api/collections/by-media` 每张卡片整份重读 `collections.json` | `tiklocal/web/library.py:128`、`:169-171` | `list_for_media` 改 `include_items=True` |
| [P2] `RadioProfileStore.record()` 每次播放/跳过都全量排序 + 整份写盘 | `tiklocal/services/radio.py:80-121` | 增量写或内存有序结构 |
| [P2] `/thumb` 命中缓存仍要 2 次 `resolve()` + 3 次 stat | `tiklocal/web/media.py:684`、`tiklocal/services/thumbnail.py:257` | `get_thumbnail` 接受已解析路径 |
| [P2] `ffmpeg_argv()` 无缓存，每张视频海报还多一次 ffprobe | `tiklocal/services/thumbnail.py:575`、`:596`、`tiklocal/services/download_tools.py:69-98` | 加 `lru_cache` + HDR 结果 sidecar |
| [P2] 设置页 `cache_stats()` 全量 glob + stat 缩略图目录 | `tiklocal/services/thumbnail.py:394-404` | 维护计数或 60s 缓存 |
| [P2] `clear_cache` 在请求线程同步删数十万文件，无进度 | `tiklocal/services/thumbnail.py:406-426` | 后台任务 + 进度轮询 |
| [P2] captions `_to_data_url` 先整图解码再缩放（无 draft、不占像素配额） | `tiklocal/services/captions.py:427-443` | 复用 thumbnail 的 draft 与 200MP 守卫 |
| [P2] flow 相册子项强制 `eager` 且建 DOM 即拉全尺寸原图 | `tiklocal/static/flow_page_controller.js:1339-1351` | 改 lazy + 首子项用缩略图 |
| [P2] library 网格无窗口化，且每追加一页跑一次全文档 `feather.replace()` | `tiklocal/static/library_page_controller.js:478` | 删掉该调用 + 事件委托（窗口化另计） |
| [P2] `refreshTileSelection()` 每次全表扫描 | `tiklocal/static/library_page_controller.js:1049` | 只对变化集合增量切换 |
| [P3] 扫描期内存里同时存在 3 份记录副本 | `tiklocal/services/library_index.py:1027-1041` | 按批直接 UPSERT |
| [P3] `metadata_json` 列只写不读（2 万行≈40MB 死重量） | `tiklocal/services/database.py:114`、`:553-564` | 确认无读取方后删列 |
| [P3] 每请求 4 连接 × 4 PRAGMA，`journal_mode=WAL` 每次连接重设 | `tiklocal/services/database.py:420-427` | 连接池或 PRAGMA 只设一次 |
| [P3] `records_for_names` 的 `IN(...)` 不分块 | `tiklocal/services/library_index.py:300-316` | >999 变量在旧 SQLite 直接报错 |
| [P3] `write_json_atomic` 全局 `indent=2`，体积与写入 CPU 翻倍 | `tiklocal/services/json_storage.py:52` | 加 `compact` 参数给大文件 |
| [P3] `read_json_file` 共享冲突时同步 sleep，最多 250ms 阻塞请求线程 | `tiklocal/services/json_storage.py:142-164` | 改指数退避或直接抛 `StoreUnreadableError` |
| [P3] `read_text()` 整份进 str 再 `json.loads`，峰值≈3× 文件大小 | `tiklocal/services/json_storage.py:135`、`:166` | 改 `json.load(fh)` |
| [P3] 单个 collection 的 items 总数无上限 | `tiklocal/services/collections.py` | 加 `MAX_COLLECTION_ITEMS_TOTAL` |
| [P3] `trash.expired()` 为拿 id 再跑一遍完整 `entries()` | `tiklocal/services/trash.py:155-158` | 复用同一次扫描 |
| [P3] `_select_weighted` 每 pick 重建整个 pool，`_spread_by_parent` 线性扫 | `tiklocal/services/radio.py:338-403` | pool 改 dict 原地 pop、`Counter` |
| [P3] probe worker 的 `skip[:400]` 截断导致失败项被重复探测 | `tiklocal/services/library_index.py:1278-1279` | 跳过集合改全量 `set` |
| [P3] `probe_media_dims` 解析后重复 stat；无 ffprobe 时在请求线程 spawn ffmpeg | `tiklocal/services/media_info.py:21-22`、`:117` | 删冗余 `exists()` + 缓存 banner 时长 |
| [P3] `/thumb` 无尺寸变体，`no-cache` 强制每次条件请求 | `tiklocal/web/media_payloads.py:10`、`tiklocal/web/media.py:703` | 跨前后端的 `w` 参数方案 |
| [P3] 集合页任一改动都整表 `innerHTML` 重建 | `tiklocal/static/collections_page_controller.js:149`、`:192` | 按 id 增量 patch |
| [P3] flow 每次切条做一次全文档 `querySelectorAll` | `tiklocal/static/flow_page_controller.js:1884` | 只清理 `prevItem.el` |
| [P3] `_to_library_record` 每行造 17 键 dict，无惰性、无缓存 | `tiklocal/services/library_index.py:750-768` | 大库转换成本的主要来源之一 |

### 第 31 轮新增（14 条）

| 级别 | 问题 | 位置 | 为什么暂不修 |
| --- | --- | --- | --- |
| [P2] memory 卡只在奇数页、且只从本页记录里挑「一年前的图片」，深页命中率趋近 0 | `tiklocal/web/flow.py:614` | 要按 (month, day) 从全量索引查候选，涉及 `on_days` 的预存列方案 |
| [P2] 深链/blog 起始流每页对整条时间线做无上限 `collapse_media_entries`，10 万图集库单页 2.6s / 52MB | `tiklocal/web/flow.py:391`、`:395`、`tiklocal/services/library_index.py:860-960` | 需把折叠下推到 SQL，属结构性改动 |
| [P2] 时间线 year_rows 两次 `GROUP BY` + 临时 B 树，10 万行 159ms，每次 `/library` 都跑 | `tiklocal/services/library_index.py:574-623` | 加 `(capture_year, media_type, mtime)` 覆盖索引可到 ~60ms，但每个新索引让快照写锁再涨 5–8% |
| [P2] `timeline_months()` 全表扫 + 临时 B 树 66ms | `tiklocal/services/library_index.py:600-623` | 同上的索引数量与写放大权衡（加覆盖索引实测 17.7ms） |
| [P2] `media_vectors` 缺 `(model, mtime)` 索引，`vectors()` 把 90MB BLOB 塞进临时 B 树：902ms | `tiklocal/experiments/similarity/embedding.py:126-152` | 加索引后 302ms，但每模型每文件一行，写放大明显，需先确认向量检索的实际调用频率 |
| [P2] `replace_snapshot` 单事务 10 万条持写锁 3.6–4.8s，撞上 activity 写入的 3×5s 重试 → 请求挂 ~15s 后 500 | `tiklocal/services/library_index.py:42-145` | 需分批提交 + 版本号，属结构性改动 |
| [P3] 收藏页每页绕过 `favorites` 的 mtime 缓存（调 `load()` 而非 `_load_cached()`） | `tiklocal/web/library.py:205` | 与「`load()` 每次全量读 JSON」同批处理 |
| [P3] 位置切片式翻页对库变化敏感：滚动中新增文件会让后续页重复 1 条并被静默丢弃 | `tiklocal/services/library_index.py:520-536`、`tiklocal/static/flow_page_controller.js` | 需 keyset（`after_uri`）分页契约，前端要同步改 |
| [P3] 多词搜索是「所有词都命中」而不是 OR，用户预期更接近 OR | `tiklocal/web/search.py`、`tiklocal/services/library_index.py:796-809` | 与 FTS5 方案同批，否则 OR 也走不了索引 |
| [P3] `photo_groups` 的 total 含已删除的父行 → 末页可能为空 | `tiklocal/services/library_index.py` | 需在 COUNT 里 join 存活父行 |
| [P3] `records()` / `time_states()` 是无 WHERE 无 LIMIT 的全表读，10 万行 862ms / ≈150MB | `tiklocal/services/library_index.py:285-298`、`:716-726` | 需按调用方逐个改 keyset / 只取需要的列 |
| [P3] `_migrate_015` 是唯一不检查列是否存在的 `ADD COLUMN`，失败后每次启动都失败 | `tiklocal/services/database.py` | 需与迁移框架的「幂等」约定一起改 |
| [P3] `_sweep_orphan_state` 用前缀 `LIKE` 清孤儿，四张表全扫 | `tiklocal/services/database.py` | 应改范围查询（`uri >= prefix AND uri < prefix+'\uffff'`），与索引方案同批 |
| [P3] `idx_media_affinity_recent` 没有查询使用，纯写放大 | `tiklocal/services/database.py:120` | 确认无读取方后删除 |

### 第 32 轮新增（20 条）

| 条目 | 位置 | 为什么暂不修 |
|------|------|--------------|
| [P2] 未知拍摄时间的行内自相矛盾：`captured_local_date='1970-01-01'` 但 `captured_at>0` | `tiklocal/services/library_index.py` `_capture_payload` | 需要给"未知时间"单独一个哨兵值，会牵动索引列与前端展示 |
| [P2] 视频分支完全不读 ffprobe `creation_time`，相机视频只能落 mtime | `tiklocal/services/media_info.py:45-68` | 需要给视频探测加字段并回填既有行，属数据迁移 |
| [P2] 月视图按 `captured_at`、探索网格按 `mtime_ts`，同一张图两个入口可能落在不同月 | `tiklocal/web/library.py`、`tiklocal/static/library_page_controller.js` | 要先决定"月份按拍摄还是按修改"，属产品决策 |
| [P3] 不读 `OffsetTimeOriginal`(36881)，跨时区拍摄的墙钟偏移丢失 | `tiklocal/services/library_index.py` `_image_capture_time` | 只有跨时区拍摄才有差异，样本少 |
| [P3] 只有日期没有时刻时默认 12:00，与"当天 00:00"语义不一致 | 同上 | 与哨兵值/时间列方案同批改 |
| [P3] 文件名时间分隔符不含点，`2024-01-01.12.00.00` 解析不到 | `tiklocal/services/tiktok_downloader.py` | 现有文件名由本项目生成，历史兼容收益低 |
| [P3] 只屏蔽 `'1970-01-01'` 字符串，UTC 以西会漏出 `1969-12-31` | `tiklocal/web/media_payloads.py:25` | 只有负偏移时区触发 |
| [P3] `captured_at or mtime` 把 0.0 换成负 mtime | `tiklocal/services/library_index.py:763` | 需与哨兵值方案一起改 |
| [P3] EXIF 墙钟直接当本地时间转 epoch，跨时区导入整体偏移 | `_capture_payload` | 需要先读 `OffsetTimeOriginal` |
| [P3] 推荐流时间窗用 365 天近似，闰年/DST 边界偏移一天 | `tiklocal/services/recommendation.py` | 边界误差一天，用户不可感 |
| [P3] 回收站保留期按字符串比较 `deleted_at`，混入非 UTC 格式会误判 | `tiklocal/services/trash.py:96-100` | 现在 `deleted_at` 一律由本项目写 `+00:00` |
| [P3] 缩略图指纹用 `datetime.fromtimestamp` 本地时间，换时区后全部失效重算 | `tiklocal/services/thumbnail.py` | 只发生一次，重算成本可接受 |
| [P3] `_derive_device_name` 的版本号只写不读 | `tiklocal/services/device_auth.py` | 字段无读取方 |
| [P3] `TIKLOCAL_TIMEZONE` 未设置时依赖宿主 TZ，容器里是 UTC | `tiklocal/run.py` | 需要引入 `zoneinfo` 与容器配置 |
| [P3] `time_states()` 不返回时区列，无法诊断"这行是什么时区算的" | `tiklocal/services/library_index.py` | 需加列并回填 |
| [P3] `_capture_payload` 对 1970 之前的文件写 `captured_at=0.0` | 同上 | 与哨兵值方案同批 |
| [P3] `on-this-day` week 回退跨年时 `years_ago` 可能为 0（文案已兜） | `tiklocal/web/library.py` | 语义上仍不精确，需按真实年差计算 |
| [P3] 复数文案只有 en 的 `_one` 变体，其他语言需 ICU | `tiklocal/i18n.py`、`tiklocal/i18n_pages_media.py` | 当前只有 zh/en 两套 |
| [P3] 首页 kicker 与磁贴的"多少年前"逻辑重复 | `tiklocal/static/home_page_controller.js` | 需抽公共函数 |
| [P3] `format_timestamp_slug` 无时区参数，依赖进程 TZ | `tiklocal/services/tiktok_downloader.py` | 与本轮 `TIKLOCAL_TIMEZONE` 同批 |

## 第 33 轮新增（28 条）

| 问题 | 位置 | 为什么暂不修 |
|------|------|--------------|
| [P2] 删除只清索引不清收藏/合集成员/元数据，`purge` 的 `reused` 分支永久跳过清理 → 同 URI 新文件继承旧归属 | `tiklocal/web/media.py:292-296`、`:356-363` | 需要「删除时把派生状态快照进 `entry.json`、恢复时写回」的数据格式变更，单独立项 |
| [P2] 取消后重试：`partial` 的产物清单不复用，从头再下 → `(1)` 副本 | `tiklocal/services/downloader.py:759-818`、`tiklocal/static/download_page_controller.js:302-309` | 需要让 `retry_job` 把 `output_files_rel` 当作已完成项跳过，牵涉 direct 引擎的文件选择 |
| [P2] 崩溃窗口：搬完第 1 个文件、未写 `output_files_rel` 时进程被杀 → 孤儿文件 + 重试副本 | `tiklocal/services/downloader.py:1332` → `:1067-1079` | 需要每搬一个文件就增量持久化，写入次数 ×N |
| [P2] `output_token` 只存不用，API 没有幂等键 | `tiklocal/services/downloader.py:550`、`:602`、`:794` | 入口去重已覆盖重复提交的主路径，幂等键属于对外契约变更 |
| [P2] 同一 URL 的两个并发 job 会写同一最终路径与 `.info.json` | `tiklocal/services/downloader.py:1219-1231` | 入口去重后只剩并发窗口内的极小概率 |
| [P2] `restore` 目标已存在时 409，不是幂等成功 | `tiklocal/services/trash.py:restore` | 不删任何一边、行为诚实；幂等需要「同名同大小视为已恢复」的判定 |
| [P2] 回收站恢复后 mtime 变化，缩略图与索引指纹都失效重算 | `tiklocal/services/trash.py` | 恢复是低频操作，重算成本可接受 |
| [P2] `purge_expired` 与手动 purge 的锁粒度不足，可能重复删同一目录 | `tiklocal/services/trash.py` | 删除是幂等的（目录消失即成功），实际影响只有日志噪音 |
| [P2] `register_uris` 与 `replace_snapshot` 重叠时后写覆盖先写 | `tiklocal/services/library_index.py` | 需要扫描代际号，改动面大 |
| [P2] `media_affinity` 的 upsert 在并发下可能计数丢失 | `tiklocal/services/database.py` | 计数只用于推荐排序，误差可接受 |
| [P2] 删除与重扫竞态：删除后扫描把文件按旧快照写回索引 | `tiklocal/services/library_index.py` | 下次扫描自愈 |
| [P2] 设置页保存/上传 cookie/作者同步有 `disabled` 但无请求代际号 | `tiklocal/static/settings_page_controller.js`、`author_page_controller.js` | 单页面内已防重，跨标签页窗口极窄 |
| [P2] 乐观更新（收藏/点赞）失败回滚窗口内再点，本地与服务端相反 | `tiklocal/static/flow_page_controller.js`、`detail_page_controller.js` | 回滚已实现，极端连点才会撕裂 |
| [P2] `fetch` 无重试语义，网络抖动直接失败 | `tiklocal/static/base.js` | 非幂等 POST 加自动重试风险更大 |
| [P3] 合集创建的服务端同名唯一性策略未定 | `tiklocal/services/collections.py:create` | 本轮只做前端防重；同名是否允许是产品决策 |
| [P3] 元数据 set/delete 两次写之间的并发窗口 | `tiklocal/services/metadata.py` | 已有 `RLock`，跨进程才触发 |
| [P3] 迁移重复执行只靠 `CREATE INDEX IF NOT EXISTS` 兜底 | `tiklocal/services/database.py` | 现有迁移都幂等 |
| [P3] 向量 upsert 的 UNIQUE 约束在 `(model, uri)` 上，换模型会留下旧行 | `tiklocal/experiments/similarity/embedding.py` | 换模型时手动重建即可 |
| [P3] 首次启动建 admin 重复执行会建两个用户 | `tiklocal/services/auth.py` | 单进程启动，实例锁已覆盖 |
| [P3] `write_json_atomic` 临时名靠 `uuid4` 防撞，无重试 | `tiklocal/services/json_storage.py` | 碰撞概率可忽略 |
| [P3] 实例锁不覆盖 CLI 子命令，`dedupe --delete` 可在服务运行时删文件 | `tiklocal/run.py` | 用户显式操作，且只影响索引新鲜度 |
| [P3] 缩略图缓存目录用本地时间命名，换时区后不命中 | `tiklocal/services/thumbnail.py` | 只发生一次 |
| [P3] 回收站 `entries()` 按字符串比较 `deleted_at` | `tiklocal/services/trash.py` | 本项目写入格式固定 |
| [P3] 后台探测失败按固定间隔重试，无退避上限 | `tiklocal/services/library_index.py` | 已有上限与日志 |
| [P3] `_persist_locked` 整表重写 jobs.json | `tiklocal/services/downloader.py:387` | 写入量小且原子替换，增量写风险更大 |
| [P3] 批量入队的 `skipped` 只回传 job_id，不含 URL | `tiklocal/web/downloads.py:246-269` | 前端按 url 匹配已够用 |
| [P3] 合集选择器 `create()` 在 `!loaded` 时 `await load()`，但失败后仍会写回 | `tiklocal/static/collection_picker.js` | 失败路径已有 toast |
| [P3] 下载页重试按钮的可用性判断分散在三处 | `tiklocal/static/download_page_controller.js` | 本轮新增 `canRetry` 后仍需收敛 |

