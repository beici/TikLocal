# 第 23 轮审查报告（flow/feed / 库与详情 / 电台下载设置 / 可访问性与多语言 / 响应式布局 / 跨页持久化 / 前后端契约）

- **轮次**：23
- **日期**：2026-10-29
- **起点基线**：HEAD `92d9449`（tag `audit-round-23-start`），`pytest -q` = **602 passed / 8 skipped / 0 failed**（443.86 s）
- **审查域**：① flow 与 feed 前端状态机 ② 库/详情/查看器 ③ 电台、下载、设置、搜索、作者、登录 ④ 可访问性与多语言 ⑤ 响应式与布局可达性 ⑥ 跨页/跨会话持久化 ⑦ 前后端契约一致性

> 说明：本轮把审查视角从第 22 轮的「数据完整性 / 并发与资源泄漏 / 性能」轮换成「**前端状态机（竞态、旧请求覆盖新状态、监听器与定时器泄漏、跨页状态污染）+ 可访问性与多语言（键盘可达、焦点管理、ARIA 语义、i18n 键与占位符）**」，域的划分也从「按代码层」改成「按功能切」，避免连续两轮在同一批文件里找同类问题。
>
> 七个域共报 **53 条**（P0×0 / P1×7 / P2×25 / P3×21）。其中两处是重复上报：网格卡片不可聚焦同时被 D2（库/详情域）与 D4（可访问性域）报为 P1；skip link 目标带 `aria-hidden` 同时被 D1 报为 P2、被 D4 报为 P3（按 P2 计）。去重后 **51 条**：P0×0 / P1×6 / P2×24 / P3×21。
>
> 处置：**修 24 条**（6 条 P1 全部修完 + 10 条 P2 + 8 条 P3），**转 backlog 27 条**（14 条 P2 + 13 条 P3），驳回 0 条。本轮改动全在前端（JS/CSS/模板）与一处模板上下文清理，共 **14 文件 +165/−31**。
>
> 另有**独立复审提出的 4 条阻塞项**（全部是本轮修复自己引入或没修完的：守卫过宽吞掉方向键、收藏键的空格处理器落在抖音分支里成了死代码、图片详情同一处过宽、tag 流隐藏页头连带丢掉移动端唯一导航入口），已当轮修完并复验，见 §十三。这些不计入上面的 51 条发现。
>
> 本轮没有新增 `pytest` 用例：所有修复都是浏览器里的交互与可访问性行为，仓库没有 JS 运行时测试，而 `AGENTS.md` 明确要求「移除 HTML/CSS/JavaScript 源文本断言，而不是保留实现细节」。因此验证改为**真实 Chromium 冒烟**（见 §十），断言的是 DOM 与事件行为，不是源码文本。

## 一、flow 与 feed 前端状态机（域 D1）

| # | 级别 | 问题 | 位置 | 处置 |
|---|------|------|------|------|
| 1 | **P1** | document 级 keydown 的守卫只排除输入控件，空格一律 `preventDefault()` 后暂停/播放：焦点在侧栏收藏按钮上时，一次空格既点赞又暂停播放；倍速/字幕/放大镜/合集按钮的原生空格激活被取消，只能按 Enter | `tiklocal/static/flow_page_controller.js:2305-2329` | 已修 |
| 2 | P2 | 收起的倍速面板只设 `opacity: 0; pointer-events: none`，7 个按钮仍在 tab 序列且可激活，焦点环不可见、误按直接改播放速度 | `tiklocal/static/flow.css:618-634` | 已修 |
| 3 | P2 | `hideFlowStatus()` 只移除 `is-visible`，不复位 retry/next/library/actions 的 `hidden`，容器仅 `opacity: 0` → 不可见按钮仍可 Tab 聚焦，回车会导航离开 | `tiklocal/static/flow_page_controller.js:233-238` | 转 backlog |
| 4 | P2 | `#main`（skip link 目标）带 `aria-hidden="true"`，键盘用户回车后焦点落在 aria-hidden 元素上，读屏不播报 | `tiklocal/templates/flow.html:9` | 已修 |
| 5 | P3 | 进度条无可访问名，读屏只播报「滑块 0」 | `tiklocal/templates/flow.html:43` | 已修 |
| 6 | P3 | 侧栏合集按钮的可访问名取自子树内容（计数），播报「0 按钮」/「3 按钮」 | `tiklocal/templates/flow.html:81-84` | 已修 |
| 7 | P3 | 放大镜开关无 `aria-pressed`，开关状态对辅助技术不可见 | `tiklocal/templates/flow.html:71`、`static/flow_ui_shared.js` | 已修 |
| 8 | P3 | `pagehide` 上报活动后置空 `activeActivity`，bfcache 恢复时无 `pageshow` 重建 → 当前条目 visible_ms 丢失 | `tiklocal/static/flow_page_controller.js:2384-2386` | 转 backlog |

域内已排除：`flow_session.js` 的 epoch 守卫（`reset`/`dropHead` 后旧响应不会覆盖新 feed）；`toggleFavorite` 先用 `syncFavorite` 取权威值再覆盖、失败回滚；`feedSince`/`seed` 由首页响应捕获并回传，cursor 只前进；4 个 JS 文件的 60 个 `T('...')` 键全部存在；`showItem` 的 activationId 守卫。

## 二、库、详情与查看器（域 D2）

| # | 级别 | 问题 | 位置 | 处置 |
|---|------|------|------|------|
| 9 | **P1** | explore 网格卡片只绑 `click`、`img.alt=''`、无 `tabindex`/role/可读名称 → 键盘与读屏用户无法打开任何媒体（同页时间线用的是 `<button aria-label>`） | `tiklocal/static/library_page_controller.js:301-356` | 已修 |
| 10 | **P1** | 图片详情页键盘守卫只排除输入控件，随后 `case ' ': case 'Enter': preventDefault(); zoomIn()` → 在「生成标题」「更多」「全屏」「缩放」按钮上按 Enter/空格被吞掉，整页按钮键盘失效 | `tiklocal/static/image_detail_page_controller.js:386-392` | 已修 |
| 11 | P2 | 切换条目时 `onSourceChange` 丢弃第二个参数 name，`updateSourceButton` 也不校验 name → 慢响应把「来源」按钮指向上一条的 URL | `tiklocal/static/library_page_controller.js:636-647,689-690` | 转 backlog |
| 12 | P2 | 提示词弹层 `role="dialog" aria-modal="true"` 但打开不移动焦点、无 Tab 陷阱、关闭不归还焦点 | `tiklocal/static/image_detail_page_controller.js:293-301`、`templates/image_detail.html:172` | 转 backlog |
| 13 | P2 | 全屏层无 role/aria-modal、打开不移动焦点、Esc 后焦点落在已隐藏的关闭键上 | `tiklocal/static/image_detail_page_controller.js:172-203`、`templates/image_detail.html:145` | 转 backlog |
| 14 | P2 | 首页「那年今日」链接用 `firstChild.nodeValue` 覆盖文案，链接与无障碍名只剩日期 | `tiklocal/static/home_page_controller.js:119` | 转 backlog |
| 15 | P3 | 库页 Escape 分支放在按钮守卫之后，焦点在按钮上时 Esc 失效 | `tiklocal/static/library_page_controller.js:941-945` | 转 backlog |
| 16 | P3 | 合集弹层 `restoreFocus` 不判可见性，焦点落到 body | `tiklocal/static/collections_page_controller.js:85-89,117,137` | 转 backlog |
| 17 | P3 | 年份弹层选中后焦点掉回 body、无 Esc、无 `aria-expanded` | `tiklocal/static/library_timeline_controller.js:202-206` | 转 backlog |
| 18 | P3 | 缩放按钮的 aria-label 覆盖百分比且数值变化不播报 | `tiklocal/static/image_viewer_controller.js:31-34` | 转 backlog |
| 19 | P3 | `.mode-tabs` 是普通 div 带 aria-label（被忽略），无 `aria-pressed` | `tiklocal/templates/library.html:60-64` | 转 backlog |

域内已排除：三个弹层关闭时 `display: none` 且无可聚焦子元素；Quick Viewer 的 focusin 陷阱正确；`toggleFavorite(name)` 丢参但有去重与回写；`collection_picker` 双绑定不双开；本域 140 个 JS 键 + 153 个模板键全部存在。

## 三、电台、下载、设置、搜索、作者、登录（域 D3）

| # | 级别 | 问题 | 位置 | 处置 |
|---|------|------|------|------|
| 20 | **P1** | 「进行中」列表每 2.2 s 整块重建（`jobsSignature` 含 `progress_percent`），容器是 `aria-live="polite"` → 读屏每轮重念整表，卡片「取消」按钮每轮被销毁、键盘无法取消进行中的任务 | `tiklocal/static/download_page_controller.js:357-393`、`templates/download.html:86` | 已修 |
| 21 | P2 | 删除确认遮罩无焦点陷阱、不归还焦点（同文件另一处确认弹层实现完整） | `tiklocal/static/download_page_controller.js:714-750` | 转 backlog |
| 22 | P2 | 设置页 5 个、作者页 2 个 `aria-modal` 对话框只切 `hidden`，Tab 可穿到背景 | `tiklocal/static/settings_page_controller.js`、`static/author_page_controller.js` | 转 backlog |
| 23 | P2 | 设置页与作者页顶层裸 `localStorage`：存储被禁时 `init()` 中途抛错，后续初始化全部不执行（同文件其它位置已有 try/catch 范式） | `tiklocal/static/settings_page_controller.js:451,471`、`static/author_page_controller.js` | 已修（设置页；作者页转 backlog） |
| 24 | P2 | 电台 `role="listbox"` 只处理 Escape，方向键无效 | `tiklocal/static/radio_controller.js:214-219` | 转 backlog |
| 25 | P2 | `#station-options` 自 `fc4551a` 起恒为 `class="hidden"`，切换入口不存在 | `tiklocal/templates/radio.html:160` | 转 backlog |
| 26 | P2 | 作者同步后整列重建，覆盖另一行未提交的输入 | `tiklocal/static/settings_page_controller.js:963-995,1045` | 转 backlog |
| 27 | P3 | 导入 textarea 无 label / aria-label | `tiklocal/templates/download.html:66` | 转 backlog |

域内已排除：`radio_scene.js` 的 rAF 单属主 + `destroy()` 成对；`radio_ambience.js` retire 前有实例比对；backlog 里「电台裸 localStorage」被本轮升级为 P2 处理（原记录低估了影响：顶层抛错会让整页控件失效）。

## 四、可访问性与多语言（域 D4）

| # | 级别 | 问题 | 位置 | 处置 |
|---|------|------|------|------|
| 28 | **P1** | 合集弹层关闭态只有 `opacity: 0; pointer-events: none`，无 `visibility: hidden` → 关闭时关闭键、合集项、名称输入框仍在 Tab 序列，焦点钻进 `aria-hidden` 子树 | `tiklocal/templates/flow.html:196`、`static/flow.css:786-798` | 已修 |
| 29 | **P1** | （与 #9 重复）网格卡片不可聚焦 | — | 合并计 |
| 30 | P2 | `#info-btn` 无 aria-label | `tiklocal/templates/flow.html:86` | 已修 |
| 31 | P2 | `#favorite-btn` 是 `<a>` 却设 `aria-pressed`（link 角色不接受该属性） | `tiklocal/templates/flow.html:76` | 已修 |
| 32 | P2 | 抖音开关的 label 内唯一子元素带 `aria-hidden` → 复选框无可访问名 | `tiklocal/templates/settings.html:44-47` | 已修 |
| 33 | P2 | 两个合集弹层的关闭键是纯图标，无 aria-label | `tiklocal/templates/flow.html:202-204`、`templates/library.html:308-310` | 已修 |
| 34 | P2 | 媒体信息弹层与 Quick Viewer 无 Tab 焦点陷阱 | `tiklocal/templates/flow.html:183`、`templates/library.html:232` | 转 backlog |
| 35 | P3 | 两个 `input[type=range]` 无可访问名（电台的同名控件有） | `tiklocal/templates/flow.html:43`、`templates/library.html:250` | 已修 |
| 36 | P3 | skip link 目标带 `aria-hidden`（与 #4 重复，按 P2 计） | — | 合并计 |
| 37 | P3 | `#quick-collection-modal` 无 role/aria-modal | `tiklocal/templates/library.html:302` | 已修 |
| 38 | P3 | live region 与内容同帧插入，首个提示被静默 | `tiklocal/static/ui_feedback.js:17-22` | 转 backlog |

域内已排除：i18n 完全对齐（中文 1086 键 = 英文 1086 键，模板与 JS 引用的 455 个键零缺失）；`base.js` 抽屉、`ui_feedback.confirm`、合集页、下载页的焦点陷阱都合规；全模板 `<img>` 100% 有 alt；无悬空的 `aria-labelledby`。

## 五、响应式与布局可达性（域 D5）

| # | 级别 | 问题 | 位置 | 处置 |
|---|------|------|------|------|
| 39 | P2 | 库页工具栏 `position: sticky; top: 0` 与固定移动页头（46px + 安全区）冲突：窄屏下滚约 74px 后首行视图切换被页头右侧导航触发器盖住，点它打开的是侧边导航 | `tiklocal/static/library.css:4-11`、`app_navigation.css:147-159` | 已修 |
| 40 | P2 | `body.tag-feed` 显示话题流顶栏（z 240）与固定页头（z 242）叠加，话题名与计数被遮成叠字 | `tiklocal/static/flow.css:1003-1004` | 已修 |
| 41 | P2 | 电台页 `height: 100dvh; overflow: hidden; align-content: center`，横屏手机（如 667×375）底部控制条被裁掉一半且整页不可滚 | `tiklocal/static/radio.css:36-39,48-60` | 转 backlog |
| 42 | P2 | Quick Viewer 的 5 个 46px 按钮在 ≤420px 高视口被 `overflow: hidden` 裁掉最上面的倍速键 | `tiklocal/static/library.css:503-516` | 转 backlog |
| 43 | P3 | `/favorite` 的底部补偿为 0，`.organize-bar` 固定悬浮压住最后一排卡片 | `tiklocal/static/library.css:1-3,1125-1141` | 转 backlog |

域内已排除：下载卡片与合集卡片的绝对定位菜单不被祖先裁切；搜索页隐藏了页头故无同类冲突；flow 的字幕/详情面板内部可滚；设置弹层是 `position: fixed`，不受 `.settings-card{overflow:hidden}` 影响。

## 六、跨页/跨会话持久化（域 D6）

| # | 级别 | 问题 | 位置 | 处置 |
|---|------|------|------|------|
| 44 | **P1** | 电台把「收听位置」与「最近播放」写进 localStorage（值是媒体库私有路径），全仓库没有任何清理点，`/logout` 只清 cookie → 同一浏览器换账号后，首页「继续收听」卡片显示上一个用户的媒体名，首次调台还会把这些名字拼进 `?exclude=` 发给服务端 | `tiklocal/static/radio_controller.js:880,892`、`tiklocal/auth.py:249-255`、`static/home_page_controller.js:205-209` | 已修 |
| 45 | P2 | CSRF token 在脚本加载时冻结进内存：另一个标签页重新登录后 token 轮转，本标签所有写请求 403 且只有 401 被统一处理 → 收藏/删除/进度静默失败直到手动刷新 | `tiklocal/static/csrf_fetch.js:4-5`、`static/base.js:157` | 转 backlog |
| 46 | P2 | 抖音模式开关只在加载时读一次 localStorage，无 `storage` 监听 → 另一标签切换后本标签显示与实际相反 | `tiklocal/static/settings_page_controller.js:451` | 已修 |
| 47 | P3 | 设置页写 `tiklocal:lang` 但全站无人读取（i18n 读的是 `<html lang>`） | `tiklocal/static/settings_page_controller.js:471` | 转 backlog |
| 48 | P3 | `ui_feedback.js` 的 `removeItem` 在 `JSON.parse` 之后，损坏值永久驻留 | `tiklocal/static/ui_feedback.js:83-85` | 转 backlog |
| 49 | P3 | `tiklocal:vt-from` 在守卫之前写入且从不删除，产生与操作无关的 hero 过渡 | `tiklocal/static/page_transitions.js:34` | 转 backlog |
| 50 | P3 | 语言切换 else 分支的 `setItem` 无 try，存储被禁时跳过主题按钮同步 | `tiklocal/static/settings_page_controller.js:80` | 转 backlog |

域内已排除：主题键三处读取都有 try + 枚举校验且已有 storage 同步；`flow_session.js` 不使用浏览器存储；`radio_position` 两侧格式一致且有 `saved.name === currentTrack.name` 守卫；`normalizeRoom` 会校验落盘值。

## 七、前后端契约一致性（域 D7）

| # | 级别 | 问题 | 位置 | 处置 |
|---|------|------|------|------|
| 51 | P2 | 图片详情页返回键写死 `history.back()`：新标签/书签/PWA 直达时 `history.length === 1`，点击毫无反应，用户被困当前页（视频详情页早已有回退分支） | `tiklocal/templates/image_detail.html:12` | 已修 |
| 52 | P3 | boot 数据注入 `libraryView` 但全仓库无 JS 读取，`\| default('explore')` 还会掩盖后端漏传 | `tiklocal/templates/library.html:344` | 已修 |
| 53 | P3 | `render_template` 传入 `tag`/`start`/`caption` 三个上下文键，`flow.html` 从未使用（前端直接读 URL 参数） | `tiklocal/web/flow.py:667-669` | 已修 |

域内已排除：4 处 boot 注入点与读取端逐键一致（`detail.html`↔`detail_page_controller.js`、`image_detail.html`↔`image_detail_page_controller.js`、`library.html`↔`library_page_controller.js`/`library_timeline_controller.js`）；空库与首次访问不崩（网格/时间线/重试按钮都在）；`empty_message` 的硬下标由函数参数驱动、不来自 `request.args`；裸字典与 `success/data` 两种响应形状都被正确归一化；5 处内联事件都能解析到全局符号。

## 八、修复取舍

- **6 条 P1 全部修完**（规则要求）：空格键双触发与按钮空格失效、网格卡片不可聚焦、图片详情页按钮被 `preventDefault` 吞掉、下载页「进行中」列表每 2.2 s 重建、关闭的合集弹层仍可 Tab 进入、换账号串上一个用户的收听记录。这 6 条都在最常见路径上（打开库页、看视频、点按钮、下载、登出换账号），且改动都 ≤ 20 行。
- **P2 修 10 条**，挑选标准是「用户会实际遇到 + 改动可验证」：三处「视觉上已收起、实际仍可聚焦/可激活」（倍速面板、tag 流页头叠加、库页工具栏遮挡）、四处「可访问名缺失或语义错误」（信息键、收藏键、抖音开关、两个关闭键）、一处跨标签不同步（抖音开关）、一处返回键死路（图片详情）、一处裸 `localStorage`（设置页）。
- **P3 修 8 条**，都是与上面同一文件、同一改动批次里的顺手项（range 可访问名、skip link 目标、quick 弹层语义、两处死键清理），单独提 PR 不值得，合在一起才符合「最小 diff」的反面——即不做无谓的分散提交。
- **转 backlog 27 条**，主要卡在：需要成体系的焦点陷阱工具（#12 #13 #21 #22 #34）、需要真机/多视口验证的布局（#41 #42 #43）、需要产品决策（#25 电台切换入口是否恢复）、以及跨标签 token 轮转需要刷新策略设计（#45）。

## 九、修复实现要点

1. **键盘守卫按角色放行**（`flow_page_controller.js`、`image_detail_page_controller.js`）：守卫从「排除输入控件」扩成「输入控件 + `button, a[href], [role="button"]`（图片详情另加 `summary, [role="menuitem"]`）直接 return」——**但只在空格（图片详情另加 Enter）时 return**：方向键不是按钮的键，点过一次按钮就再也切不了条是更糟的回归（详见 §十三）。这样全局快捷键只在「焦点在页面空白/媒体区」时生效，按钮恢复原生激活；侧栏收藏与合集的元素级处理器自己 `preventDefault()` 后 `click()`，因此补 `stopPropagation()` 才不会在同一次空格里既点赞又暂停。
2. **网格卡片获得键盘语义**（`library_page_controller.js::createTile`）：`tabIndex = 0` + `role='button'` + `aria-label = T('home.view_media', {name})`，并新增 keydown 把 Enter/Space 转成 `tile.click()`（`preventDefault()` 防止空格滚页）。因为点击处理器本身已处理选择模式，键盘路径自动继承多选行为。
3. **列表就地更新而不是重建**（`download_page_controller.js`）：新增 `activeKey(jobs)`（id/status/engine/url/file_count，**不含 progress_percent**）、`updateActiveNode(node, job)`（只写状态文字、`aria-valuenow`、进度条宽度）、`syncActiveList(container, jobs, force)`（key 未变就地更新，变了或 `force` 才整块重建）。进度仍然每 2.2 s 更新，但 DOM 节点不再被销毁，`aria-live` 只在状态变化时播报。
4. **CSS 用 `visibility` 关闭「不可见但可聚焦」**：`.zoom-options` 与 `.collection-modal` 都补 `visibility: hidden`，打开态补 `visible`，过渡加 `visibility 0.22s` 让关闭动画期间仍可见。这是 `library.css:769-777` 已经用过的同款修法。
5. **换账号清理本机残留**（`base.js`）：对 `form[action$="/logout"]` 绑 submit 清 `radio_position` / `radio_recent_tracks`（值里含媒体库私有文件名）。主题、语言、抖音模式是本机偏好，保留。
6. **存储访问全部包 try**（`settings_page_controller.js`）：抖音开关读取抽成 `storedDouyinMode()`（内部 try），写入包 try，并新增 `storage` 事件监听同步复选框；语言切换的写入同样包 try。
7. **同步请求加超时**（`settings_page_controller.js::postAuthor`）：`signal: AbortSignal.timeout(60000)`，与同文件其它请求（20 s / 30 s / 120 s）对齐，避免 `finally` 永不执行导致行按钮永久「同步中」。
8. **CSS 偏移只在页头真正渲染时生效**（`library.css`、`flow.css`）：`@media (max-width: 767px), (max-height: 599px)` 下把 `.library-toolbar-wrap` 的 `top` 改为 `calc(58px + var(--app-nav-safe-top))`；tag 流则把 `.douyin-topbar` 下移到 `calc(46px + var(--app-nav-safe-top))`（`height: 52px`、`padding-top: 0`），并限定 `:not(.douyin):not(.immersive-mode)`——抖音模式隐藏页头、沉浸模式淡出页头，都不需要偏移。**不要隐藏页头**：<768 px 时 dock 是隐藏的，页头里的菜单键是唯一导航入口（第一版就是这么错的，见 §十三）。
9. **模板语义补齐**（`flow.html`、`library.html`、`settings.html`）：`aria-pressed` 由 `flow_ui_shared.showUI()` 统一同步（所有调用点都经过它），收藏键从 `<a>` 补 `role="button"`，两个合集弹层补 `role="dialog"`/`aria-modal`/`aria-labelledby` 与关闭键名称，抖音开关补 `aria-label`。
10. **两处死代码清理**（`library.html`、`web/flow.py`）：删掉无人读取的 `libraryView` 注入键与三个未使用的模板上下文键；`library_view` 变量本身仍被模板的服务端分支使用，故只删注入行。

## 十、验证

**全量测试**（`\.\.venv\Scripts\python.exe -m pytest -q`）：**602 passed / 8 skipped / 0 failed**（213.71 s，复审修正后重跑；此前一轮为 202.51 s）。与起点基线（602P/8S/0F）逐项一致，未恶化；本轮未新增用例，原因见开头说明。

**真实浏览器冒烟**（Chromium 1280×900，`.venv` 起的本地服务，临时媒体库 3 张 640×960 JPEG，测完删除）：

| 页面 | 断言 | 结果 |
|------|------|------|
| `/flow` | `#collection-modal` 与 `.zoom-options` 的计算样式 `visibility` | 均为 `hidden`（关闭态不再可聚焦） |
| `/flow` | `#info-btn`/`#collection-btn` 可访问名、`#favorite-btn` 的 role 与 `aria-pressed`、放大镜 `aria-pressed`、进度条 `aria-label`、`#main` 的 `aria-hidden` | 全部符合预期（`aria-hidden` 已为 false） |
| `/flow` | 向按钮派发可取消的 `KeyboardEvent('keydown', {key: ' '})` 后读 `defaultPrevented` | 倍速键/收藏键为 `false`（原生空格激活恢复）；合集键为 `true`（元素级处理器自己 `preventDefault()` 后 `click()`，且不再冒泡到播放/暂停） |
| `/library?view=explore` | 卡片 `tabIndex`/`role`/`aria-label`，派发 Enter 后是否触发点击 | 3 张卡片均 `tabIndex=0`、`role=button`、名称为「查看 @default/trip/xxx.jpg」，Enter 触发点击 1 次 |
| `/library?view=explore` | 1280×900 下工具栏 `top` | `0px`（媒体查询未生效，符合「只在窄屏/矮屏偏移」的设计） |
| `/image?uri=…` | 返回键的 `onclick` 属性 | `history.length > 1 ? history.back() : (location.href = '/library')` |
| `/settings/` | 抖音开关的可访问名 | 取自新增的 `aria-label` |
| `/flow?tag=test`（375×812） | 页头与话题顶栏的 `getBoundingClientRect()`、菜单触发器可见性与点击结果 | 页头 `12–58 px`、顶栏 `58–110 px`，不重叠；触发器 42×42 可见，点击后 `aria-expanded=true` 且菜单展开 |
| `/flow?tag=test` | 向 `#info-btn`/`#speed-btn`/`#favorite-btn` 派发空格与 `#favorite-btn` 派发 `ArrowRight` 后读 `defaultPrevented` | 依次为 `false`/`false`/`true`/`false`（真按钮恢复原生激活、收藏键由元素级处理器接管、方向键不再被守卫吞掉） |
| 全部页面 | 控制台 error/warn | 无 |

**语法检查**：改动的 6 个 JS 文件逐个 `node --check` 通过。

## 十一、覆盖率缺口

1. **没有 JS 运行时测试**：本轮 24 条修复里 20 条是前端行为，`pytest` 只能证明页面仍能渲染。冒烟覆盖了 `/flow`、`/library?view=explore`、`/image`、`/settings`，但**没有覆盖**：空格键在真实键盘焦点下的完整链路（用的是合成事件）、下载页轮询 2.2 s 的实际表现（需要真实任务）、跨标签 `storage` 同步（需要两个标签）。
2. **媒体信息弹层与 Quick Viewer 的焦点陷阱未实现**（#34），故「Tab 不会跑出弹层」这条没有被验证过。
3. **响应式只验证了 1280×900 与 375×812 两个视口**：窄屏工具栏偏移（#39）、横屏电台（#41）、Quick Viewer 溢出（#42）都没有在真机或更多视口下验证；#39 只在 1280×900 下确认「不该生效时没生效」、在 375×812 下确认 tag 流顶栏让位，但没有验证横向 320 px 与横屏手机。
4. **`/favorite`、`/collection/<id>`、`/radio`、`/download` 页未逐个冒烟**：本轮改动没有触及它们的模板，但 #20 的下载页修复只在代码层验证。
5. **`aria-pressed` 的读屏播报**无法自动验证，只能确认属性值正确。

## 十二、转 backlog

本轮 27 条转 backlog（14 条 P2 + 13 条 P3），明细见 `docs/audit/backlog.md` 的「第 23 轮新增」段。优先级最高的三条：

1. **#45 CSRF token 跨标签冻结**：换标签登录后所有写操作静默 403，是真实用户会遇到的「点了没反应」。
2. **#41 电台横屏布局**：横屏手机完全看不到控制条，且整页不可滚。
3. **#22 七个 `aria-modal` 弹层没有焦点陷阱**：键盘用户 Tab 会跑到背景内容里，与 #12/#13/#21/#34 一起做成一个通用焦点陷阱工具最省事。

## 十三、独立复审

独立复审实例（只读 + 可跑测试，fresh context）逐条判定本轮 14 处改动：**10 条「正确」，3 条「有问题」（其中第 1 条同时含两个缺陷），1 条「部分正确」**，并新报 4 条缺陷——全部是**本轮修复自己引入或没修完**的，当轮修完。

| # | 级别 | 复审意见 | 处置 |
|---|------|----------|------|
| 1 | P2 | `flow_page_controller.js` 的键盘守卫对**所有按键**都 `return`：点过任意按钮后焦点留在按钮上，`←/→/↑/↓` 的 seek 与上/下条切换全部失效 | 守卫收窄为 `event.key === ' ' && target.closest(...)`——只有空格属于按钮，方向键仍然属于 feed |
| 2 | P2 | `#favorite-btn` 是 `<a role="button">`，没有元素级 keydown（`:2223` 那组只覆盖抖音镜像按钮）→ Tab 到点赞键按空格毫无反应 | 补「空格 → `preventDefault()` + `stopPropagation()` + `click()`」；**并把它移出 `if (douyinModeOn && ...)` 块**——第一次实现放在块内，非抖音模式下是死代码，浏览器实测 `defaultPrevented` 仍为 `false` 才发现 |
| 3 | P2 | `image_detail_page_controller.js` 的守卫同样过宽 → 点过收藏/缩放后 `←/→` 与 `f` 快捷键失效 | 加 `&& (e.key === ' ' \|\| e.key === 'Enter')`，与 `detail_page_controller.js:182` 的既有写法对齐 |
| 4 | P2 | `body.tag-feed .mobile-app-header { display: none; }` 误伤导航：<768 px 时 dock 本就隐藏，页头是唯一的菜单入口 | 改为不隐藏页头，把 `.douyin-topbar` 下移到页头下方：`top: calc(46px + var(--app-nav-safe-top))`、`height: 52px`、`padding-top: 0`，并限定 `@media (max-width: 767px)` 且 `:not(.douyin):not(.immersive-mode)` |

第 4 条复审建议「只隐藏标题/wordmark」或「把菜单触发器搬进顶栏」，最终选了第三种：顶栏是 `position: absolute; top: 0` 的 52 px 整条，只藏 wordmark 的话它仍会压住页头两侧的模式切换与菜单键（页头 z-index 242 高于顶栏 240，会出现控件叠在顶栏按钮上的错位）；下移整条才能让两行各归各位。抖音模式本来就把页头整个隐藏、沉浸模式把页头淡出，这两种情况都不需要偏移，故用 `:not()` 排除。

复审还确认了几件正面事实：`createTile` 的 keydown 有 `preventDefault()` 不会滚页、多选路径自动继承；`updateActiveNode` 的字段覆盖完整（queued 无进度条、running 必有数字）；`showUI()` 是所有调用点的唯一入口，`aria-pressed` 语义正确；`base.js` 的 logout 清理只匹配到 `base.html:90/119` 两个表单；`image_detail.html` 的 `&gt;` 由解析器还原为 `>`，与 `detail.html:12` 逐字一致；模板确实不再引用 `tag/start/caption`。

**复审后的验证**：4 处改完再跑 `node --check`（3 个 JS）全部通过；`pytest` 全量仍为 **602 passed / 8 skipped / 0 failed**（Python 侧本轮改动只有 `web/flow.py` 的上下文清理，未受影响）；移动视口（375×812）实测 `/flow?tag=test`：页头占 `12–58 px`、顶栏占 `58–110 px`、两者**不重叠**，菜单触发器 42×42 可见且点击后 `aria-expanded=true`、菜单展开；同一页面派发合成 `keydown` 后读 `defaultPrevented`：`#info-btn`/`#speed-btn` 的空格为 `false`（不再被文档级处理器取消）、`#favorite-btn` 的空格为 `true`（元素级处理器接管）、`#favorite-btn` 上的 `ArrowRight` 为 `false`（方向键不再被守卫吞掉）。
