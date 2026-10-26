# 第 03 轮审查报告

- 日期：2026-10-27
- 起点：`pytest -q` = **2 failed / 365 passed / 6 skipped**（第 02 轮结束时）
- 终点：`pytest -q` = **2 failed / 381 passed / 6 skipped**（新增 16 个用例）
- 轮次重点：并发与资源（前端状态机 + 缓存/媒体服务 + 测试覆盖 + 模板 + CLI/打包）
- 审查方式：5 个 fresh 只读 subagent，文件域不重叠；全部只读，探针只写 `%TEMP%` 与临时 instance，不碰仓库

## 域划分

| subagent | 域 | 结果 |
|----------|----|------|
| `ab656426` | 前端性能/交互（`static/*.js`、`base.html` 脚本装载） | P1×1 / P2×2 / P3×2 |
| `b527f424` | 测试质量与覆盖缺口（`tests/`） | P2×4 / P3×2 |
| `ef726ad2` | 模板/Jinja（`templates/`） | P2×2 / P3×2 |
| `9c382b57` | 媒体服务与缓存（`web/media.py`、`services/thumbnail.py`） | P1×1 / P2×2 / P3×1 |
| `75bb009d` | CLI/配置加载/打包（`run.py`、`config.py`、`pyproject.toml`） | P2×2 / P3×4 |

合计 25 条发现（P0 0 / P1 2 / P2 10 / P3 13），**修复 21，记录 4**。

## 发现与处置

### 前端性能域

| 级别 | 位置 | 问题 | 处置 |
|------|------|------|------|
| P1 | `static/library_timeline_controller.js:266-270` | `restorePosition()` 的 `while (!target && hasMore)` 无 guard；`loadMore()` 失败仍保留 `hasMore=true` → 无限重试风暴；`loadMore()` 同步早退时变成微任务饿死，标签页冻结。触发路径是主流程：月份详情页「返回时间线」链接（`library.html:35`）直接生成 `#month-YYYY-MM` | **修复**：改为 `for (guard<200)` + `if (!(await loadMore())) break`，与 `loadUntilVisible()` 同构 |
| P2 | `static/flow_page_controller.js:802,823-831` | 预载下一视频用 `preload='auto'` 整段拉流，滑走不取消 | 记录（`backlog.md`）：改 `preload='metadata'` 需真机验证首帧体验 |
| P2 | `templates/base.html:12-24` | `<head>` 内 8 个解析阻塞脚本 + 47 KB 不可缓存内联 i18n | 记录（`backlog.md`）：需前端构建拆分，收益低于改造面 |
| P3 | `static/download_page_controller.js:363,389,540` | 活动任务期每 2.2 s 重建两个列表 + 全文档 `feather.replace()` | 记录 |
| P3 | `static/library_page_controller.js:376-379` | 旋转/换列宽时瀑布流整表销毁重建 | 记录 |

### 测试质量域

| 级别 | 问题 | 处置 |
|------|------|------|
| P2 | 下载器取消/超时逻辑（第 02 轮新增）零测试 | **修复**：新增 `tests/test_downloader_lifecycle.py`（2 用例：取消赢过完成中的下载、超时杀掉引擎进程） |
| P2 | 第 02 轮输入边界加固（`MAX_CONTENT_LENGTH`、CSRF 来源、`json_body`）无测试 | **修复**：新增 `tests/test_request_boundaries.py`（6 用例） |
| P2 | `recommendation.py` 尾部（多样性截断）+ `metadata_for_many` 无测试 | **修复**：新增 `tests/test_recommendation.py`（3 用例） |
| P2 | 登录限流 429 无测试 | **修复**：`tests/test_auth.py` 增加用例；同时补 `Retry-After` 响应头（见下） |
| P3 | `/api/library/stats` 的 `favorites` 计数因 `load()` 返回 `set` 恒为 0 | **修复**：`web/settings.py:41-48` 接受 `set`，加测试 |
| P3 | i18n 键无「被模板使用但缺失」的守卫 | **修复**：`tests/test_i18n.py` 增加扫描用例（本轮借此发现并补 `image.override.*` 等键） |

### 模板域

| 级别 | 位置 | 问题 | 处置 |
|------|------|------|------|
| P2 | `templates/base.html:29` | 服务端从不注入 `theme`，深色模式首帧闪白，早读脚本拿到错误主题 | **修复**：`<head>` 内联脚本在首帧前解析 `localStorage.theme` + `prefers-color-scheme`，并给 `theme-color` 加 light/dark 双 `media` |
| P2 | `templates/settings.html:245` | 凭据上传 `<input type=file>` 无可访问名称 | **修复**：加 `aria-label="{{ t('settings.cred.upload') }}"` |
| P3 | `templates/base.html:99,105,111` | 导航分组标题 `Overview/Experience/Media` 硬编码英文 | **修复**：新增 `nav.group.overview/experience/media`（zh/en） |
| P3 | `templates/image_detail.html:191,195` | `temperature (0-2)` / `tags_limit (1-20)` 硬编码英文 | **修复**：新增 `image.override.temperature_label/tags_label` |

### 媒体服务与缓存域

| 级别 | 位置 | 问题 | 处置 |
|------|------|------|------|
| P1 | `web/media.py` `api_set_thumbnail()` | 选帧接口不传 `overwrite=True`：已有海报时 `generate_thumbnail()` 判定「已新鲜」直接返回旧图，用户选的帧被静默丢弃，响应却报成功 | **修复**：`generate_thumbnail(..., overwrite=True)`；并补 `find_existing_uri` 空值与 `is_file()` 校验（404/400） |
| P2 | `web/media.py` `thumb_view()` | 占位图（生成失败/无 uri）也带 `max-age=3600`，浏览器一小时不再重试；`app.py` 的 `after_request` 还会覆盖成 1 小时 | **修复**：新增 `_placeholder_response()` 统一 `private, no-store`；`app.py:202-209` 仅在响应未标 `no-store` 时补 1 小时缓存；真实缩略图加 `ETag`（`mtime_ns-size`） |
| P2 | `services/thumbnail.py` `_path_for_key()` | 缓存键只有相对 uri，不含媒体根：不同库同名文件互相串图，且 mtime 校验无法识别（拷贝保留旧 mtime） | **修复**：键改为 `sha1(root_key + '|' + canonical)`；新增 `_cached_path_for_key()` 兼容并迁移旧布局（`legacy.replace(canonical)`），`delete_thumbnail()` 同时清理两种布局 |
| P3 | `web/media.py` `thumb_view()` | 对 `request.args['uri']` 再 `unquote()` 一次（Werkzeug 已解码），`%2520` 类 uri 被二次解码 | **修复**：删除多余 `unquote` |

### CLI / 配置 / 打包域

| 级别 | 位置 | 问题 | 处置 |
|------|------|------|------|
| P2 | `run.py:235-243` | 端口只校验「是整数」：`70000`/`-1` 直达 `bind()` 抛 `OverflowError`（先打印 URL 再裸 traceback）；`--port 0` 被 falsy 吞成 8000 | **修复**：区分「未指定」与 0，校验 `1..65535`，越界走 `parser.error` |
| P2 | `run.py:42` | 配置解析成列表/纯量时 `config.get()` 抛裸 `AttributeError`，栈顶指向 host 行 | **修复**：`load_config()` 末尾校验映射，非映射打印明确错误并 `exit 2` |
| P3 | `run.py:232` | `host:` 留空（null/''）绕过默认值：null 让 waitress 抛 `ValueError`，'' 意外监听全部网卡；IPv6 打印 `http://::1:8000` 不可点 | **修复**：`config.get('host') or '0.0.0.0'`；打印时 IPv6 加方括号 |
| P3 | `config.py:5` | `--media-source` 整体替换配置里的 `media_sources`（README 说「追加」） | **修复**：按 id 合并，CLI 覆盖同 id |
| P3 | `paths.py:19`、`run.py` `finally` | 数据目录创建失败（`TIKLOCAL_INSTANCE` 指向文件/不可写）裸 traceback；Ctrl+C 时 `download_manager.close()` 抛 `RuntimeError` 覆盖正常退出 | **修复**：新增 `resolve_data_dir(parser)` 转 usage error；`finally` 捕获 `RuntimeError` 打印警告 |
| P3 | `tiklocal/__init__.py`、`app.py:85`、`pyproject.toml`、`package.json` | 版本三处不一致（空 `__init__` / 回退 `1.0.0` / 0.9.0 / 0.8.7） | **修复**：`__init__.py` 暴露 `__version__`（`importlib.metadata` → `pyproject.toml` 回退）；`app.py` 不再回退到不存在的 `1.0.0`；`package.json` 对齐 0.9.0 |

## 关键复现证据

- restorePosition：`loadMore()` 在 `hasMore=true` 且请求失败时返回后仍保留 `hasMore`，`while` 立即重试 → 请求风暴；若 `loadMore()` 因 `loadingMore` 早退则同步返回，`await` 变微任务，主线程永不空闲。
- 选帧覆盖：`thumbnail_service.generate_thumbnail(name, timestamp=...)` 内部先查 `cached_thumbnail()`，命中即返回旧文件，`timestamp` 从未使用。
- 占位缓存：`send_file(..., max_age=3600)` + `after_request` 无条件覆盖 `Cache-Control`，两层都把 `no-store` 冲掉。
- 缓存串图：`_path_for_key(canonical)` 只哈希 uri；`cached_thumbnail()` 的 mtime 比较对「拷贝过来的同 mtime 文件」失效。
- 端口越界：`socket.bind(('127.0.0.1', 70000))` → `OverflowError: bind(): port must be 0-65535.`；waitress 自身不做范围校验。

## 检索（C 阶段）

`web_search` 在本机不可用（缺 `DEEPSEEK_API_KEY`），改用 `web_fetch` 抓官方文档：

- Flask `send_file` / `Response.set_etag` / `MAX_CONTENT_LENGTH` 语义
- Jinja2 autoescape 与模板内联脚本的 `</script>` 转义边界
- Waitress `serve()` 对 host/port 的处理（确认不做范围校验）
- `importlib.metadata.version()` 在源码检出与 wheel 安装下的行为

## 验证（E 阶段）

| 检查 | 结果 |
|------|------|
| `python -m py_compile` 全部改动文件 | 通过 |
| `node --check` 全部改动 JS | 通过 |
| `pytest tests/test_request_boundaries.py tests/test_recommendation.py tests/test_thumbnails.py tests/test_auth.py tests/test_i18n.py -q` | 全绿（含新增 11 例） |
| 全量 `pytest -q` | **2 failed / 381 passed / 6 skipped**，失败仍为两个 Windows 权限位断言 |
| 起服务 + `probe_round03.py`（16 页） | 16/16 = 200；`/thumb?uri=<不存在>` 返回 `Cache-Control: private, no-store`；真实缩略图带 `ETag` 且 `304` 生效 |
| 选帧接口 | 同一视频二次选帧返回不同缩略图（`ETag` 变化），旧行为返回同一张 |
| 端口/配置 | `--port 70000` → `error: The server port must be between 1 and 65535`；列表型 config → `Error: ... must contain a mapping`；`--media-source extra=...` 与配置源合并为 3 个 |
| 版本 | `import tiklocal; tiklocal.__version__` → `0.9.0` |

## 记录未修（写入 backlog）

1. 预载下一视频 `preload='auto'` 整段拉流且不取消（`flow_page_controller.js:802,823-831`）。
2. `<head>` 内 8 个解析阻塞脚本 + 47 KB 内联 i18n（`base.html:12-24`）。
3. 下载页活动任务期每 2.2 s 全量重建（`download_page_controller.js:363,389,540`）。
4. 瀑布流旋转/换列宽整表重建（`library_page_controller.js:376-379`）。

## 备注

- `75bb009d` 起初超时未返回，本轮收尾时补交报告，故 CLI 域的 6 条并入本轮。
- 测试域报告指出的 `test_author_sync` 偶发失败，已在第 02 轮定位为跨进程共享 `.tmp-inst-*` instance 目录撞库，改为按 PID 隔离后全量稳定。
