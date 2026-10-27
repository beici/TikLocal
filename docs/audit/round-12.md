# 第 12 轮：日志可观测性、键盘可达性与第 11 轮遗留项重新落地

- 起点基线：`pytest -q` = **432 passed / 6 skipped / 0 failed**（第 11 轮收尾）
- 终点基线：`pytest -q` = **439 passed / 6 skipped / 0 failed**（183.41s，新增 7 个用例）
- 本轮确认：**22 条**（P1×2 / P2×13 / P3×7）——其中 10 条来自本轮新审查（可达性 4 + 日志 6），12 条是第 11 轮报告里因工作区回滚而丢失的条目（i18n 5 / 鉴权 2 / 详情页与主题 5），本轮重新取证后落地。
- 驳回：0 条。

第 11 轮结束时我在 `docs/audit/backlog.md` 记了「13 条待重新取证」，本轮把它们逐条对回代码：12 条成立并修复，1 条（`AUTH_ENABLED=False` 时的安全响应头）维持原驳回。

---

## 一、日志与可观测性（6 条）

### 1. [P1] 后台探测线程每次唤醒都 `NameError`，图片尺寸永远探不出来

- 位置：`tiklocal/services/library_index.py:1018`（`MediaProbeWorker._run_budgeted_pass`）
- 代码：`deadline = time.monotonic() + budget`，但文件头只有 `datetime / logging / random / threading`，**没有 `import time`**。
- 复现：`create_app({'TESTING': True, 'MEDIA_ROOT': media})` 后调用 `app.extensions['media_probe'].run_once(budget_sec=5.0)` → `NameError: name 'time' is not defined`。
- 影响：`run_once()` 默认 `budget_sec=None` 走另一条分支，所以测试全绿；但生产里 worker 的每一次后台唤醒都走预算分支，异常被线程入口吞掉 → 库页瓦片永远没有宽高，视频按 16:9 强行裁切、竖图被拉扁，且日志里只有一条没有上下文的线程堆栈。
- 修复：补 `import time`（`library_index.py:8`）；新增用例 `test_probe_pass_accepts_a_time_budget` 直接调 `run_once(budget_sec=5.0)`。

### 2. [P1] 下载工具输出按 cp936 解码，一个对勾就能让任务「成功但零产物」

- 位置：`tiklocal/services/downloader.py:1365`（`_start_process`）
- 代码：`sp.Popen(cmd, stdout=sp.PIPE, stderr=sp.STDOUT, text=True, bufsize=1, ...)`——`text=True` 但没给 `encoding`，中文 Windows 上按 `locale.getpreferredencoding()`（cp936）解码。
- 复现：`DownloadManager._run_tool('job', [sys.executable, '-c', 'print("✓ 中文")'], lines.append)`，工具以 UTF-8 写出 `✓`（`0xE2 0x9C 0x93`）→ 泵线程抛 `UnicodeDecodeError` 死亡，`lines` 为空、进程返回 0。
- 影响：yt-dlp / gallery-dl 的输出里带 ✓、emoji 或非 GBK 字符是常态。泵线程死后 `_execute_download` 仍按 `returncode == 0` 判成功，`output_files_rel` 只能靠 `before/after` 目录快照兜底；一旦文件名也不在快照窗口内（例如工具自己改名/移动），任务就显示「已完成」但索引里没有文件——这正是第 10 轮「下载成功但库里看不到」的根因之一。
- 修复：`encoding='utf-8', errors='replace'`；泵线程 `except Exception: LOGGER.exception('Download output pump failed for job %s', job_id)`（`downloader.py:1284-1287`），保证线程不会静默死亡。新增用例 `test_download_output_survives_non_gbk_bytes`。

### 3. [P2] 脱敏只覆盖 `record.getMessage()`，traceback 与 dict 参数原样落盘

- 位置：`tiklocal/logging_setup.py:38-52`（原 `RedactFilter.filter`）
- 代码：过滤器只对 `record.getMessage()` 做正则替换；`logger.exception()` 的 traceback、`logger.info('...%s', {'url': ...})` 的 dict 都不经过它。
- 复现：`logger.exception('download failed')` 的异常文本含 `https://api.example.com/v1?token=abc&api_key=AKIA...` → 日志文件里逐字出现。
- 影响：`services/captions.py` 的 provider 错误、`downloader.py` 的请求 URL 都可能带 key；日志文件在 `data_dir` 下，是用户会随手发出来的东西。
- 修复：抽出 `redact_text()`，新增 `RedactingFormatter.format()` 对**最终渲染行**（含 traceback）脱敏，handler 用它；`RedactFilter` 保留（结构化字段场景）。同时把敏感值匹配从 `{4,}` 改为 `+`：三字符的 `?t=abc` 之前会漏。新增用例 `test_traceback_and_short_credentials_are_redacted`。

### 4. [P2] 日志文件写在 `app.instance_path`，wheel 安装时静默消失

- 位置：`tiklocal/app.py:175-178`
- 代码：`configure_logging(app.instance_path)`。
- 复现：`pip install tiklocal` 后 `instance_path` 在 site-packages 内（只读）；`configure_logging` 的 `except OSError` 只打一条 warning，之后**没有任何文件日志**。
- 影响：用户按文档去 `instance/tiklocal.log` 找日志时文件不存在，故障无法自查；而 `TIKLOCAL_INSTANCE` / `~/.tiklocal` 才是数据目录。
- 修复：`configure_logging(get_data_dir())`（`from tiklocal.paths import get_data_dir`）。新增用例 `test_log_file_is_created_under_the_data_dir` 校验文件落在传入目录、且内容已脱敏。

### 5. [P2] 探测失败也写入 `probed_mtime`，坏文件永远不再重试

- 位置：`tiklocal/services/library_index.py:1041-1051`（`_run_budgeted_pass` 的写回循环）
- 代码：`probe_media_dims()` 返回 `(None, None)` 时，结果仍被 `results.append((None, None, mtime, uri))` 写回，等于「这个 mtime 已处理过」。
- 复现：放一个损坏的 `broken.jpg`，`run_once()` 之后 `unprobed()` 里再也看不到它；修好图片（或 ffprobe 恢复可用）后仍不会重试，除非文件 mtime 变化。
- 影响：一次瞬时失败（ffprobe 超时、文件正被写入）会让该文件永久没有宽高，用户看到的瓦片是占位比例，且没有任何提示。
- 修复：探测失败时把 uri 放进 `deferred` 集合（不写 mtime），并 `LOGGER.warning('Could not read media dimensions for %s; will retry')`，每个 uri 只告警一次（`self._warned`，上限 500 条后清空）。`tests/test_media_state.py` 的期望同步更新：只有真正探测成功的文件计入完成数，坏文件保持 unprobed。

### 6. [P3] 500 日志只有路径，分不清 GET/POST 也无法复现

- 位置：`tiklocal/app.py:461-470`（`handle_server_error`）
- 代码：`app.logger.exception('Unhandled error on %s', request.path)`
- 影响：同一条路径上 GET 与 POST、首次请求与重试的日志完全一样；用户报「点下载报错」时无法从日志判断是哪个动作。
- 修复：`'Unhandled error on %s %s from %s'`，带 `request.method`、带查询串的 `request.full_path`、`request.remote_addr`。

### 7. [P3] 启动信息只在终端横幅里，日志里查不到本次运行的配置

- 位置：`tiklocal/run.py:399-411`（`serve` 命令，`create_app` 之后）
- 代码：横幅 `print` 到 stdout；作为服务运行（nssm / systemd / Windows 服务）时 stdout 常被丢弃。
- 影响：事后排查「这份日志对应哪个 media_root、哪个端口、有没有开鉴权」只能靠猜。
- 修复：`app.logger.info('TikLocal %s on %s:%s; media_root=%s sources=%s data_dir=%s auth=%s', ...)`，一行写全版本、监听地址、媒体根、源列表、数据目录与鉴权状态。

---

## 二、键盘与焦点可达性（4 条）

### 8. [P2] 电台页全局 Space 抢走按钮的激活键

- 位置：`tiklocal/static/radio_controller.js:233-240`
- 代码：守卫只排除 `input, textarea, select, [contenteditable]`；`button` / `a[href]` 不在其中，而 Space 正是按钮的标准激活键。
- 复现：`/radio` 上 Tab 到 `#btn-fav` 按 Space → 收藏没触发，播放反而暂停/继续。
- 影响：键盘用户无法用 Space 操作任何按钮（底部 dock、播放控制、收藏），只能改用 Enter，且每次误触都改变播放状态。
- 修复：守卫列表补 `button, a[href], summary, [role="button"]`。

### 9. [P2] 浮层打开时方向键与 Space 仍穿透到下面的卡片

- 位置：`tiklocal/static/flow_page_controller.js:1988-1994`
- 代码：全局 `keydown` 先处理方向键/空格，再判断焦点目标；合集面板或全屏文案浮层打开时没有早退。
- 复现：`/flow` 打开合集面板，按 ↓ 或 Space → 面板没滚动，背后的视频切到下一条。
- 影响：浮层期间的所有键盘操作都在改看不见的内容，返回后发现自己已经不在原来那条。
- 修复：`if (collections.isOpen() || douyinCaptionFull?.classList.contains('is-open')) return;`。

### 10. [P2] 下载页选择器是模态，但 Tab 会跑出面板、关闭后焦点丢到 body

- 位置：`tiklocal/static/download_page_controller.js:467-510`
- 代码：面板设了 `aria-modal="true"`，只处理 Esc；没有 Tab 循环，关闭时也没有把焦点还给触发按钮。
- 复现：打开「选择来源」面板 → Tab 几次后焦点落到面板后面的网格；Esc 关闭后 `document.activeElement === body`。
- 影响：屏幕阅读器用户会读到被遮住的内容；键盘用户关闭面板后要从头 Tab 一遍才能回到原按钮。
- 修复：记录 `previousFocus` 并在 `finish()` 时 `focus()`；`keydown` 里对 Tab 做首尾循环（过滤不可见元素）。

### 11. [P3] 快速查看层没有 tabindex，焦点留在卡片上，Tab 走进背后的网格

- 位置：`tiklocal/static/library_page_controller.js:849-862`（打开）、`780-790`（关闭）、`1269-1277`（新增守卫）
- 代码：`#quick-view` 只有 `aria-hidden`，没有 `tabindex="-1"`，打开时不 `focus()`，也没有焦点陷阱。
- 复现：点开图片 → `document.activeElement` 仍是那个 `button.timeline-tile`；Tab 一次焦点进入网格里被遮住的卡片。
- 影响：键盘/读屏用户在查看大图时实际在操作看不见的网格；关闭后焦点也不回到原卡片。
- 修复：`tabindex="-1"` + `quickView.focus({ preventScroll: true })`；关闭时把焦点还给 `viewerPreviousFocus`（若仍 connected）；`focusin` 守卫把逃逸的焦点拉回层内。

---

## 三、第 11 轮遗留项（本轮重新取证并落地）

### i18n

- **电台配对 4 条英文**：`tiklocal/radio_client.py` 的 `too_many_attempts` / `pairing_not_required` / `invalid_server_url` / `invalid_pairing_grant` 原为硬编码英文，改为 `t('radio.pairing.*')`（新增 zh/en 各 4 键，`i18n_pages_radio.py:47-50`、`125-128`）。
- **字幕/识图 2 条英文**：`tiklocal/web/captions.py:20`、`:54` 的英文 503 文案改为 `image.error.config_save_failed` / `image.error.metadata_read_failed`（`i18n_pages_media.py:125-127`、`372-374`）。
- **TikTok 未配置文案**：`tiklocal/web/downloads.py:228-230` 与 `services/tiktok_downloader.py` 统一用 `download.alert.tiktok_unconfigured`。
- **作者计数单位错位**：英文界面把 12345 显示成「1.2K」——单位是千、除数却是万。`tiklocal/web/author.py:73-82` 与 `static/author_page_controller.js:86-93` 改为按 locale 分支：中文 `1.2万`，英文 `12.3K / 1.2M / 1.2B`。

### 鉴权

- **CSRF 失败返回裸字符串**：`tiklocal/auth.py:110-116` 由 `return t('auth.error.csrf'), 403` 改为渲染 `error.html`（带「首页 / 媒体库」出口）。原实现会让用户在第二个标签页登录后点「退出登录」时看到一个 `<html lang="en">` 的纯文本 403，而 Cookie 仍然有效 30 天。
- **`/api/v1/server` 需要设备令牌**：该端点只返回名字、版本与 `auth_required`，客户端必须先读它才能配对；加进白名单（`auth.py:137-142`）。新增用例 `test_server_info_is_reachable_before_pairing`。

### 详情页与主题

- **删除没有二次确认**：`detail_page_controller.js:262-274` 与 `image_detail_page_controller.js:410-419` 原先点红菜单项立即删除；改为 `TikLocalUI.confirm`，标题带文件名（`detail.delete_confirm_title` / `detail.delete_confirm_copy`，zh/en 各一对）。
- **返回按钮在无历史时无反应**：`templates/detail.html:11-13` 改为 `history.length > 1 ? history.back() : (location.href = '/library')`（从书签/新标签直接打开详情页时）。
- **收藏状态跨标签页不同步**：接口是 toggle 而非 set，缓存值会让「另一个页面刚收藏过」的那次点击变成取消。`flow_media_actions_controller.js:31-39` 在 `pageshow` 与 `visibilitychange` 时清空 `favoriteState`。
- **收藏失败只报通用错误**：`detail_page_controller.js:94-107`、`radio_controller.js:428-444` 现在把服务端 `error`（例如 CSRF 过期提示）透出，而不是固定文案。
- **窄屏详情工具栏被裁掉**：`static/media_detail.css:68-73` 加 `flex-wrap: wrap`——6 个 40px 按钮 + 间距需要 336px，320px（iPhone SE）与 280px（Fold 外屏）会裁掉最后一个。
- **主题切换只改 `<body>`**：`static/base.js:33-37` 同时写 `document.documentElement.dataset.theme`，否则首屏脚本写在 `<html>` 上的属性与 `base.js` 写的 `<body>` 打架，页面外壳（所有从 `html` 解析的 `var()`）停在旧主题。
- **提示条互相顶掉**：`static/ui_feedback.js:43-53` 原 `wrap.replaceChildren(el)` 会删掉还在倒计时的「撤销」提示；改为信息类 toast 追加、只清理无 action 的旧条，撤销条保留。

---

## 四、验证

- `pytest -q`：**439 passed / 6 skipped / 0 failed**（183.41s）。
- 新增 `tests/test_round12_regressions.py`（7 个用例）：预算分支探测、非 GBK 输出泵、traceback/短令牌脱敏、日志落数据目录、`/api/v1/server` 免鉴权、CSRF 失败渲染页面、英文计数单位。
- 同步修正 2 个既有用例的期望：`test_media_state.py`（探测失败不再计入完成）、`test_tiktok_downloader.py`（文案改走 `t()`）。
- 浏览器实测（127.0.0.1:8000，`.smoke-run` 实例，鉴权开启）：`/nope-404` 渲染中文错误页（「404 请求的资源不存在。」+ 首页/媒体库）；`/api/v1/server` 未登录返回 `{"auth_required": true, ...}`；`<html>` 的 `color-scheme` 与 `data-theme` 一致（dark）。
- 语法检查：7 个 JS `node --check` 通过；12 个 Python 文件 `py_compile` 通过。

## 五、转 backlog

| 条目 | 理由 |
|------|------|
| 启动日志级别可配（`--log-level`） | `configure_logging` 固定 `INFO`；需求要先有「谁会改」的场景，否则只是多一个开关 |
| 空态/加载态的 `aria-live` | 需要逐页设计播报文案，避免朗读风暴，属体验设计而非缺陷 |
| 会话过期时各页 401 的统一跳转 | 现在由 `base.js` 的 fetch 补丁兜底，逐页行为差异未造成实际故障 |
