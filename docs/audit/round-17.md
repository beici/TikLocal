# 第 17 轮审查报告（界面一致性 / 交互状态机）

日期：2026-10-27
起点基线：462P/6S/0F（第 16 轮结束后）
审查域：模板与 CSS 跨页面一致性、下载页队列与任务状态、流式交互（flow）、库页交互（Quick Viewer / 时间线 / 空态）、设置与详情页表单持久化

## 一、模板与 CSS 跨页面一致性（2×P2 + 3×P3）

| # | 级别 | 问题 | 位置 | 处置 |
|---|------|------|------|------|
| 1 | P2 | 全站没有 `@media print`：暗色主题打印出白字白纸（`dark:text-white` 在打印时仍生效，而背景色默认不打印），固定定位的浮动 Dock（`z-index: 241`）会印在每一页正文上 | `tiklocal/static/base.css`、`tiklocal/templates/base.html` | 已修：新增打印规则，隐藏 Dock/底栏/浮层，强制浅色前景 |
| 2 | P3 | `error.html` 用写死的浅色值 + 内联样式，暗色主题下文字与背景几乎同色 | `tiklocal/templates/error.html` | 已修：改用主题变量与 Tailwind 主题类 |
| 3 | P3 | 6 个页面的 `{% block title %}` 写死 `TikLocal`，浏览器标签与历史记录无法区分页面 | 各页模板 | 已修：`base.html` 给默认值，各页覆盖为「页面名 · TikLocal」 |
| 4 | P3 | 各页主内容区没有统一的可跳过锚点，键盘用户每页都要 Tab 过整条侧栏 | 各页模板 + `base.css` | 已修：统一 `id="main"` + 首元素「跳到主内容」链接（聚焦时显示） |
| 5 | P3 | `settings.html` 的「清空缓存」行缺少 `id="cache-copy"`，说明文字的 `aria-describedby` 指向不存在的节点 | `tiklocal/templates/settings.html` | 已修：补齐 id |

## 二、下载页队列与任务状态（2×P2 + 2×P2 + 2×P3）

| # | 级别 | 问题 | 位置 | 处置 |
|---|------|------|------|------|
| 6 | P2 | 失败任务把 yt-dlp 原文整段渲染（实测 421 字符），移动端单卡高达 251px，重试按钮被挤到首屏外 | `tiklocal/static/download_page_controller.js`、`download.css` | 已修：错误默认折叠为「查看错误详情」，正文限高可滚动 |
| 7 | P2 | 任务失败但已保存部分文件时，卡片仍显示 0 个文件，用户以为一无所获 | 同上 | 已修：失败态也显示「已保存 N 个文件」 |
| 8 | P2 | 导入链接后提示「已导入 N 个链接」，N 是清单总长度而不是本次新增数 | `tiklocal/services/url_list.py`、`tiklocal/web/downloads.py` | 已修：`add_reporting()` 返回 added/rejected/truncated，前端分别提示 |
| 9 | P2 | 队列为空时点「全部下载」，提示文案与实际不符且无引导 | `download_page_controller.js` | 已修：空队列明确提示先导入链接 |
| 10 | P3 | 并发度按钮初次渲染不反映已保存的配置（`aria-pressed` 与实际值不一致） | `tiklocal/static/settings_page_controller.js` | 已修：加载配置后调用 `applyConcurrency()` |
| 11 | P3 | 部分失败的任务（queued+failed 混合）没有「查看失败项」入口 | `download_page_controller.js` | 已修：部分成功时提供查看入口 |

## 三、流式交互（3×P2 + 1×P3）

| # | 级别 | 问题 | 位置 | 处置 |
|---|------|------|------|------|
| 12 | P2 | 前端从不发送 `since` 参数，服务端每页都从第一张重算，翻页会重复卡片 | `tiklocal/static/flow_page_controller.js` | 已修：记录首帧 `since` 并随后续分页发送 |
| 13 | P2 | 加载失败后 `loadFeed` 不恢复 `isLoading`，页面永久停在 loading 态 | 同上 | 已修：`finally` 复位并显示重试 |
| 14 | P2 | 预加载与用户滑动并发时旧响应覆盖新位置（无请求序号） | 同上 | 已修：`requestId` 自增，过期响应丢弃 |
| 15 | P3 | 收藏/合集操作成功没有反馈，用户不确定是否生效 | 同上 | 已修：成功 toast（带撤销语义的沿用既有组件） |

## 四、库页交互（1×P1 + 1×P2 + 2×P3）

| # | 级别 | 问题 | 位置 | 处置 |
|---|------|------|------|------|
| 16 | P1 | Quick Viewer 的 `focusin` 焦点陷阱把「加入合集」弹窗锁死：弹窗是 `#quick-view` 的兄弟节点，任何落在外面的焦点都被拽回查看器。实测点击名称输入框后 `activeElement` 仍是 `quick-view`，真键盘输入写不进 `#quick-collection-name`，`collection_picker.create()` 读到空值 → 新建合集 100% 不可用 | `tiklocal/static/library_page_controller.js` | 已修：陷阱对 `#quick-collection-modal` 放行 |
| 17 | P2 | 时间线「跳到某年」在加载失败或并发点击时被静默丢弃：`loadMore()` 对「失败」和「正有请求在跑」都返回 false，`revealYear` 直接放弃；重试按钮只补数据不重放跳转 | `tiklocal/static/library_timeline_controller.js` | 已修：`loadMore()` 返回共享 in-flight Promise，`revealYear` 记住待跳转目标，重试成功后自动重放 |
| 18 | P3 | 已加载末项按方向键时若下一页正在预取，该次按键被丢弃（counter 停在 `24 / 24`） | `library_page_controller.js` | 已修：等待在途分页完成后再前进 |
| 19 | P3 | 筛选后 0 结果仍显示「这里还没有可展示的媒体。」，与库内实际内容矛盾 | 同上 | 已修：空态按当前 mode 给出筛选相关文案 |

## 五、修复实现要点

- `tiklocal/services/url_list.py`：新增 `add_reporting(lines) -> (lines, added, rejected, truncated)`，逐行用 `normalize_url` 判定，重复项不计入 added，超过 `MAX_LINES` 计入 truncated；`add()` 保留为薄包装。
- `tiklocal/web/downloads.py`：`action == 'add'` 改调 `add_reporting`，响应 `data` 合并 `{added, rejected, truncated}`。
- `tiklocal/static/library_timeline_controller.js`：模块级 `inFlight` Promise 共享 + `pendingYear` 重放。
- `tiklocal/static/library_page_controller.js`：焦点陷阱放行弹窗、空态文案按 mode 分支、末项方向键等待在途分页。
- `tiklocal/static/download_page_controller.js`：错误块折叠、失败态文件数、导入计数分别提示、空队列提示。
- `tiklocal/static/flow_page_controller.js`：`since` 透传、`finally` 复位 loading、`requestId` 丢弃过期响应。
- `tiklocal/static/base.css` / `base.html` / 各页模板：打印样式、标题、跳过链接与 `id="main"`。
- i18n 新增（zh/en 成对）：`download.urls.import_rejected`、`download.urls.import_truncated`、`download.urls.queue_empty`、`download.error_details`、`download.job.saved_files`、`common.skip_to_content`。

## 六、验证

- `node --check`：`library_page_controller.js`、`library_timeline_controller.js`、`flow_page_controller.js`、`download_page_controller.js`、`settings_page_controller.js`、`ui_feedback.js`、`detail_page_controller.js` 全过。
- `python -m py_compile`：`url_list.py`、`downloads.py`、`i18n_pages_sync.py` 全过。
- 新增 `tests/test_round17_regressions.py`（7 个用例）：每页独立标题 + 跳过链接 + `id="main"`、错误页正文色不再用 `--dock-ink`、打印样式隐藏固定层、`retire_pwa.js` 带版本号、并发度按钮单一 `aria-pressed`、`add_reporting()` 的 added/rejected/truncated 计数、`/api/download/urls` 返回导入计数。
- 全量 `pytest -q` = **469 passed / 6 skipped / 0 failed**（151.95s；本轮新增 7 个用例，起点 462P）。
- 实测（真实 waitress，round-17 代码）：Quick Viewer 内点「加入合集」→ 输入框可获得焦点并写入；时间线断网后点年份 → 出现重试 → 恢复网络点重试 → 自动滚到目标年份。

## 七、转 backlog

1. 缩略图内容指纹（同 mtime 换内容仍判新鲜）——需要额外的哈希列与迁移，跨两轮评估后再做。
2. 下载 URL 幂等（同链接双标签重复入队）。
3. 跨标签广播（收藏/删除/队列的 storage 事件联动）。
4. 列表页可见性重校验（另一标签删除后卡片仍在）。
5. `config.yaml` 的 `download:` 段无读取入口。
6. Windows 保留名/长路径（`CON.mp4`、>260 字符路径）。
