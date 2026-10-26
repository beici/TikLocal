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

## 待确认（推断成立但未复现）

| 问题 | 位置 | 待验证点 |
|------|------|----------|
| `sendBeacon` 在 iOS Safari 上可能因 body 类型限制被丢弃 | `tiklocal/static/playback_resume.js` | 需真机验证；即便丢弃也只丢最后一次进度（每 5 秒另有 `timeupdate` 保存） |

## 明确驳回（不再重复上报）

| 问题 | 驳回理由 |
|------|----------|
| `DELETE /api/trash` 在两个 media source 指向同一目录时 500 | 边角配置；修它会动回收站存储格式，风险高于收益 |
| `/api/trash` 响应无 `success/data` 信封 | 前端按现有形状读取；改需同时改前端，收益低 |
| 后台 worker 线程 cookie 报错固定中文 | 无请求上下文，已在 `docs/phase-3-verification.md` §7 记录为设计取舍 |
| 下载历史 `error_message` 不随语言切换重译 | 持久化字段，同上 |
| 2 个既有失败测试（`test_auth.py:61`、`test_radio_client.py:77`） | 断言 POSIX 权限位 `0o600`，Windows 返回 `0o666`，平台差异 |
| `/api/download/jobs?limit=80` 之外历史不可达 | 「最近下载」区块的设计上限，改分页需重做 UI |
| `AUTH_ENABLED=False` 时不下发安全响应头 | 该模式是自担风险的完全内网配置，加头反而误导 |
