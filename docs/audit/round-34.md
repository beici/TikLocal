# 第 34 轮审查报告：契约与兼容性

日期：2026-10-29
起点：`7159211`（第 33 轮收尾，`audit-round-33-end`）
视角：接口/模板/存储/配置对外承诺的形状有没有变——旧客户端、旧数据、旧配置还能不能用。

## 一、方法与范围

五个域并行只读审查（四个子代理 + 自查），每域先读代码再跑探针，结论必须落到
`文件:行` 与「谁会踩到」。

| 域 | 范围 | P0 | P1 | P2 | P3 |
| --- | --- | --- | --- | --- | --- |
| O1 | HTTP 接口契约（信封、状态码、分页、绝对路径） | 0 | 1 | 4 | 4 |
| O2 | 模板/JS 与 i18n 目录（启动数据、data-*、键存在性） | 0 | 0 | 1 | 4 |
| O3 | 持久化格式与版本（jobs.json、collections、缩略图、SQLite） | 0 | 0 | 2 | 3 |
| O4 | 配置与命令行入口（config.yaml、CLI、环境变量） | 0 | 1 | 2 | 6 |
| O5 | 自查（路由表、信封、错误形状、i18n 完整性） | 0 | 0 | 0 | 0 |

去重后 **27 条**（O1 的 `thumbs`/`dedupe` 与 O4-3 是同一个 bug）：P0 0 / P1 2 / P2 8 / P3 17。
本轮修 **9 条**（P1 2、P2 5、P3 2），转 backlog **18 条**。

## 二、P1

### O4-1 设置页保存过文件夹后，启动目录被静默丢弃

`tiklocal/services/media_sources_store.py:264-280` + `tiklocal/run.py:576-577`。

`resolve_startup_sources()` 在「有 saved sources」时直接返回 saved 列表，把启动参数
（`tiklocal /new/library` 或 `MEDIA_ROOT`）整个丢掉。用户第一次在设置页点保存后，
`saved` 里出现 `default` 源；此后每次带参数启动，banner 与实际服务目录都是旧路径，
而 `--media-source` 只是追加，没有任何 CLI 能改回启动目录。

第 23 轮修过「设置保存后启动目录失效」的另一半（`normalize_media_sources` 让显式
目录覆盖 config 的 default），但 `saved` 优先于启动参数这条路径没覆盖：那次修的是
config → CLI 的方向，这次是 saved → CLI 的方向。

修法：两者都存在且路径不同时，**保留 saved 但仍警告**，并把差异写进日志与启动日志，
让「为什么服务的是旧目录」在 `tiklocal.log` 里能查到，而不是靠猜。

### O1-1 / O4-3 `thumbs` 与 `dedupe` 不认 `media_sources`

`tiklocal/run.py:421`、`:435`。

README 主推 `media_sources` 配置，`vectorize` 也支持，但 `thumbs`/`dedupe` 只读
`media_root` 参数、`MEDIA_ROOT` 和 config 的 `media_root`。同一份 config.yaml，一条
命令能跑、另一条 exit 2「Specify a media directory」，用户只能把路径再抄一遍。

修法：抽出 `_configured_media_root()`，两个子命令在显式路径与 `MEDIA_ROOT` 之后
回落到配置里的 default 源（没有 default 就用第一个源），错误提示补上 config.yaml 这条。

## 三、P2（本轮修复 5 条）

### O1-4 扫描失败被当成功播报

`tiklocal/web/settings.py:243` 与 `tiklocal/services/library_index.py:1145` 对同一个
「源不可用」用了两个名字：`sync` 端点叫 `unavailable_sources`，settings 的 `scan`
端点叫 `unavailable`，而设置页 JS 读的是前者。结果拔掉移动硬盘后点「扫描」，页面
显示「已索引 0 个媒体」，与成功同形。

修法：settings 端点同时返回 `unavailable_sources` 与 `source_errors`（逐源错误码），
前端在两者任一非空时弹 warning toast，带上源名。新增 i18n 键
`settings.library.source_scan_unavailable`。

### O1-3 绝对路径外泄（部分修复）

`tiklocal/web/settings.py` 的 `_source_payload()` 返回 `store_path`（服务器上
`media_sources.json` 的绝对路径），仓库内零消费方。删掉该字段。

同一报告里的 `tiklocal/web/media.py:658` 的 `data.path` **没有删**：它被
`flow_page_controller.js:644`（信息面板）与 `:670`（复制按钮）使用，是功能不是泄漏；
但 `data.path` 若来自用户提供的目录，仍可能暴露服务器布局——已记入 backlog 观察。

### O3-1 `jobs.json` 白名单把持久化字段读没了

`tiklocal/services/downloader.py:997-1026`。`_persist_locked()` 全量写整个 job dict，
`_load_history()` 却用固定白名单重建：`retried_by`、`locale`、`partial` 写进去、读
不回来。重启后「已重试」的原 job 又长出重试按钮（`download_page_controller.js:303`
判 `!job.retried_by`），`retry_job` 的守卫（`downloader.py:798`）也失效 → 同一 URL
再下一份 `(1)` 副本。`locale` 丢了则重试后的文案退回默认语言。

修法：白名单补三个字段；长期方向是「以持久化 dict 为基底 + 已知键类型校正」，已记
backlog。

### O4-2 README 里的向量命令不存在

`README.md:289-292` 四条命令里三条在当前 CLI 上不存在（`--order`、`--source`、
`--cleanup`、`analyze-similar`）。照抄即 exit 2。

修法：改成实际存在的 `--media-source`、`--limit`、`--dry-run`、`--yes`。

### O2-1 同一个字典字面量里重复定义的 i18n 键

`tiklocal/i18n_pages_flow.py:32-33` 与 `:83-86` 在**同一个 dict 字面量**里各定义了
一次 `flow.kind.image`/`flow.kind.video`，Python 求值时后者覆盖前者：

- flow 页图片加载失败的 HUD 拿到「图文」（作品类型标签），应为「图片」；
- `tiklocal/web/flow.py:301` 的作品类型标签拿到「图片」，应为「图文」。

`i18n.py` 的重复键守卫只比较**跨模块**的键，同字面量内的重复在守卫运行前已被
Python 折叠，永远看不见。

修法：拆键——HUD 保留 `flow.kind.*`（图片/视频），作品标签改用
`flow.post.kind_image` / `_video` / `_album` / `_live`（图文/视频/图集/实况），
同步 `flow.py:295-301` 与 `flow_page_controller.js:469-471`。

## 四、P2（未修，转 backlog）

| 编号 | 位置 | 为什么暂不修 |
| --- | --- | --- |
| O1-1 | `tiklocal/web/media.py:237,367,372,395,406,422` | 同模块 6 种成功信封 / 4 种失败信封，统一要改客户端解析器，属于跨轮重构 |
| O1-2 | `tiklocal/web/media.py:333` | `/delete` 响应类型由 `Accept` 决定，`fetch()` 的 `*/*` 落到 HTML；仓库内无触发方 |
| O1-6 | `tiklocal/web/search.py:354,411` | `counts`/`truncated` 零消费方，作者数被报成截断值；需要先定 UI 口径 |
| O3-2 | `tiklocal/services/collections.py:116` 等 | `version` 只写不校验、写回按 v1 白名单重建；需要引入 `StoreVersionError` + 503 语义 |
| O1-5 | `tiklocal/web/author.py:423,464` | 两条错误路径只有 `ok` 没有 `success`，客户端当前只读 `ok` |
| O2-2 | `radio_controller.js:1069` 等 | 四处 body/element 状态类无对应 CSS 选择器（死类） |
| O2-3 | `settings_page_controller.js:526`、`search.html:13` | 两个 data 属性只写不读 |
| O2-5 | `i18n.py:68,85-87` 等 | 16 个键全仓库无引用（改名残留） |
| O3-3 | `tiklocal/services/thumbnail.py:203-231` | legacy 认领绕过 `THUMB_VERSION`；一次性重算成本换正确性，留待专项 |
| O3-4 | `tiklocal/app.py:170-206` | 损坏库隔离会丢 `playback_positions`/`media_events`/`media_affinity` |
| O3-5 | `tiklocal/services/downloader.py:356-367` | `download_config.json` 无版本号，降级保存会丢新键 |
| O4-4 | `tiklocal/run.py:637` vs `:750` | `--dev` 改写绑定地址后 banner 仍打印原地址 |
| O4-5 | `tiklocal/config.py:4-21` | `media_sources` 类型写错时整段静默消失 |
| O4-6 | `tiklocal/config.py:37-48` | 旧 `media_root` 静默覆盖 `media_sources` 的 default |
| O4-7 | `tiklocal/run.py:68-78` | 实例目录 config 是「第一个存在即独占」，文档写成「优先读取」 |
| O4-8 | `media_sources_store.py:282-285` | 设置页删过的源 id 会吞掉同名 `--media-source`（本轮已加 warning，未改语义） |
| O4-9 | `tiklocal/run.py:539` | `TIKLOCAL_HOST=` 空串被 `or` 链吞成 `0.0.0.0` |
| O1-7/O1-8 | `library.py:405`、`library.py:55` 等 | 死端点 `/api/library/blogs`、分页参数静默改写且各端点不一致 |

## 五、P3（本轮修复 2 条）

- **O2-4** `tiklocal/i18n.py:679-690`：`js_catalog()` 只返回当前 locale 的表，而
  `translate()` 缺键会回落默认语言 → 只存在于一种语言的键在浏览器里显示原始 key。
  改为「默认语言打底 + 当前语言覆盖」。
- **自查**：AST 扫描发现 `tiklocal/i18n.py:145` 与 `:141` 重复定义
  `settings.scan.busy`（同值），删除后者。扫描同时确认其余 8 个 `i18n*.py` 无同字面量
  重复键。

其余 P3（O1-5/O1-7/O1-8、O2-2/O2-3/O2-5、O3-3/O3-4/O3-5、O4-4..O4-9）全部进 backlog。

## 六、实现要点

1. **警告而非改语义**：O4-1 只在 saved 与启动目录冲突时记 warning。真正的语义选择
   （启动参数该不该覆盖 saved）会影响所有现有部署，留待用户决策，本轮只让现象可见。
2. **回落顺序**：`_configured_media_root()` 的优先级是「显式参数 > `MEDIA_ROOT` >
   config 的 `media_root` > 配置源的 default > 第一个源」，与 `serve` 的
   `normalize_media_sources` 保持一致，避免两个子命令对同一份配置有两种解释。
3. **拆键而不是改值**：O2-1 没有把 HUD 文案改成「图文」，因为同一个键被两个语义共用
   才是根因；拆成两组键后，两边都能各自演进。
4. **白名单补字段 + 备注**：O3-1 的修复加了注释说明「`_persist_locked()` 全量写出，
   白名单少一个字段就等于删一个字段」，避免下一个人再往白名单里加键时忘记同步。
5. **客户端与服务器同一份回退**：O2-4 让 `js_catalog()` 与 `translate()` 用同一套
   回退规则，SSR 与浏览器不会出现「页面有文案、交互后变原始 key」。

## 七、验证

- 相关测试：`test_i18n`、`test_web_runtime`、`test_media_sources`、
  `test_run_media_sources`、`test_round34_contracts` → **61 passed**。
- 新增用例：`tests/test_round34_contracts.py`（重启后 `retried_by`/`locale`/`partial`
  仍在）、`tests/test_media_sources.py` +62 行（saved 与启动目录冲突时的警告路径、
  `_configured_media_root` 的回落顺序）。
- AST 重复键扫描：`duplicate literal keys: []`。
- 全量：`pytest -q` → **621 passed / 8 skipped / 0 failed**（第 33 轮基线 616，新增 5 条：
  `test_round34_contracts.py` 2 条 + `test_media_sources.py` 3 条）。

## 八、覆盖率缺口

- **没有降级演练**：O3-2/O3-5 的「新版本写、旧版本读」路径只有推演，没有真跑一次
  降级保存；这类 bug 只有在真实升级/回滚序列里才暴露。
- **没有旧客户端**：O1-1 的信封不一致靠人读，缺少「用上一版前端打新后端」的回归
  夹具。
- **CLI 只测了参数解析**：`thumbs`/`dedupe` 的端到端执行（真跑一次缩略图生成与去重
  预览）仍靠手工。
- **i18n 只查键不查语义**：`flow.kind.*` 这类「键在、词不对」的问题，键存在性测试
  永远发现不了，只有人工读页面。

## 九、下轮建议

第 35 轮视角建议 **错误恢复与部分失败**：一个源/一个文件/一个子步骤失败时，整体是
回滚、跳过还是降级；`/api/library/sync` 的 `source_errors`、下载队列的部分失败、
缩略图批量生成的半成品，都属于「一条坏数据把整批拖下水」的高发区。
