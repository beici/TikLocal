# 第 11 轮 · 日志可观测性、可达性、鉴权与 i18n 复查

- 日期：2026-10-27
- 审查方式：5 个只读 subagent（日志轮转与可观测性、可达性复查、鉴权/会话、i18n 覆盖复查、详情页行为），每条要求「文件:行号 + 逐字代码 + 复现 + 影响」。
- 结论：**报告 20 条（P2×12 / P3×8）**。本轮修复批次写入后**工作区被回滚**（`git status` 只剩第 10 轮改动），因此按「复核一条、落地一条、立即验证」重做，最终确认并修复 **4 条**，复核后判为「本来就没问题」**3 条**，其余 **13 条**转第 12 轮重新取证（见末节）。

## 一、本轮已落地（4 条，逐条 `git diff` + 运行验证）

| # | 级别 | 问题 | 修复 |
| --- | --- | --- | --- |
| 1 | P2 | 退出登录只 `session.clear()`，浏览器里的 `session` cookie 原样保留：同一个旧 sid 会被继续发送，服务端每次都要重建会话 | `tiklocal/auth.py` `logout_view()` 改为 `response = redirect(...)` + `response.delete_cookie(SESSION_COOKIE_NAME or 'session')` |
| 2 | P2 | 日志没有脱敏：provider 报错会把 `?api_key=...` 连同 URL 一起写进 stderr 和 `tiklocal.log`（实例目录里的文件还会被备份 3 份） | `tiklocal/logging_setup.py` 新增 `RedactFilter`（`token/password/secret/api_key/cookie/authorization=...` 与 `Bearer xxx` 两类正则），同时挂到 stream 与 rotating file 两个 handler |
| 3 | P2 | 库页快速查看器是全屏大图，但 `quickImage` 从未设置 `alt`，读屏只能读出「图片」 | `tiklocal/static/library_page_controller.js` 在设置 `src` 处同步 `quickImage.alt = item.name`（网格缩略图保持 `alt=""`，因为卡片本身有标题，属于装饰图） |
| 4 | P3 | 电台页只有鼠标可用：空格不能播放/暂停，方向键不能快进快退 | `tiklocal/static/radio_controller.js` 的 document keydown 增加：Space → `togglePlay()`，←/→ → ±5 s seek（输入框/可编辑区域内不拦截） |

## 二、复核后判为「本来就没问题」（3 条）

- 导航当前项已有 `aria-current="page"`（`base.html` 的 dock 与移动端 mode 链接都是条件输出）。
- `<html lang>` 已经是 `{{ locale or 'zh' }}`，不是写死的 `en`。
- 登录跳转已有 `safe_next()`（`tiklocal/auth.py:79` 用 `urlsplit` 拒绝绝对/协议相对地址），不存在开放重定向。

## 三、转第 12 轮重新取证（13 条）

报告原文在会话压缩中丢失，为避免按记忆「修」出不存在的缺陷，这些条目交回下一轮用同样的取证标准重做：

1. 日志：启动日志级别、文件 handler 的轮转边界（已在上表第 2 条顺带补齐 formatter）、后台 worker 异常是否带 `exc_info`。
2. 可达性：设置页各分组的焦点顺序、对话框关闭后的焦点归还、表格/列表的空态播报。
3. 鉴权：`/api/v1/*` 之外是否需要公开的版本/健康检查端点；会话过期时前端提示与重定向的一致性。
4. i18n：设置页「同步路径」、下载/字幕接口错误文案、JS 里可能残留的硬编码中文。
5. 详情页：重新导航后撤销栈、跨标签页同步、工具栏按钮宽度、视频 `thumb_url` 是否等于 `media_url`。

## 四、验证

- `node --check`：`radio_controller.js`、`library_page_controller.js` 通过。
- `py_compile`：`tiklocal/auth.py`、`tiklocal/logging_setup.py` 通过。
- `pytest tests/test_auth.py tests/test_i18n.py -q` = **24 passed**。
- 日志脱敏实测：`api_key=sk-live-...` → `api_key=***`，`Authorization: Bearer abc...` → `Authorization: ***`，普通日志不受影响。

## 五、流程改进（针对本轮事故）

- 每批修复后**立即** `git diff --stat` 复核落地情况，再继续下一批；不再连续改多个文件后统一验证。
- 每轮结束前必须 `git commit`，让工作区状态可恢复。
