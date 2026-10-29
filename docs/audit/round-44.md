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
