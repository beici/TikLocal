# 第 38 轮：权限与身份边界

> 审查日期：2026-10-29 ｜ 起点提交：`50e6384`（第 37 轮） ｜ 视角：**权限与身份边界**

## 一、方法与范围

前 37 轮查的是「代码对不对」「换个环境还对不对」。这一轮问的是另一个问题：**谁被允许做这件事，他凭什么被允许，拒绝的时候是不是真的拒绝了？**

TikLocal 没有账号体系，只有一道实例访问密码，所以「权限」在这里不是「用户 A 能不能看用户 B 的东西」，而是四件更朴素的事：

1. 没通过认证的人能不能碰到数据（HTTP 层）；
2. 通过了认证的人能不能碰到**不该碰的路径**（文件系统层）；
3. 程序自己写下的状态，会不会把某个人看到的东西泄漏给下一个人（数据归属层）；
4. 拒绝发生时，前端是把它当成「拒绝」，还是当成「空」。

| 域 | 范围 | 关注点 |
| --- | --- | --- |
| S1 | `tiklocal/auth.py`、`app.py` 的认证装配、`run.py` 的 `--dev`/默认口令/TLS、`login.html` | 会话、CSRF、限流键、密码与 `auth_revision` |
| S2 | `tiklocal/web/*.py` 全部 + `app.py` 的注册与错误处理器 | 页面×方法授权矩阵、写端点守卫、媒体端点归属 |
| S3 | `services/library.py`/`trash.py`/`thumbnail.py`/`media_sources_store.py`、`web/settings.py`、`run.py` 的实例目录 | URI→路径、源增删校验、缓存与私钥位置 |
| S4 | `favorites.py`/`collections.py`/`playback_resume.py`/`media_state.py`/`author_profiles.py`、`web/client_log.py`、`logging_setup.py` | 状态是全局还是按人、日志与诊断端点的泄漏 |
| S5 | `base.js`/`base.html`、`settings_page_controller.js`、各页 `fetch()` 与 401/403 处理 | 401 是否被当空态、注销清什么、bfcache |

五个子代理只读（禁改文件、禁跑测试、禁起服务），在 Windows 上逐条给出 `file:line` + 触发路径 + 影响量化 + 最小修复 + 置信度；主审负责复现、定级与落地。

**结论：35 条（P0 1 / P1 5 / P2 16 / P3 13），当轮修 13 条（P0 1 + P1 4 + P2 5 + P3 3），其余 22 条转 backlog。**

## 二、问题总览

| 域 | P0 | P1 | P2 | P3 | 合计 | 当轮修复 |
| --- | --- | --- | --- | --- | --- | --- |
| S1 认证与会话 | 0 | 0 | 3 | 3 | 6 | 1 |
| S2 路由授权矩阵 | 1 | 1 | 4 | 2 | 8 | 3 |
| S3 文件系统与路径边界 | 0 | 1 | 4 | 4 | 9 | 1 |
| S4 数据归属与隐私 | 0 | 1 | 2 | 3 | 6 | 3 |
| S5 前端身份状态 | 0 | 2 | 3 | 1 | 6 | 4 |
| **合计** | **1** | **5** | **16** | **13** | **35** | **13** |

这一轮的 P0 只有一个，但它的形状值得记下来：**能改媒体目录的人，等于能读应用自己的密钥。**

## 三、P0 详述

### S2-F1 媒体根可以指向实例数据目录 → 读 `secret_key` 伪造会话（持久化后门）

`tiklocal/web/settings.py` 的 `validate_folder()` 只检查「目录存在、可读」，不检查它在哪。而 `tiklocal/services/paths.py` 的实例数据目录里放着：

- `auth.json`（含 `password_hash`、`secret_key`、`auth_revision`）；
- `tiklocal.db`（索引、播放位置、事件、亲和度）；
- `download_config.json`、`jobs.json`、`cookies/`。

把媒体根设成数据目录之后，这些文件在库视图里是**普通媒体文件**：`/media/<uri>` 直接返回内容，`/api/media/info` 给出绝对路径与大小。于是拿到实例密码的会话可以：

1. `POST /api/library/sources {"path": "<data dir>"}`；
2. 等一次扫描，`GET /api/library?...` 找到 `auth.json` 的 uri；
3. `GET /media/<uri>` 读出 `secret_key`；
4. 用 `secret_key` 自签一个 `auth_revision` 更高的会话 cookie —— 之后即使改密码、删 `auth.json` 重建，攻击者只要留着自己的签名就能重新登录。

这条链不需要任何代码执行，也不依赖 Windows 特性，是「配置即权限」的典型越界。**修法**：

- `paths.is_in_data_dir(path)`：`resolve()` 后与 `get_data_dir().resolve()` 比较，相等或在其下即命中；
- `web/settings.py` 的 `validate_folder()` 与 `web/media.py` 的 `resolve_target_path()` 都拒绝命中项（返回 `MediaSourceError('data_dir')`，i18n 有对应文案）；
- `app.py` 启动时若已有源指向数据目录，打一条 warning 并在设置页提示，但不静默删源（用户可能真的把媒体放在数据目录旁边，需要他自己确认）。

**验证**：`test_round38_regressions.py::test_the_data_directory_cannot_become_a_media_source` 同时覆盖「新增源」和「打开既有 uri」两条路径。

## 四、P1 详述

### S2-F2 `auth.json` 损坏或删除后，全站 500 —— 包括 `/login`，无法自救

`is_authenticated()` 直接读 `auth_store.revision`，`revision` 属性在数据缺失时抛 `RuntimeError('Authentication has not been initialized')`。这个异常发生在**认证装饰器之前**，于是每个请求都 500；`/login` 的 GET 也会先调 `is_authenticated()`，同样 500。用户手上没有任何页面能改配置，只能手动去数据目录删文件。

**修法**：`is_authenticated()` 捕获 `RuntimeError` 返回 `False`；`login_view()` 在 GET/POST 开头单独探测，不可用时渲染 503 页（`login.error_store_unreadable`，提示去实例目录检查 `auth.json`），而不是把异常抛给错误处理器。

### S3-1 保存媒体源后，`apply_sources()` 用旧根目录映射

`_resolved_root_by_id` 只在 `__init__` 建一次，`apply_sources()` 更新源列表后不重建。结果：改名或换路径的源仍按旧根解析 uri，`resolve_target_path()` 返回不存在的路径，缩略图与详情页对整源失效，直到重启。

**修法**：`apply_sources()` 末尾重建 `_resolved_root_by_id`（与 `_load_sources()` 共用同一段构造逻辑），并加注释说明「源列表变更后必须重建」。

### S4-F1 从回收站还原，会永久丢掉续播点、观看历史、亲和度与向量

删除流程里 `media_index.delete(uri)` 会级联删 `playback_positions`/`media_events`/`media_affinity`/`media_vectors`；回收站保留的只有文件本身。于是「删除→还原」这条正常恢复路径回来的是一个**第一次见到的文件**：续播点回到 0、推荐流当新片、相似度重算。而收藏与合集反而保留（它们按 uri 存，不随索引删）。

**修法**：删除时先 `media_index.snapshot_state(uri)` 取一份（位置/事件/亲和度/向量），随回收站条目一起存；还原时 `restore_state()` 写回。`trash.restore()` 的返回值改为 `(uri, state)`，`web/media.py` 的还原路由负责回写。取不到状态时（老条目）按原样还原，不报错。

### S5-1 注销后按「后退」，整页私有媒体从 bfcache 原样恢复（0 请求）

浏览器对 `Cache-Control: no-store` 之外的 HTML 会放进 bfcache。注销只是清 cookie，页面本身还在内存里，后退即可看到缩略图、标题、播放位置——**认证被绕过，只是绕过的不是 HTTP 层而是内存**。

**修法**：`after_request` 对已认证的 HTML 响应加 `Cache-Control: no-store`（媒体流不加，避免破坏 Range 与缓存收益）；同时在 `logout` 响应里加 `Clear-Site-Data: "cache"`（浏览器不支持时忽略）。

### S5-2 设置页首屏的 6 个请求跑在 401 处理器安装之前

`settings_page_controller.js` 在 IIFE 顶部直接 `fetch()` 若干配置，而把 401 → 跳登录的包装器安装在 `DOMContentLoaded` 之后。会话过期时这 6 个请求各自失败，页面把失败当空态渲染：开关全关、源列表空，用户以为配置丢了，一保存就把空态写回去。

**修法（本轮未落地，转 backlog）**：把 401 包装器提到 IIFE 最前，或在 `base.html` 里用内联脚本在 `head` 安装。之所以不当轮改：它要求所有页面的控制器都遵守「先装包装器再发请求」的约定，属于跨 9 个文件的重构，超出「最小 diff」边界。

## 五、P2/P3 一览（当轮修复的 5 条 + 3 条）

| 级别 | 问题 | 位置 | 处理 |
| --- | --- | --- | --- |
| P2 | `FLASK_TESTING=1` 环境变量可关掉整站认证 | `app.py:216` | 已修：只认 `TIKLOCAL_TESTING` |
| P2 | 绝对路径泄漏到 `/api/media/info` 与 `/api/download/config` | `web/media.py`、`web/downloads.py` | 已修：只回文件名 |
| P2 | 401 重定向闩锁永不复位，一次 401 后整页静默 | `base.js` | 已修：成功响应复位 |
| P2 | 注销不清 `radio_station`，下一个人看到上一个的电台 | `base.js` | 已修 |
| P3 | `/api/v1/pair/` 尾斜杠 401 而非 308 | `web/radio.py` | 已修：`strict_slashes=False` |
| P3 | 删源不清理作者档案 | `web/settings.py` | 已修 |
| P3 | 回收站条目的 `name` 未校验 | `services/trash.py` | 已修：拒绝路径分隔符 |
| P2 | `cookie_dir` 可指向任意绝对路径 | `web/downloads.py` | 转 backlog（需先定「允许哪些目录」的产品语义） |
| P2 | 关认证后安全响应头一起消失 | `app.py` | 转 backlog |
| P2 | 限流键用 `remote_addr`，反代后一把锁锁死所有人 | `auth.py` | 转 backlog（需信任代理配置） |
| P2 | 默认口令 `admin666` + 默认 `0.0.0.0` 仅告警 | `run.py` | 转 backlog（属产品策略） |
| P2 | `/media`、`/thumb` 的 401 纯文本被当「缺图」渲染 | `web/media.py` | 转 backlog |
| P2 | CSRF 令牌只读一次，无 403 处理 | `base.js` | 转 backlog |
| P3 | 注销不清 `sessionStorage['tiklocal:vt-from']` | `base.js` | 转 backlog |
| P3 | 退出登录后旧 cookie 在 30 天内仍可重放 | `auth.py` | 转 backlog（需服务端会话表） |
| P3 | 已配对设备 token 在注销后仍有效 180 天 | `services/pairing.py` | 转 backlog |
| P3 | 下载任务持久化原始 URL | `downloader.py` | 转 backlog |

## 六、实现要点

1. **拒绝要发生在两条路上**：只挡「新增源」不够，既有的 uri 还能直接打开数据目录里的文件。所以 `validate_folder()` 与 `resolve_target_path()` 必须同时拒绝，测试也同时覆盖。
2. **认证读不出来 ≠ 认证通过，也 ≠ 崩溃**：`is_authenticated()` 返回 `False` 把请求导向登录页，再由登录页给出可操作的 503 —— 三态比两态多一个分支，但这一个分支决定了用户能不能自救。
3. **状态要跟着文件走**：回收站条目的身份是「文件 + 状态」，只存文件等于把用户的历史扔了。快照必须是「删除前」取的，删除后再取只能拿到空表。
4. **bfcache 是认证的旁路**：`no-store` 加在 HTML 上，不加在媒体流上；`Clear-Site-Data` 是补充而不是替代。
5. **`strict_slashes=False` 是契约修正**：前端拼的是 `.../pair/`，后端注册的是 `.../pair`，401 说明请求根本没进路由——这种「看起来像权限问题、其实是路由问题」的错配，只有把两边放在一起看才会暴露。

## 七、验证

- 新增 `tests/test_round38_regressions.py`（5 条）：数据目录不能成为媒体源、`auth.json` 损坏时登录页返回 503、源列表变更后 `apply_sources()` 用新根、回收站还原恢复状态、`/api/media/info` 不返回绝对路径。
- 聚焦回归：`test_auth`/`test_i18n`/`test_download_urls`/`test_media_sources`/`test_web_runtime`/`test_round07`/`test_round12`/`test_round15`/`test_tls` = **145 passed / 1 skipped**。
- 全量：**646 passed / 8 skipped in 302.71s**（第 37 轮收尾 641P/8S，新增 5 条）。
- 顺带修掉一处测试脆弱性：`tests/test_tls.py` 的两个用例固定端口 `8828`，在端口被 Hyper-V 预留的机器上必失败；改为由内核分配空闲端口后再写入配置。

## 八、覆盖率缺口

1. **没有真正的多身份端到端**：应用只有一道密码，所有「换个人」的场景都靠手工推理，没有自动化用例能证明「A 注销后 B 看不到 A 的状态」。
2. **bfcache 只有头部断言**：`no-store` 是否真的让 Safari 不进 bfcache，需要真机验证；本轮只断言响应头存在。
3. **回收站快照没有跨重启用例**：`state` 存在条目里，但「重启后还原」这条路径没有测试覆盖。
4. **限流与反代**：`X-Forwarded-For` 的信任边界没有测试，也没有文档说明部署在反代后面应该怎么配。

## 九、下轮建议

下一轮换到**可访问性与国际化**之外的一个尚未系统扫过的角度：**并发与锁的持有边界**——哪些锁在 I/O 期间被持有、哪些临界区里调用了回调、哪些「先查后写」没有原子性。第 22/33 轮碰过个别点（`url_list.pull()` 无锁、`enqueue` 去重），但没有按「锁的粒度与持有时长」系统过一遍。
