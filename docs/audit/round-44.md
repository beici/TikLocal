# 第 44 轮审计：错误信息与用户可理解性

日期：2026-10-29
起点基线：`893c5f6`（第 43 轮收尾），tag `audit-round-44-start`
全量基线：676 passed / 8 skipped

## 一、方法与范围

视角：**报错是否指出下一步动作、是否泄漏实现细节、是否有可复制标识**。
四个只读审查域 + 主代理自查：

| 域 | 范围 | P0 | P1 | P2 | P3 |
| --- | --- | --- | --- | --- | --- |
| X1 | 服务层错误构造：`tiklocal/services/*` | 0 | 1 | 6 | 2 |
| X2 | Web/CLI 错误响应：`tiklocal/web/*`、`run.py`、`thumbs.py`、`dedupe.py`、`app.py` errorhandler | 0 | 2 | 4 | 3 |
| X3 | 前端错误呈现：`static/*.js`、`templates/*.html`、`i18n.py` | 0 | 2 | 8 | 4 |
| X4 | 日志与隐私：`logging_setup.py`、`web/client_log.py`、`auth.py`、`paths.py` | 0 | 0 | 4 | 7 |
| — | 合计（去重后 43 条） | **0** | **5** | **22** | **16** |

探针（一次性脚本，未入库）：`/api/playback` 四种非法载荷、`/media?uri=` 缺参、
`/thumb` 缺参、404/405/413 框架响应、i18n 双语键集对比、`--port abc` / `--version` /
未知子命令的 CLI 出口码与 stderr。

## 二、问题总览

修复 **24 条**（P1 5 + P2 11 + P3 8），转 backlog **12 条**，其余为重复登记或已修。

## 三、P1（5 条，全部当轮修复）

1. **X1-1 扫描失败在界面上不存在**。`library_index.sync()` 把失败源的
   `str(error)`（含绝对路径）塞进 `source_errors`，设置页只拿 `Object.keys()`
   数个数；下载完成触发的二次扫描还会用 `again` 的字典整体覆盖 `result`，
   把第一次的失败抹掉。→ 失败值改为 `{'code': 'missing'|'unreadable', 'name': …}`，
   新增 `unavailable_names`，二次扫描**合并**而非覆盖失败表，服务端
   `LOGGER.warning` 记录原因（路径只进日志），设置页按名字提示，页面上的「扫描」按钮可原地重试。
2. **X2-1 500 的 reference 从未到达用户**。`app.py` 为每个 500 生成 8 位
   `reference` 并放进 JSON，但 `ui_feedback.js` 与设置页的 `readJson()` 只读
   `data.error`。→ 把 reference 追加进 `error` 文案（`…（3f9a1c2d）`），
   一处改动覆盖全部调用方，用户可直接把 id 粘进报告。
3. **X2-2 TikTok sidecar 原始异常直出**。`str(exc)` 会把
   `HTTPConnectionPool(host=127.0.0.1, port=9911)` 显示给用户。→ 下载入队、
   作者绑定/同步/作品更新/批量同步全部改走 `tiktok_user_message()`，细节进日志；
   并为「无 code 但文本含 host=」的兜底加了同一层过滤。
4. **X3-1 新建合集失败零反馈**。`collection_picker.js` 的 `catch` 只把名字写回
   输入框，按钮恢复可点、界面无任何文案。→ 补 `showMessage(errorText(...))`，
   Flow 与 Library 两个入口同时生效；勾选失败的静默回弹也补了文案。
5. **X3-2 网络异常以浏览器英文原文上屏**。`TypeError: Failed to fetch` /
   `Load failed` / `NetworkError when attempting to fetch resource` 在 7 个页面
   30+ 处直接进入 toast 与状态行。→ `ui_feedback.js` 新增
   `TikLocalUI.errorText(error, fallbackKey)`，并让 `toast()` 自己识别这几种
   原文（含 Error 对象入参）替换为 `common.error.offline` / `common.error.timeout`；
   库页搜索状态行、Flow 信息面板、设置页作者同步改用该 helper。

## 四、P2 修复（11 条）

| 编号 | 位置 | 修法 |
| --- | --- | --- |
| X2-3 | `services/library_index.py` | `source_errors` 值带 code/name，浏览器不再收到路径 |
| X2-4 | `web/settings.py`、`settings_page_controller.js` | 扫描结果透传 `unavailable_names`；提示文案点出目录名 |
| X2-5 | `web/media.py` | `/api/playback` 统一 `{success, saved}` 形状，400/404 改本地化文案 |
| X1-P2-1 | `services/downloader.py` | 新增 `download_failure_message()`：`OSError` 只给 `strerror`+errno，不带路径 |
| X1-P2-5 | `services/thumbnail.py` | 失败日志只写 `found/missing`，不再打印 ffmpeg 绝对路径 |
| X3-4 | `static/settings_page_controller.js` | 作者同步的 `.catch(() => {})` 改为落行内状态 + toast |
| X3-5 | `static/download_page_controller.js` | 组件探测失败不再静默，改为 warning toast |
| X3-6 | `static/radio_controller.js` | 电台加载失败给出原因 + 「重试」按钮（新增 `radio.retry`） |
| X3-7 | `static/ui_feedback.js/.css` | `warning` 类型有自己的三角图标与配色（原先借用成功勾） |
| X3-8 | `static/flow_page_controller.js` | 信息面板不再把 `error.message`（可能含路径）塞进 DOM |
| X3-9 | `static/author_page_controller.js` | 作者页 POST 加 120 s 超时，`setBusy` 不会永久锁死 |

## 五、P3 修复（8 条，落在 5 处）

- `web/client_log.py`：上报文本先 `redact_text()` 再截断（页面 URL 里的 token 不进日志）。
- `auth.py`：登出写审计日志（登录有、登出没有）。
- `services/tiktok_downloader.py`：无 code 的错误文本含 `host=` 时回落固定文案。
- `i18n.py` / `i18n_pages_radio.py`：新增 `download.error.os_failure`、
  `download.probe_failed`、`radio.retry`、`common.error.offline`、`common.error.timeout`。
- 测试契约同步：`tests/test_playback_resume.py` 断言从整字典改为 `['saved']`。

## 六、未修清单（转 backlog，12 条）

- X1-P2-5 缩略图失败只有占位图，界面仍不说明原因（需要缩略图服务暴露失败原因，改动面大）。
- X1-P2-7 `GET /api/download/urls` 返回绝对 `store_path`（`tests/test_download_urls.py:229` 锁定，前端未使用；改契约需同步测试）。
- X2-6 CLI 顶层未捕获异常仍打整段 traceback（`run.py` 有 `_fatal`，但非 `TikLocalError` 的异常走 Flask 之外的路径）。
- X2-7/8 部分 4xx 仍用英文常量（`/media` 缺参、`/thumb` 缺参）。
- X3-3 `image_detail` / `detail` 页仍把 `error.message` 直接写入 DOM。
- X3-10 `experiments/similarity.js` 的错误文案未走 helper。
- X4-3 日志中的查询串（`uri=`、`q=`）未脱敏，只做了凭据脱敏。
- X4-4 家目录/数据目录前缀未在日志中替换为 `~`。
- X4-6 客户端上报的 `stack` 仍可能含完整脚本 URL。
- X4-7 登出日志不含来源标签页信息。
- X1-P2-2/3 服务层部分异常仍以 `str(exc)` 冒泡到路由。
- X2-9 `thumbs` 命令的失败摘要不含失败文件名。

**记账缺口（诚实记录）**：本轮记录 43 条、当轮修 24 条、本节列 12 条，差额 7 条在方案复核与去重时并入了同一处代码的已修项，但原记录没有逐条留痕哪一条并进了哪一条。这是文档缺陷，不是 7 条「凭空消失」；后续若要回收这 7 条，需按 `round-44.md` 的域表重新对齐一次。

## 七、验证

- 新增 `tests/test_round44_regressions.py`（8 条）：playback 400/404 形状与本地化、
  API 500 的 reference 出现在文案中、`download_failure_message` 不含路径、
  客户端上报脱敏、TikTok 文案不含 host、登出留痕、本轮新增键双语齐备。
- 语法：8 个改动过的 JS 文件 `node --check` 全部通过；`compileall tiklocal` 通过。
- 聚焦：`tests/test_playback_resume.py tests/test_thumbnails.py tests/test_auth.py
  tests/test_web_runtime.py tests/test_downloader_lifecycle.py tests/test_download_urls.py
  tests/test_round44_regressions.py` → 66 passed（29.06s）。
- 契约同步：`tests/test_round35_regressions.py` 的 `source_errors` 断言从字符串改为
  `{'code','name'}`；`tests/test_playback_resume.py` 断言从整字典改为 `['saved']`。
- 全量：`pytest -q` = **684 passed / 8 skipped / 0 failed**（281.81s）。起点基线
  676 passed / 8 skipped，本轮净增 8 条用例，无新增失败。
- 收尾复核（本次补做）：删除本轮误加的 7 个无人引用的 i18n 死键
  （`download.error.{disk_full,permission,failed_retry,index_busy,no_filename,tiktok_status_unreachable}`、
  `common.error.storage_unreadable`），`tests/test_i18n.py` 14 passed；二次全量
  `pytest -q` = **684 passed / 8 skipped / 0 failed**（220.01s）。
- UI 实测（真实 Chromium，端口 8123 起临时实例）：用调试规则把
  `/api/radio/stations`、`/api/radio/tune`、`/api/radio/items` 全部 mock 成 503，
  重载后电台页显示「加载失败 / 加载失败，请检查网络后重试。/ 重试」；解除 mock
  后点「重试」，页面恢复并重新播放。截图核对样式，无控制台报错。

## 八、收尾时发现并修正的两处自身缺陷

1. **电台「重试」按钮其实看不见**。原实现把按钮塞进 `#upcoming-preview`，
   而该元素是 `class="hidden" aria-hidden="true"` 的离屏列表（`radio.html:156`），
   全仓没有任何地方切换它的 `hidden`。也就是说「失败后给出下一步动作」这个
   P2 修复在 DOM 里存在、在屏幕上不存在。改为放进可见的 `#track-meta`，加
   `has-action` 状态让该行从 `nowrap` 改为可容纳按钮，并补 `.radio-retry` 样式。
2. **`errorText()` 会把自家 bug 说成网络故障**。原判断是
   `error instanceof TypeError || NETWORK_TEXT.test(...)`；fetch 的网络失败确实是
   `TypeError`，但「Cannot read properties of undefined」也是。后者被替换成
   「无法连接服务器」，会把用户和排查者一起引向错误方向。改为只按文案匹配，
   并把 Safari 的 `The network connection was lost.` 补进正则。

## 九、结论

本轮把「错误在用户眼里长什么样」从「各页面各写各的」收敛成一条链路：
服务端给可行动文案与可复制 id、`TikLocalUI.errorText` 统一兜底浏览器英文、
`toast` 的 warning/error 有区分。仍未解决的是「失败后没有下一步动作」的少数场景
（缩略图失败、CLI traceback），已按成本转入 backlog。

收尾补正的两条也值得记：**修「失败要有出口」的时候，出口本身可能根本不可见**——
组件在 DOM 里、在屏幕外，测试又只断言 DOM 存在，就会一起骗过所有人；
**用类型判断用户可见文案时，先问这个类型还会被谁抛出**。

## 十、收尾后的分诊：16 条历史 P0/P1 结清（未开第 45 轮）

用户问「这 16 条有必要修复吗」。答案先摆证据：**0 条已修、16 条仍存在（3 条部分修复）、无真 P0**（原标 `[P0]` 的两条按当前影响降为 P1）。逐条核查表见 `总结-44轮.md` 5.1，处置结果见 `backlog.md` 顶部「分诊」一节（那一节插在文件开头后，backlog 的行号整体下移了，别拿旧行号去对）。判断标准是一句话：**单用户的本地媒体服务器，不是公网 SaaS**——值得修的只有安全边界，和「一次交互就卡住」；十万文件规模下的慢、默认关闭的实验特性、1px 的视觉抖动都不值得修。

分诊结果：**修 2 条、关闭 7 条、转成规矩 3 条、留 4 条**。留下的 4 条是 `records()` 无 LIMIT（第 432/702 行，同一根因）、feed 每页建全库 dict + 每请求三次读 `favorites.json`（第 433 行）、设置页 6 个请求跑在 401 包装器之前（第 648 行）。**这 4 条后来也结了**——用户接着问「欠着的 4 条有必要修吗」，量完之后的结论见下面第十一节：3 条修掉、1 条明确不修。

### 10.1 修掉的两条

**默认口令（安全类，唯一一条）。** 原状：首次启动安装 `admin666` 的哈希，而默认绑定是 `0.0.0.0`，启动只打一行 WARNING。改法是生成而不是设默认——12 位、字母表去掉 `0/O/1/l/I`（这串要从终端手抄），只打印一次，文件里只存哈希，`AuthBootstrap.default_password` 相应改名 `generated_password`：

```python
PASSWORD_ALPHABET = 'abcdefghjkmnpqrstuvwxyz23456789'   # 12 位 ≈ 59 bit
password = supplied or generate_password()
```

三个入口都说清楚：`run.py` 的 banner 打印一次并提示改法；`tiklocal auth status` 首次运行时能打印，重启后**打不出来**（只有哈希），于是显式说「已生成且只显示过一次，忘了就 `auth set-password`」；`app.py` 只在日志里说「生成了首启口令，在 banner 里」，**口令本身不进日志**——日志是第一个被贴进 issue 的东西。

老实例不能自动迁移（明文早就没了），所以加了 `uses_published_default()`：文件里仍认 `admin666` 时，启动打 WARNING 并指出改法。默认绑定没动，`0.0.0.0` 是这个产品的主要用法（手机连电脑），防护靠口令而不是靠绑回环。

顺带一个坑：`password_generated` 这个标记必须写进 `auth.json`，否则「忘了口令」的用户看到的是一个登录框和零解释——而 `auth status` 手里只有哈希，无法区分「是你设的」和「是机器生成后没人抄下来」。

**`/thumb` 槽位超时。** 原状：`with _GENERATE_SLOTS:` 无超时，waitress 8 个线程、槽位最多 4 个，冷缓存首屏 8 个瓦片就能把每条请求线程（包括和缩略图无关的 JSON 接口）按在信号量上。改成 `acquire(timeout=5.0)` 超时抛 `ThumbnailBusy`，`/thumb` 返回占位图、`/api/thumbnail/frame` 返回 503 + 可读文案（`detail.error.thumbnail_busy`）。

关键约束是**不能写失败标记**：原实现在大图槽位超时后 `return False`，调用方当成「这个文件坏了」，写一次失败标记就把它藏一小时——一次赶巧的排队能把一张好图藏起来。所以 `ThumbnailBusy` 是一个独立的 `Exception` 子类（不复用任何既有失败类型），并且在 `_generate_image` 的宽 `except Exception` **之前**单独 `raise`——否则宽分支会把它当成解码失败，绕过这整套设计。日志按 30 s 一次汇总（带同一波被吞掉的条数），因为冷缓存时它是常态而不是故障；`similarity` 的批量索引遇到它就 `continue`，让文件留在 pending，而不是记成「损坏文件」或「embedding 服务挂了」。

### 10.2 为什么另外七条不再修

写进 backlog 的 §2，每条一句理由。共同点是：**它们在两万文件规模以下的代价测不出来，而修法都要改契约**。例如每页 `COUNT(*)` 改成 keyset 会让分页器失去总数；搜索的 `LIKE '%term%'` 要根治得引 FTS5，而真正痛的是非 ASCII 文件名搜不到（那要单独一轮做 `search_key`，会顺手解决本条）；`media_vectors` 读 BLOB 只在默认关闭的 `similarity_enabled` 下发生。

### 10.3 「性能」改成一个提问，不再开条目

三条规模类问题（首建索引同步读 EXIF、`/library` 全表聚合、启动期同步全扫）从 backlog 正文撤下，换成三步：先问文件数（不到 2 万不修）→ 超过才用 Performance 面板录一次并贴帧时间（没有数字的「慢」不上报）→ 只有「一次交互就卡住（>300 ms 且用户在场）」才进「值得修」。改法留在原条目里，谁量到真慢直接开工。

### 10.4 验证

`pytest -q` = **688 passed / 8 skipped / 0 failed**（提交树上实测三次：237.59s / 209.41s；收尾时是 684P，分诊补了 4 条；第二次分诊再补 7 条后是 695P，见 §11.4）。真实进程实测：`tiklocal serve` 首次启动打印 `Access password (generated, save it)` + 12 位口令，用它对 `/login` POST 得 200，用 `admin666` 得 401；重启后 `auth status` 只说「已生成且只显示过一次」而不再打印；把口令改回 `admin666` 后 `uses_published_default()` 为真、banner 恢复 WARNING。八个页面（`/`、`/flow`、`/library`、`/favorite`、`/collections`、`/radio`、`/download`、`/settings/`）登录后全 200，`/thumb?uri=photo.jpg` 出真图（`image/jpeg`）、坏文件的 `/thumb` 仍走占位图。缩略图侧新增两条用例：占满槽位后 `generate_thumbnail` 抛 `ThumbnailBusy` 且**不产生失败标记**，图片路径的宽 `except Exception` 不吃掉它。

### 10.5 记一条

**「安全类」和「性能类」不能用同一把尺子。** 默认口令和槽位无超时之所以该修，不是因为它们「严重度高」，而是因为前者是一个任何人都能走进来的门，后者是一次正常操作就会撞上的墙；而十万文件的慢是「你还没到那个规模」。分诊真正要回答的不是「这条严不严重」，而是**「这个用户、这个规模，会不会在这一次交互里遇到它」**。

## 十一、剩下四条 P1：用同一把尺子量完（仍未开第 45 轮）

用户接着问「欠着的 4 条有必要修吗」。这 4 条是第 10 节表里标「仍待修」的那行：`records()` 无 LIMIT（432）、flow 每请求物化整库（702，同一次调用）、feed 两份全库 dict + 三次读 `favorites.json`（433）、设置页 6 请求跑在 401 包装器之前（648）。

### 11.1 先量再判断：造 1:1 的表才量得准

`records()` 的成本只能在自己的表上量（临时库要先 `PRAGMA temp_store = MEMORY`——本机 TEMP 不可写，否则 `ORDER BY` 直接 `unable to open database file`）。实测 `MediaIndexStore.records()`：

| 行数 | 127 | 2 000 | 8 000 | 20 000 | 50 000 |
| --- | --- | --- | --- | --- | --- |
| `SELECT *` + 建 dict | 5.0 ms | 17.8 ms | 60.0 ms | 168.1 ms | 584.9 ms |

拆开看 20 000 行：`SELECT *` fetchall 96.5 ms、`_to_library_record` 建 dict 60 ms、只取两列 28.7 ms。**这台机器的真实库只有 127 条**（image 64 / live 35 / video 28），`records()` 是 5.0 ms。这正是第 10 节那把尺子的答案：**用户不会遇到它，只有「规模」这一条在变。**

`favorites.load()` 单次成本（100 / 1 000 / 5 000 / 10 000 条）：0.33 / 2.15 / 11.1 / 21.3 ms，三次/请求就是 63.9 ms @1 万。另外发现 `recommendation.py:112` 是 `canonicalize_many(self.favorites.load())`——`load()` 内部已经 canonicalize 过，这一层**把 1 万条收藏又算了一遍**，17.2 ms，占 21.3 ms 的 80%。

### 11.2 修了三条

- **648 是四条里唯一「后果恶劣」的，而且它根本不是性能问题。** 401/403 包装器装在 `base.js` 里，而 `base.js` 是 `</body>` 前最后一个脚本；`settings_page_controller.js` 一执行就发 6 个请求（`loadStats` / `loadRadioDevices` / `loadTrash` / `loadDownloadSettings` / `loadTikTokDownloaderStatus` / `loadAuthorSources`），全会跑在包装器之前。会话过期时这 6 个各拿 401 文本，页面按「没有数据」渲染，**用户一保存就把这套空态写回服务端**。
  改法不是给 9 个控制器排队（下一个页面还会忘），而是把包装器搬进 `tiklocal/static/session_guard.js`，在 `base.html` 的 `<head>` 里、`csrf_fetch.js` 之后加载。它只需要 `window`、不碰 DOM，所以能提到任何控制器之前。`base.js` 里那段删掉只留一行注释——两个包装器会 redirect 两次、每个响应 clone 两次。
- **433 三次读 JSON → 一次解析。** 新增 `FavoriteService.read_only()`，走已有的 `_load_cached()`（按 `mtime_ns + size` 判 stamp），返回只读视图不复制；`recommendation.py:114/226/255` 与 `radio.py:229` 换成它，并删掉那层重复 canonicalize。同一批把另外两处纯读也换了：`web/library.py` 的收藏页分页（它只把 set 交给 `dict.fromkeys`）与 `web/settings.py` 的 `/api/library/stats`。`{'uri','mtime'}` 那份列表（2 万行 2.5 ms）刻意不动——它比候选查询便宜两个数量级，改了只增加出错面。
- **432 窄列。** `SELECT *` → 15 列 `RECORD_COLUMNS`（`_to_library_record` 实际读的全部列），2 万行 192 ms → 135 ms，产出记录**逐字段相等**（用一个 2 万行的库对拍确认）。

### 11.3 一条明确不修

**432 的 LIMIT 与 702 不加。** 还在用 `records()` 的三个调用方语义上都需要完整集合：tag feed 与深链 caption feed 在 Python 里匹配标题（标题从文件名派生，SQL 无法预过滤），作者页按文件名里解析出的发布时间排序。加 LIMIT 只会**静默少条目**——那是把「慢」换成「错」。日常 Flow 根本不经它（走 `records_for_names` 取那 ~24 张）。docstring 里记了实测曲线，谁量到 2 万文件以上变慢，就按 keyset 分页开工。

### 11.4 验证

`pytest -q` = **695 passed / 8 skipped / 0 failed**（228.62 s）。新增 `tests/test_session_guard.py` 七条：只读视图复用一次解析且文件变了就失效、能看见经服务写入的改动、不泄漏可变缓存；收藏增删之后收藏页与 `scope=favorite` 列表立刻看到变化（读那条缓存了，写这条路仍然打通）；对 `/`、`/flow`、`/library`、`/settings/` 断言 `session_guard.js` 在首个 controller 之前、`base.js` 在其后；`base.js` 不再装第二个包装器。

端到端用真实进程验：`tiklocal serve --port 8877` 起一个 6 文件的小库，登录后 11 条路由全 200；`/api/feed/mix?tag=%23travel` 返回 2 条（`#travel 山顶`、`#travel 海边日落`）——这条正是走 `records()` 全表 + Python 匹配的路径，窄列改动后结果不变。Chromium 里在 `/settings/` 上读脚本顺序 `guard=8 < controller=9 < base=10`，换新实例让会话失效后 `fetch('/api/library/stats')` 得 401，页面跳到 `/login?next=%2Fsettings%2F`。

### 11.5 记一条

**「修」和「关」都要有数字。** 上一节用「2 万文件」当分界线时，它是估的；这一节把它换成了实测曲线，于是同一把尺子自动给出了三种答案：这一条要修（648，后果是错的）、这一条修一半（432，窄列是纯收益，LIMIT 是纯风险）、这一条不修（702，2 万行 135 ms 是「翻页多 0.1 秒」不是「卡住」）。判据一旦落到表上，结论就不再需要争论。
