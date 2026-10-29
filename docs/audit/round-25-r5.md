# 第 25 轮 R5 域：feed 与详情页前端

| ID | 级别 | 位置 | 一句话结论 |
|---|---|---|---|
| R5-1 | P2 | `tiklocal/static/library_page_controller.js:840` | Quick Viewer 图片直取原图，翻页无守卫无节流，按住方向键 30 req/s 打满线程池 |
| R5-2 | P2 | `tiklocal/templates/image_detail.html:163` | 隐藏全屏层 eager 拉原图，JS:51 又 `new Image` 二次请求常驻，24MP 峰值 200MB+ |
| R5-3 | P2 | `tiklocal/static/flow_ui_shared.js:108` | 放大镜关闭不清 `backgroundImage`，原图解码位图与 2.5× 光栅常驻，每换一张钉住一张大图 |

P3：2 条（详见对话）

## 已排除

- 相册 stage 子元素不随相册长度累积：`flow_page_controller.js:1431`/`:1438` 滑动结束移除 outgoing，`:1463-1468` 另有按 `data-group-generation` 的兜底清扫，stage 内最多 2 个 `.image-group-media`。
- 时间线页观察器不累积：`library_timeline_controller.js:331-360` 的 4 个 `IntersectionObserver` 只在模块初始化建一次，`revealObserver`/`imageObserver` 命中即 `unobserve`，`loadObserver` 只 observe 单个 sentinel。
- 域内 Set/Map 均有界：`collections_page_controller.js:18` 的 `collectionsById` 每次 render 先 `clear()` 再重建，`flow_session.js:20` 的 `keys` 随 `dropHead`/`reset` 同步删，`library_page_controller.js:991` 的 `selectedNames` 退出多选即清空。
- `playback_resume.js:7` 的 `tracked` Set 只增不减，但本域只有 `detail_page_controller.js:229`（单元素）与 `library_page_controller.js:822`（复用同一个 `quickVideo`）两处 `track()`，flow 不调用，实际 ≤2 项；`latest` Map 只存字符串，500 条约 50KB。
- 监听器与对象 URL 无增长面：`flow_page_controller.js:2219-2220` 两个 `MutationObserver` 启动时各建一次；`flow_ui_shared.js:305`/`:324` 的 `Hammer(lens)` 与全局 `resize` 每实例一份（全站 2 个 magnifier 实例）；全站 `static/*.js` 无 `createObjectURL`，本域 `setInterval` 为 0 处。
