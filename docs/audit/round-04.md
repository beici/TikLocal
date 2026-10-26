# 第 04 轮审查报告

- 日期：2026-10-27
- 起点：`pytest -q` = **2 failed / 381 passed / 6 skipped**（第 03 轮结束时）
- 终点：`pytest -q` = **2 failed / 381 passed / 6 skipped**（本轮修复集中在前端状态机；新增 2 个认证用例后为 **2 failed / 383 passed**）
- 轮次重点：前端状态机（下载页轮询、flow 相册与手势、库页时间线、电台播放、全局抽屉）
- 审查方式：5 个只读 subagent，域不重叠；3 个 fresh（下载页、库页、flow 竖滑）+ 2 个复用（电台/播放进度/抖音模式、全局壳层）。全部只读，探针只写 `%TEMP%` 与临时 instance

## 域划分

| subagent | 域 |
|----------|----|
| `audit-download-ui` | 下载页状态机（`static/download_page_controller.js`） |
| `audit-library-ui` | 库页时间线/探索网格（`static/library*.js`） |
| `audit-flow-ui` | flow 竖滑与相册（`static/flow_page_controller.js`） |
| `audit-frontend-shell`（复用） | 电台、播放进度、抖音模式（`static/radio_controller.js` 等） |
| `audit-web-routes`（复用） | 全局壳层/抽屉/主题（`static/base.js`、`templates/base.html`） |

## 确认并修复（17 条）

### 下载页

| 级别 | 位置 | 问题 | 处置 |
|------|------|------|------|
| P0 | `static/download_page_controller.js:343-350` | `renderJobs()` 在「载荷签名未变」时提前 `return`，而唯一的 `schedulePolling()` 在早退之后 → 排队中/0% 阶段连续两轮相同即让轮询链永久中断 | **修复**：早退前 `schedulePolling(hasActiveJobs)` 续期 |
| P1 | `static/download_page_controller.js:813-825` | 「批量入队」对博主引擎（`tiktok-downloader`）必然全失败（队列接口只创建无 payload 任务），对未安装 `gallery-dl` 同样必失败 | **修复**：提交前按引擎校验并给出可读提示（`download.error.tiktok_queue_unsupported` / `download.error.image_missing`） |
| P2 | `static/download_page_controller.js:611-617` | 首屏 `probe_binary` 失败后 `dependencyMeta` 永久标记「未就绪」，用户装好组件也不恢复 | **修复**：选中 `gallery-dl` 时补一次 `refreshSetup()` 再重选（带 `retry` 防递归） |

### flow 竖滑与相册

| 级别 | 位置 | 问题 | 处置 |
|------|------|------|------|
| P1 | `static/flow_page_controller.js:1053-1056` | 相册（image_group）子视频在**建 DOM 时就 `play()`**，整页元素预建 → 每个相册的子视频都在后台解码循环 | **修复**：删除建 DOM 时的自动播放，改由 `renderImageGroup` 在可见时播放 |
| P1 | `static/flow_page_controller.js:887-895` | 离开相册只暂停 `prevItem.el`（相册容器本身不是 video），子视频继续播 | **修复**：`prevItem` 非视频时对 `querySelectorAll('video')` 统一 `pause()+muted` |
| P2 | `static/flow_page_controller.js:1085-1095` | 从别的页滑回同一相册时命中「已在台上」的提前返回，子视频停在 `clearPreviousState` 暂停的状态 | **修复**：命中提前返回时若当前项可见则 `currentTime=0; play()` |
| P2 | `static/flow_page_controller.js:577-586` | 分页动画期间的上滑被丢弃（`settling` 直接 return） | **修复**：`queuedSwipe` 记录方向，动画结束后补一次 `slidePage` |
| P2 | `static/flow_page_controller.js:1396-1404` | 页尾单页加载失败时用全屏错误层（fixed 覆盖层会吃掉所有手势），用户被锁死 | **修复**：仅在一条都没有时用全屏错误页；已有内容时改为 toast，再滑一次即重试 |

### 电台

| 级别 | 位置 | 问题 | 处置 |
|------|------|------|------|
| P1 | `static/radio_controller.js:149-166` | `audio` 的 `error` 无连续失败上限 → 坏源 2 秒内触发 80 次 `error` 并无限切歌 | **修复**：`PLAY_ERROR_LIMIT=3`，超限停止自动跳歌并提示；`play` 事件清零 |
| P2 | `static/radio_controller.js:152-158` | 每次 `error` 都 `reportPlaybackExit('error')` 扣推荐权重，网络抖动也扣 | **修复**：仅 `MEDIA_ERR_DECODE(3)` / `MEDIA_ERR_SRC_NOT_SUPPORTED(4)` 记失败 |
| P2 | `static/radio_controller.js:355-362` | `audio.play()` 的 `AbortError`（切歌打断）被当成播放失败并记到新选中的曲目 | **修复**：`AbortError` 直接返回 |
| P2 | `static/radio_controller.js:866-876` | 队列耗尽后 `mediaSession` 仍显示上一首，锁屏按播放无反应 | **修复**：`metadata=null` + `playbackState='none'` |
| P2 | `static/radio_controller.js:775-778` | `isDarkTheme()` 只读 `body[data-theme]`，而该属性已移除 → 深色氛围失效 | **修复**：`body` 缺失时回退 `documentElement` |

### 库页

| 级别 | 位置 | 问题 | 处置 |
|------|------|------|------|
| P2 | `static/library_timeline_controller.js:224-244` | `loadMore()` 失败后底部 sentinel 仍处相交态，Observer 不再回调，「向下滚动可重试」实际无法触发 | **修复**：`#library-retry` 按钮 + `library-status-actions` 显隐；文案改为「点下方重试」 |
| P2 | `static/library_page_controller.js:376-381` | 瀑布流整表重建丢掉 `.is-selected`，整理模式「已选 N 项」但无高亮，批量操作作用在看不见的项上 | **修复**：重建后 `refreshTileSelection()` |
| P2 | `static/library_page_controller.js:915-920` | 弹层输入框的按键透传给 Quick Viewer（空格被吞、方向键换媒体，最终写进合集的是另一张图） | **修复**：`keydown` 来自 `input/textarea/select/contenteditable` 时直接返回 |
| P2 | `static/library_page_controller.js:1418-1421` | 网格与时间线两个 controller 同时绑定重试按钮，一次点击触发两套加载 | **修复**：时间线接管后打 `dataset.timelineBound`，网格侧跳过 |

### 全局壳层

| 级别 | 位置 | 问题 | 处置 |
|------|------|------|------|
| P2 | `static/base.js:51-105` | 抽屉 `role="dialog"` 无 `aria-modal`、Tab 可跑出抽屉、关闭后 keydown 监听未移除 | **修复**：`aria-modal=true` + `focusables()/trapFocus()` + 关闭时移除监听 |
| P2 | `static/base.js:117-128` | bfcache 恢复时 `DOMContentLoaded` 不再触发，抽屉卡在展开态并锁滚动 | **修复**：`pageshow(persisted)` 时复位 |
| P2 | `static/base.js:5-12` | 隐私模式下 `localStorage.getItem` 抛异常会中断整个初始化回调（抽屉菜单不绑定） | **修复**：try/catch |
| P3 | `templates/base.html:44` | `body[data-theme]` 由服务端硬编码 `light`，与首帧脚本写入的 `html[data-theme]` 冲突 | **修复**：移除服务端属性，统一由内联脚本 + `base.js` 写 `html` |

## 误报与转 backlog

**误报（已核对代码，缺陷不存在或早已修好）**

- 「`POST /api/download/jobs/<id>/cancel` 无请求体 → 400」：`tiklocal/web/downloads.py:250-255` 根本不读请求体，直接 `download_manager.cancel()`，实测 `200 {"success":true,"data":{"job":…}}`。
- 「CSRF 只认 `X-CSRF-Token` 头」：`tiklocal/auth.py:87-92` 已依次读头 → 表单 `_csrf_token` → JSON 体；查询参数刻意不支持（令牌会经 Referer/日志泄漏）。
- 「登录忽略 `next`」：`tiklocal/auth.py:79-84` 的 `safe_next()` + `:165/:191` 的 `redirect(next_url)` 早已实现（含 `//host` 与绝对 URL 拦截）。
- 「401 前先校验 CSRF」：`tiklocal/auth.py:144-147` 顺序为 `is_authenticated()` → CSRF，未登录请求拿 401。

**转 backlog**（本轮未改，理由见 `docs/audit/backlog.md`）

- 下载页加载失败无重试入口（需新增错误态 DOM 与文案，改造面比收益大）。
- 空状态未区分「从未配置引擎」与「暂无任务」。
- 凭证域名按 URL 全串比较（`services/credentials.py`）——未复现到真实影响，先记录。
- 电台列表失败静默（`loadTracks` catch）、429 文案分流——与上一轮 `MAX_PROBE_PER_REQUEST` 改动叠加，等下一轮统一处理。

## 验证

- `node --check`：`download_page_controller.js`、`radio_controller.js`、`flow_page_controller.js`、`base.js`、`douyin_mode.js`、`library_page_controller.js`、`library_timeline_controller.js` 全过。
- 全量 `pytest -q`：**2 failed / 381 passed / 6 skipped**，失败集合与起点一致（Windows 权限位断言）；新增 2 个认证用例后 `tests/test_auth.py` = **9 passed / 1 failed**（同一权限位用例）。
- 新增测试：`test_login_returns_to_the_requested_page_and_drops_external_targets`、`test_unsafe_api_request_without_session_is_401_not_403`。
- 浏览器实测（服务 `TIKLOCAL_AUTH_PASSWORD=audit-pass`，媒体源 71 项）：
  - 下载页轮询：修复后 12 s 首轮 + 每 2.2 s 一轮（19 s 内 4 次），不再因载荷相同中断。
  - 队列校验：博主引擎下点击批量入队 → toast「博主引擎不支持批量入队，请逐条提交链接。」（中文正常，i18n 生效）。
  - 库页时间线：注入 500 后「重试」按钮出现，点击后月份 8 → 16。
  - 主题：`body` 服务端不再输出 `data-theme`；深色模式 `.floating-dock` 首帧即为 `rgba(24, 24, 22, 0.86)`。
  - 抽屉：`role="dialog"` + `aria-modal="true"`，`Esc` 可关闭。
  - `/library?view=explore` 正常渲染（h1「探索你的媒体库」），25 张图仅 1 张懒加载未就绪。
