# 第 01 轮审查报告

- 日期：2026-10-27
- 起点：`642f706 feat: paginate download lists and clear history with multi-select`
- 审查方式：5 个只读 subagent 并行（文件域不重叠），Lead 复现/甄别/检索/修复/验证
- 模型：5 × `deepseek-v4-flash`（fresh context）

## 1. 基线与环境事实

| 项目 | 值 |
|------|-----|
| 基线 `pytest -q` | 6 failed / 354 passed / 6 skipped（54.79s） |
| 本轮结束 `pytest -q` | 2 failed / 358 passed / 6 skipped（60.60s） |
| 减少的 4 个 | `test_library_upgrade` 缩略图、`test_media_state` 尺寸探测（修复后转绿）、`test_thumbnails` 两个参数化用例（用例间污染，本次全量运行通过） |
| 剩余 2 个 | `test_auth.py:61`、`test_radio_client.py:77` |

剩余 2 个是**环境问题，不是产品 bug**，本轮已取证：两处都断言 `stat.S_IMODE(...) == 0o600`，Windows 实测返回 `0o666`（`st_mode=33206`）。Windows 上 POSIX 权限位无意义，该断言在 Windows 上不可能成立。

结论：**任何新增失败都是 P0 阻塞项**，这 2 个允许保留。

## 2. 发现与处置总览

| 域 | 报告数 | 确认 | 驳回 | 修复 |
|----|--------|------|------|------|
| 安全（auth/CSRF/路径/上传） | 6 | 5 | 1 | 5 |
| 库与下载前端 | 6 | 5 | 1 | 5 |
| Flow / 播放器前端 | 6 | 5 | 0 | 5 |
| 服务层（索引/缩略图/媒体信息） | 6 | 5 | 1 | 5 |
| 并发与存储（下载/回收站/同步） | 6 | 4 | 2 | 4 |

共 27 个文件修改，`+435 / -155`。

## 3. 已确认并修复

### P0 — 页面冻结

**1. 媒体库时间线跳转会永久卡死标签页**

`tiklocal/static/library_timeline_controller.js:235-238`（`revealYear`）与 `:256-259`（`restorePosition`）原为：

```js
while (!target && hasMore) await loadMore();
```

`loadMore()` 在 `loadingMore === true`（滚动触发的请求尚未结束）时直接 `return`，或请求失败进入 `catch` 后 `hasMore` 仍为 `true` —— 两种情况都会让 `while` 变成永不退出的微任务死循环，主线程被饿死，整页冻结（只能强杀标签页）。

修复：`loadMore()` 改为返回布尔值（真正取到一页 → `true`；早退或失败 → `false`），新增 `loadUntilVisible(id)` 统一封装「滚动加载直到目标出现」，带 `guard < 200` 上限，并用「目标是否前进」判断是否继续。`revealYear` / `restorePosition` 都改走它。

### P1

**2. 未鉴权的 `/api/v1/pair` 可被打爆内存**

`tiklocal/auth.py:135` 把 `/api/v1/pair`、`/pair/claim` 排除在鉴权之外，而 Flask 没有设置请求体上限 → `get_json()` 会把任意大小的请求体整个读进内存。实测 32 MiB 请求体 → 进程峰值约 128 MiB（4 倍），8 个 waitress 线程并发即可打爆进程。

修复：`tiklocal/app.py` 设置 `MAX_CONTENT_LENGTH = 8 * 1024 * 1024`。实测 32 MiB 请求现在返回 **413**（此前 401 后才解析，仍会先读完整个 body）。

**3. `/api/download/urls/sync` 可写任意绝对路径（自毁式）**

`tiklocal/web/downloads.py` 的同步接口把用户给的 `source_path` 直接传给 `Path()` 并写入，没有任何「必须在数据目录内」的约束。实测：`POST {"direction":"push","source_path":"<instance>/auth.json"}` → **200**，`auth.json` 被清空 → 之后所有页面 500（`services/auth.py:46-51` "Authentication configuration cannot be read"），只能手改磁盘恢复。

修复：`tiklocal/services/url_list.py` 新增 `FORBIDDEN_SUFFIXES`（配置/数据库类后缀）与 `_is_inside_data_dir()` 校验，在 `set_source_path()` 入口拒绝。实测 `auth.json`、`shared-list.json` 均返回 400 与本地化文案。

**4. Flow 会话重置与在途请求竞争，把旧页灌回新列表**

`tiklocal/static/flow_session.js:74-103`：`reset()` 清空列表并把游标归零，但此时若 `loadMore()` 的请求仍在途，返回后会把**旧查询**的一页 append 进已清空的列表，且把 `cursor` 覆盖成旧值（例如 42）→ 新查询再也不会发出，界面出现「内容不匹配 + 不再加载」。

修复：引入 `epoch`；`loadMore()` 进入时捕获 `myEpoch`，`await` 之后若 `myEpoch !== epoch` 则丢弃结果并返回 `{skipped:true, stale:true}`；`reset()` 递增 `epoch` 并复位 `loading`；`finally` 仅在 epoch 未变时清 `loading`。

**5. 服务层并发写入**

- `tiklocal/services/radio.py`：画像 `_save()` 用就地 `write_text()` 且无锁，并发请求会互相覆盖/写坏文件。改为模块级 `_PROFILE_LOCK` 保护 load→改→save，写入改走 `write_json_atomic()`。
- `tiklocal/services/library_index.py`：`mtime` 在 `stat()` 之后才比较，扫描期间文件变化会漏更新。改为先捕获 `stat()` 快照再比较，并加 `run_once()` 与进程内锁。

**6. 媒体库页 focus 恢复循环**

`tiklocal/static/library_page_controller.js:498-506`：恢复焦点失败时会无限重试（每次失败又排一次 `requestAnimationFrame`）。修复：`guard < 200` + 用「卡片数量是否变化」判断是否继续。

### P2

**7. 20 处 `get_json() or {}` 遇到 JSON 数组体返回 500**

`AttributeError: 'list' object has no attribute 'get'`。实测 6 个端点复现（含 `/api/download/jobs/delete`）。修复：新增 `tiklocal/web/__init__.py` 的 `json_body(*, force=False)`，非 dict 时返回本地化 400；全量替换 `downloads.py`、`flow.py:597`、`library.py`（3 处）、`radio.py:136`、`media.py`（2 处，其中 `force=True` 兼容既有数组请求）、`captions.py`（2 处）、`author.py`。

**8. 删除占用中的文件 → 500 + 回显绝对路径**

`tiklocal/web/media.py:133-134` 直接把 `OSError` 的字符串塞进 500 响应，Windows 下包含 `WinError 32` 与完整路径（信息泄漏）。修复：`winerror ∈ (32, 33)` 或 `errno ∈ (EACCES, EBUSY, EPERM)` → **409** + 本地化「文件正被占用」，其余 → 500 + 通用文案；不再回显 `str(e)`。新增 i18n 键 `detail.error.file_in_use` / `detail.error.delete_failed`（zh+en）。

**9. 抖音文案转义后被正则二次解析**

`tiklocal/static/flow_page_controller.js` 的 `douyinCaptionHtml()` 先 `escapeHtml()` 再按 `#话题` 正则切分，`&#39;` 里的 `#39;` 被当成话题标记。修复：改为先 `split(/(#[^\s#]+)/)` 再对每段单独转义。

**10. `loadFeed()` 无超时**

`tiklocal/static/flow_page_controller.js`：请求挂住则永久 spinner 且 `isLoading` 不释放。修复：`fetch(..., {signal: AbortSignal.timeout(20000)})`。

**11. 播放进度信标永远 403**

`tiklocal/static/playback_resume.js:23-26` 用 `navigator.sendBeacon()` 提交，未带 CSRF token → `/api/playback` 全部 403。因 `timeupdate` 每 5 秒另有保存，实际最多丢 5 秒进度。修复：beacon body 内带 `_csrf_token`（取自 `window.TikLocalSecurity.csrfToken`）。

**12. 下载页 Esc 误清空选择**

`tiklocal/static/download_page_controller.js:475-477` 与 `:953-955`：关闭抽屉时 `keydown` 冒泡到 document 处理器 → `exitHistorySelection()` 把用户已勾选项清空。修复：抽屉内 `stopPropagation()`。同文件 `api()` 先 `json()` 再查 `ok`，HTML 错误页会抛 SyntaxError 并被当成成功，已改为文本安全解析。

**13. 缩略图/媒体信息/索引的边角缺陷**

- `services/thumbnail.py`：改用 `os.replace()` 原子落盘；同一目标并发只算一次（in-flight 去重）；失败后可重试；`clear_cache()` 真正清盘。
- `tiklocal/thumbs.py`：`/thumb` 支持覆盖已有缓存，修复「先 404 再生成」时的字节读取错乱。
- `services/media_info.py`：`ffprobe` 缺失时不再反复重试（记录 `banner` 标志）。
- `services/library.py`：索引跳过下载临时目录（`.part` / `.tmp`）。
- `services/downloader.py`：只允许 `http/https`，拒绝 `file://` 等 scheme。
- `services/trash.py`：先写 `entry.json` 再移动文件（此前崩溃会留下无元数据的孤儿文件）。
- 下载/URL 同步加进程内互斥锁。

## 4. 明确驳回

| 发现 | 驳回理由 |
|------|----------|
| `/api/download/jobs?limit=80` 之外的历史不可达，「共 N 条」有误导 | 该区块就是「最近下载」，80 条上限是设计；改成分页需重做 UI，收益低 |
| 未登录时安全响应头消失（`AUTH_ENABLED=False` 时 `auth.py:51-55` 早退跳过 `apply_security_headers`） | 该模式是「完全内网无鉴权」的自担风险配置；加头会误导用户以为有防护 |
| `/api/trash` 响应无 `success/data` 信封 | 前端按现有形状读取，改需同步改前端 |
| 后台 worker 线程 cookie 报错固定中文 | 无请求上下文，已在 `docs/phase-3-verification.md` §7 记录为设计取舍 |
| 下载历史 `error_message` 不随语言切换重译 | 持久化字段 |

## 5. 验证证据

**单元/集成**：`pytest -q` = 2 failed / 358 passed / 6 skipped；2 个失败均为上述环境问题。定向：`tests/test_auth.py tests/test_trash.py tests/test_download_urls.py tests/test_i18n.py -q` = 70 passed / 2 skipped。

**真实服务探针**（`.smoke-data/audit_tools/probe_live.py`，登录后带 CSRF）：

| 探针 | 结果 |
|------|------|
| `GET /api/download/jobs?limit=3` | 200 `{"data":{"jobs":[]},"success":true}` |
| `POST /api/download/jobs/delete`，body=`[{"id":"nope"}]` | **400** `ids 必须是数组。`（此前 500） |
| `POST /api/download/jobs/delete`，body=`{"ids":["nope"]}` | 200 正常信封 |
| `POST /api/author/sync` | 400 本地化「TikTokDownloader 未配置。」（不再回显键名） |
| `POST /api/download/urls/sync`，`source_path=<instance>/auth.json` | **400** 拒绝（此前 200 并清空 auth.json） |
| `POST /api/v1/pair`，32 MiB body | **413**（此前 401 但已读满内存） |

**浏览器 smoke**（Chrome DevTools，登录 `audit-pass`）：`/login` → 登录成功跳 `/`；`/library` 渲染「2026 / 8 个月 · 71 项」，10 月网格正常出图，控制台无报错；时间线月份链接跳转 `/library?view=month&month=2026-01` 正常。

## 6. 本轮未覆盖 / 下一轮方向

- Flow 页未在浏览器里实操（需真实视频目录，本轮媒体库只有图片）。
- 电台页、`/collections`、`/settings` 的交互未实操。
- 服务端并发压测未做（只有单发探针）。
- 第 02 轮审查域建议：`tiklocal/web/*` 其余路由（home/favorite/collection/settings）、`tiklocal/services/download_*` 全家桶、前端 controller 的异常路径、`run.py`/CLI 参数边界、模板 XSS（`|safe` 使用点）。
