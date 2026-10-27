# 第 13 轮：下载管道编码回退、大库查询计划与 API 契约收口

- 起点基线：`pytest -q` = **439 passed / 6 skipped / 0 failed**（第 12 轮收尾）
- 终点基线：`pytest -q` = **448 passed / 6 skipped / 0 failed**（167.03s，新增 9 个用例）
- 本轮确认：**27 条**（P1×2 / P2×5 / P3×20）。修复 **14 条**，转入 backlog **13 条**，驳回 0 条。
- 本轮特点：5 份只读报告里有 4 份属于「上一轮修复自己引入的回退」与「契约细节」，没有新的架构级缺陷。

本轮最严重的一条（§一.1）是**我上一轮亲手引入的回退**：第 12 轮为了修「泵线程被解码错误打死」，给子进程硬编码了 `encoding='utf-8'`。它在英文系统上正确，在中文 Windows 上让 yt-dlp 的中文路径输出变成替换符，任务变成「成功但零产物」——比原来的 bug 更隐蔽。教训：修解码问题必须以**子进程实际使用的编码**为准（`locale.getpreferredencoding()`），不能以「我们想要的编码」为准。

---

## 一、下载任务生命周期（4 条）

### 1. [P1] 硬编码 `encoding='utf-8'` 让非 ASCII 媒体根下的任务「成功但零产物」

- 位置：`tiklocal/services/downloader.py:1365-1369`（`_start_process`）
- 代码：`sp.Popen(cmd, stdout=sp.PIPE, stderr=sp.STDOUT, text=True, encoding='utf-8', errors='replace', bufsize=1, ...)`
- 复现（媒体根 `...\媒体库`，yt-dlp 真实任务）：
  - 管道原始字节按 utf-8 解码出现 6 个替换符 `\ufffd`，`[MoveFiles]` 行里的目标路径变成 `\ufffd\ufffd\ufffd\...`，`Path(...).exists()` 为 `False`；
  - 同一段字节按 cp936 解码 0 个替换符、路径正确；
  - 端到端：ASCII 根 `status=success, file_count=1`；中文根 **`status=success, file_count=0, output_files_rel=[], error_message=''`**，而磁盘上 `.mp4` 与 `.info.json` 都在（`_to_media_relative` 对不存在的路径静默返回空）。
- 影响：中文/日文媒体目录（本项目的常见用法）下所有 yt-dlp 下载都「完成但入库失败」，前端显示「已完成」且没有错误，用户只能自己去文件管理器找文件。
- 修复：去掉 `encoding='utf-8'`，保留 `errors='replace'`，让管道解码与子进程的 locale 一致；`test_download_output_survives_locale_encoded_bytes` 用 `locale.getpreferredencoding(False)` 构造子进程输出，并混入一个非法字节验证泵线程不再死亡。

### 2. [P3] 重复/重试任务共享产物时，删除一条会把另一条仍在用的文件搬进回收站

- 位置：`tiklocal/web/downloads.py:349-358`
- 复现：两条 job 的 `output_files_rel` 都指向同一个 `@default/x.mp4`（重试或重复导入），删除其中一条 → 文件进回收站，另一条详情链接 404。
- 修复：删除前收集其余 job 的 `output_files_rel`，命中则跳过（注释说明共享产物的来源）。

### 3. [P3] 取消落在「工具已退出、`on_outputs` 执行中」窗口 → `canceled` 却 `file_count=1`（backlog）

- 位置：`tiklocal/services/downloader.py:938-941` → `:965-968` → `:973-979`
- 未修原因：需要把「产物登记」与「取消状态写入」放进同一个事务；改动会牵动 job 状态机，而实际影响只是一条任务的状态文字不准确。

### 4. [P3] 中途取消在库根留下 `.info.json` 孤儿（backlog）

- 位置：`tiklocal/services/download_tools.py:116`（`--write-info-json`）+ `downloader.py:1072-1073`（清理只覆盖 temp 目录）
- 未修原因：需要按 job 记录「本次产生的 info 文件」并在取消时回收；收益低（一个几十 KB 的 json）。

---

## 二、API 契约（5 条）

### 1. [P2] 错误分支原样回显服务层英文 `str(exc)`，中文界面弹出英文

- 位置：`tiklocal/web/downloads.py:128`、`:160`、`tiklocal/web/settings.py:26`
- 复现：未配置服务器路径时点「从服务器文件导入」→ 中文界面 toast 显示 `No server path configured.`；`/api/cache/clear` 失败时 500 回显服务器绝对路径。
- 修复：`downloads.py` 新增 `_URL_LIST_ERROR_KEYS` 映射表 + `url_list_error_message()`，把 `UrlListStore` 的英文异常按关键字映射到 `download.urls.error_*`（10 个键，zh/en 各一套），未知异常回落到 `download.urls.error`；同时 `app.logger.warning` 保留原始文本。`settings.py` 改为 `t('settings.cache.failed')` + `app.logger.exception`。

### 2. [P3] `str()` 强转让 `null`/`[]`/`{...}` 变成名字为 `"None"` 的合集

- 位置：`tiklocal/web/library.py:421-422`
- 复现：`POST /api/collections {"name": null}` → 200 且落库为 `"None"`。
- 修复：非 `str` 且非 `None` 的 `name`/`description` 返回 400 `library.error.name_required`；`None` 归一为空串。

### 3. [P3] 405 丢 `Allow` 头

- 位置：`tiklocal/app.py:456-458`
- 复现：`POST /api/library/items` → 405 但无 `Allow`；`OPTIONS` 有。
- 修复：在 405 handler 里重建响应后补 `Allow: ', '.join(error.valid_methods)`。

### 4. [P3] 字面量端点被 `<job_id>`/`<collection_id>` 遮蔽（backlog）

- 位置：`tiklocal/web/downloads.py:273`、`:287`、`tiklocal/web/library.py:434`
- 复现：`GET /api/download/jobs/clear` 返回 404（被 `<job_id>` 吞掉）而非 405；`DELETE /api/collections/by-media` 同理。
- 未修原因：需要调整路由顺序或改用 `uuid` 校验器，牵动前端调用；当前无真实调用方。

### 5. [P3] 导出响应头出现两个 `charset=utf-8`

- 位置：`tiklocal/web/downloads.py:138`
- 修复：`mimetype='text/plain; charset=utf-8'` → `mimetype='text/plain'`（Flask 会补一次）。

---

## 三、数据一致性（5 条）

### 1. [P2] 探测队列队首全失败 → 整库饥饿，每轮重烧 ffprobe

- 位置：`tiklocal/services/library_index.py:1054-1055`（`if not results: break`）
- 复现：无 ffprobe 机器上 65 行 × 3 轮 `done=0`，JPEG 宽高永为 NULL；有 ffprobe 但队首 50 个坏文件时每轮 5-6 s 空转，`good.jpg` 永远探不到；删掉坏文件后 0.02 s 就探到 `(640,480)`。
- 修复：失败行记入 `_failed` 集合并按 `mtime` 失效（文件没变就不再排队），本轮跳过它们继续取后面的行；`test_probe_pass_steps_over_rows_that_keep_failing` 覆盖。

### 2. [P3] 多源指向同一目录 → 全部归第一个源（backlog）

- 位置：`tiklocal/web/library.py:202`、`:210`（`_resolved_roots` 取首个匹配）
- 未修原因：需要给「同目录多源」定义明确语义（重复行还是按顺序归属），属配置约定问题。

### 3. [P3] 源移出配置后回收站条目失联（backlog）

- 位置：`tiklocal/services/trash.py:86-97`
- 复现：删除源配置后 `/api/trash` 返回 `entries: []`，`restore` 抛 `KeyError`，文件仍在 `.tiklocal-trash/`。
- 未修原因：回收站条目需要冗余保存 `media_root`，涉及存储格式迁移。

### 4. [P3] `preserved_prefixes` 的前缀匹配过宽，保护了兄弟目录

- 位置：`tiklocal/services/library_index.py:95-101`
- 复现：`@default/sub` 被保护时 `@default/subdir/gone.jpg` 也被保留（`deleted=0`，对照 `deleted=1`）。
- 修复：改为「等于该前缀 或 以 `前缀/` 开头」，并对手工前缀做 `rstrip('/')` 归一（扫描器上报的是带尾斜杠的目录 URI）。
- 注：本轮第一次改法漏了尾斜杠归一，`test_round07_regressions.py::test_unreadable_subfolder_keeps_its_rows_in_the_index` 立刻变红——这正是回归测试的价值。

### 5. [P3] 持锁时 `POST /api/library/sync` 直接 500

- 位置：`tiklocal/web/library.py:388-391`
- 复现：另一连接持写锁时返回 500 并回显 `database is locked`。
- 修复：捕获 `sqlite3.OperationalError`，`locked`/`busy` 映射 409 `library.error.busy`（新增 zh/en 键），其余继续抛出。

---

## 四、性能（4 条）

### 1. [P1] 首次建索引同步读 EXIF：20k 库冷启动 11 s（backlog）

- 位置：`tiklocal/services/library_index.py`（`_record_for_path` 调 `discover_capture_time`）
- 复现：20k 假库 `create_app()` 冷 **11056 ms**，warm 2.1–2.9 s。
- 未修原因：正确修法是「先入库 + 后台补时间元数据」（`time_metadata_version=0` → 探测线程回填 1），要同时改写入路径、探测队列与「按时间排序」的回退逻辑，属结构性改动，留到专门一轮做。

### 2. [P1] `/api/library/items` 大库排序建临时 B 树

- 位置：`tiklocal/services/database.py`（迁移 4 之前无 `(mtime, uri)` 复合索引）
- 复现：20k 行、8000 偏移取 40 条 **220 ms**，`EXPLAIN QUERY PLAN` 显示 `USE TEMP B-TREE FOR ORDER BY`。
- 修复：迁移 4 新增 `CREATE INDEX IF NOT EXISTS idx_media_items_mtime_uri ON media_items(mtime DESC, uri)`；`test_media_items_page_uses_the_mtime_index` 断言计划里不再出现 TEMP B-TREE。

### 3. [P2] `/api/feed/mix` 候选全量载入后再算权重

- 位置：`tiklocal/services/recommendation.py:140-141`
- 修复：`if len(pool) > POOL_CAP: pool = rng.sample(pool, POOL_CAP)` 移到权重计算**之前**（抽样与打分顺序无关，分布不变），20k 库的候选从 2 万降到 1500。

### 4. [P2] 缩略图解码并发只有 2 路且无 single-flight（backlog）

- 位置：`tiklocal/services/thumbnail.py:22`（`_GENERATE_SLOTS = threading.BoundedSemaphore(2)`）
- 复现：冷缓存 200 张网格 p95 7.4 s。
- 未修原因：提高并发会与 Pillow 的内存峰值冲突（第 09 轮才为 222 MB 峰值加过 draft），正确修法是「按 CPU 数设上限 + 同路径 single-flight」，需要配套压测。

---

## 五、i18n（6 条，全部 backlog）

本轮 i18n 域的 6 条发现（`media_common` 9 个英文键、`sub_locale` 类型、`_js_catalog_guard` 只扫模板不扫 JS、`search.js` 中英混排、JS 内拼串、`<html lang>` 静态）**没有落地**：我核对工作区时发现上一批改动并未写入磁盘（与第 12 轮同样的回滚现象），本轮按实际代码重新确认后转入 backlog，下一轮以「逐条改动 + 立即跑 `test_i18n.py`」的方式重做。

---

## 六、前端交互（3 条 P3，均已修复）

1. **主题跨标签页不同步**：`tiklocal/static/base.js:25-33` 新增 `storage` 事件监听，另一标签页写 `theme` 时镜像应用；`setPreference` 的 `localStorage.setItem` 加 try/catch（隐私模式下写入抛错会让主题按钮失步）。
2. **下载弹层 Tab 逃逸**：`tiklocal/static/download_page_controller.js:498-509`，焦点落到 `<body>`（既非 first 也非 last）时把焦点拉回弹层。
3. **Toast 动作按钮可被双击触发两次**：`tiklocal/static/ui_feedback.js:29-42`，点击后 `removeEventListener` + `disabled = true`（第二次恢复请求 404 会把成功提示顶掉）。

---

## 七、验证

- 全量：`pytest -q` = **448 passed / 6 skipped / 0 failed**（167.03s）。
- 本轮新增 `tests/test_round13_regressions.py`（9 个用例）：405 `Allow`、URL 清单错误本地化、导出单 charset、清缓存不回显路径、合集名类型校验、前缀不保护兄弟目录、锁库 409、探测跳过失败行、items 查询走索引。
- 过程中修掉两个**本轮引入**的失败：
  - `test_unreadable_subfolder_keeps_its_rows_in_the_index` —— 前缀归一漏了尾斜杠（见 §三.4）；
  - `test_download_output_survives_non_gbk_bytes` —— 与 §一.1 的编码契约冲突，改写为 `test_download_output_survives_locale_encoded_bytes`。
- 语法：`py_compile` 覆盖 `app.py`、`downloader.py`、`downloads.py`、`library.py`、`settings.py`、`library_index.py`、`database.py`；`node --check` 覆盖 4 个改动的 `static/*.js`。
- 未做浏览器实测：本轮改动集中在后端契约、查询计划与编码，均由单测与 `EXPLAIN QUERY PLAN` 覆盖；浏览器回归留到下一轮与 i18n 重做一起做。
