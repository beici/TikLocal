# 第 07 轮审查报告（2026-10-27）

起点基线：`pytest -q` = **399 passed / 6 skipped / 0 failed**（第 06 轮收尾后的干净基线）。

本轮换域，避开前六轮已覆盖的路径，5 个只读 subagent 并行：

| 域 | 成员 | 发现 |
|----|------|------|
| 下载引擎适配层（yt-dlp/ffmpeg 参数、cookie、playlist） | `audit-download-pipeline` | 3 P2 / 1 P3 |
| 日志与可观测性 | `audit-core-routes` | 2 P2 / 2 P3 |
| Windows 路径与编码 | `audit-web-routes` | 2 P2 / 1 P3 |
| 静态资源与前端产物 | `audit-frontend-shell` | 2 P2 / 1 P3 |
| 分页/排序/去重 | `audit-flow-ui` | 1 P2 / 1 P3 |

确认 16 条（P2 9 / P3 7），修复 15 条，转 backlog 1 条（见 `backlog.md`）。

---

## P2

### 1. `--paths temp:` 被 yt-dlp 完全忽略，半成品文件直接落在媒体库

`tiklocal/services/download_tools.py:112`（修复前）：

```python
"-o", str(media_root / YT_DLP_OUTPUT_TEMPLATE),
```

实测 yt-dlp 输出 `WARNING: --paths is ignored because the output template is an absolute path`，`temp/` 目录始终为空。`.part` / `.f137.mp4` 因此写在媒体目录里：扫描器会把半成品当作品收录（缩略图失败、播放 0 秒），`/library` 出现无法删除的"幽灵条目"。

**修复**：模板改相对路径，落点交给 `-P`：

```python
"-o", YT_DLP_OUTPUT_TEMPLATE,          # download_tools.py:118
"-P", f"home:{media_root}",            # :119
cmd.extend(["-P", f"temp:{temp_dir}"])  # :124
```

### 2. 引擎直接拿到用户的 cookie 原件，并发任务会互相截断

`tiklocal/services/downloader.py`（修复前）：`--cookies <imported.txt>` 指向持久化目录里的原件。yt-dlp 以读写方式打开该文件并在退出时回写 jar，两个并发任务共享同一路径时后写者会把先写者的内容截断，用户下次导入的登录态就丢了。

**修复**：新增 `_private_cookie_copy()`（`downloader.py:1219-1234`），每个任务在 `temp_dir` 里拿一份一次性副本；拷贝失败才回退原件（只读场景仍可下载）。

### 3. 播放列表只记录第一个输出文件，N-1 个文件对索引/历史/删除不可见

`downloader.py` 的 `_run_tool` 解析 `--print after_move:filepath` 时只保留最后一个 `Destination`（原实现 `destination = line` 覆盖赋值）。下载 10 条播放列表，历史里只有 1 条，其余文件既不能删也不能收藏。

**修复**：`output_paths: list[str]` 累积去重（`downloader.py:1019-1040`），`_execute_download_direct` 返回全部相对路径（`:1046`），调用方逐个入库。

### 4. 下载失败没有任何服务端日志，UI 只有一句空错误

`downloader.py` 整个模块没有 logger；工作线程崩溃时只写 `error_message = str(exc)`，而 `TimeoutError()` 之类异常 stringify 为空串，前端显示"下载失败"、服务端无 traceback。

**修复**：模块级 `LOGGER`（`downloader.py:59`）；工作线程 `except` 里 `LOGGER.exception('Download job %s crashed', job_id)`（`:955-958`）；错误文案回退到类型名（`:1025-1029`，`str(exc) or f'{type(exc).__name__} while downloading.'`）。

### 5. 应用没有任何日志配置：轮转文件不存在，waitress 与 Flask 各写各的

`tiklocal/app.py` 只调用 `app.logger`，没有 `logging.basicConfig`，也没有文件 handler；`instance/` 下永远没有 `tiklocal.log`，线上排障只能看终端。

**修复**：新增 `tiklocal/logging_setup.py`，`create_app()` 最先调用 `configure_logging(app.instance_path)`（`app.py:4-8`）：`RotatingFileHandler`（2 MB × 3）+ 与 waitress 共享的 stderr handler，Flask 默认 handler 被替换，`waitress` 晚到的 `basicConfig()` 成为 no-op，同一 record 只落一次。

### 6. `--print after_move:filepath` 的多行输出被按"单行"解析（非 ASCII 模板超长）

`download_tools.py:34` 的模板在中文文件名上叠加 80 字符截断，yt-dlp 在 Windows 上拼出的绝对路径超过 259 字符后 `os.replace` 失败，任务报 `ERROR: unable to rename file`。

**修复**：模板改相对路径 + `-P home:` 后，绝对路径不再由引擎拼接；同时把文件名字段的截断宽度收紧到 `.80B`（按字节截断，CJK 3 字节/字），配合 `temp/` 缩短中间路径。

### 7. 设置页"缓存条数"永远显示 0：选择器写错 id

`tiklocal/static/settings_page_controller.js:87`（修复前）`document.querySelector('#cache-size')`，而模板里该元素的 id 是 `cache-copy`（`settings.html:110`），`null` 被 `if (el)` 静默吞掉 → 缓存统计从不刷新。

**修复**：模板补 `id="cache-copy"`，控制器改按该 id 取值并同时刷新副本数与占用。

### 8. `output.css` 落后于模板：新增类名在产物里不存在

`static/output.css` 最后一次构建早于第 03-06 轮新增的 `sr-only`、`timeline-sticky-bar` 等类，模板引用了 Tailwind 里存在但产物里没有的类 → 无障碍标签不隐藏、移动端吸顶条错位。

**修复**：`npm run build-css-prod` 重新生成（`output.css` 2 行变更，压缩产物单行）。

### 9. `/api/feed/mix` 的分页游标不是快照：第 2 页重复第 1 页的卡片

`tiklocal/web/flow.py:471` 的 `mixed[start:end]` 每次请求都从同一 seed 重算整张权重序列，而 `recommendation.py:98` 的 `recent_penalty`（`last_shown_at`）与 `:134` 的探索分支（按 `impressions` 排序）会被上一页刚写入的 impression 改变。

实测 120 张图、`size=24`：第 1 页 24 张 → 对这 24 张 `POST /api/activity`（accepted=24）→ 第 2 页**重复 2 张**（img073、img054），且 24 张里有 22 张掉出第 2 页窗口；不发 impression 的对照组重复 0。

**修复**（两处，让"同一 feed 会话内排序冻结"）：

- `recommendation.py:96-108` 新增 `session_start` 参数：`_recent_view_weight(..., session_start)` 对本会话内已展示的条目返回 1.0（不再重复降权）。
- `recommendation.py:133-140` 探索分支在 `session_start` 存在时改按 `zlib.crc32(f"{seed}:{uri}")` 排序——与 impression 计数无关，页间稳定。
- `flow.py:594-612` 解析 `since`（首帧由服务端在响应里回显，前端 `flow_page_controller.js` 原样带回后续页），并把它作为 `session_start` 传给两个 select。

实测（`.smoke-audit/probe_feed_cursor.py`）：120 张图连续翻 5 页、每页后写 impression，**去重后 120 张、重复 0、缺项 0**；新增 `tests/test_feed_mix.py::test_mix_feed_pages_do_not_repeat_cards_after_impressions`（60 张 3 页，断言页间无重复且首帧回显 `since`）。

---

## P3

### 10. 集合头部计数用 JSON 里的历史值，删除文件后永久偏大

`tiklocal/web/library.py:101` 渲染 `collection.get('item_count', 0)`，而网格来自 JOIN 索引的 `total`；通过应用删除文件只删索引行、不清 collection 条目，头部于是显示"3 条媒体"、网格只有 2 张，且没有入口清理。

**修复**：`collection_detail_view`（`library.py:291-300`）用本次查询的 `page['total']` 覆盖摘要里的计数；列表页仍用存储值（避免每集合一次 COUNT）。

新增 `tests/test_collections.py::test_collection_header_count_follows_the_index_after_a_delete`。

### 11. 回收站删除在只读文件上留下孤儿目录

`tiklocal/services/trash.py:61-63`（修复前）`shutil.rmtree(folder)`：`rmtree` 先删 `entry.json` 再删载荷，Windows 上遇到只读媒体文件（从光盘或只读共享拷来的）抛 `PermissionError`，于是目录既不在列表里、也不能恢复、永远不会过期——静默且永久的泄漏。

**修复**：新增 `_delete_folder()`（`trash.py:64-86`）：先清只读位、逐个删载荷文件，`entry.json` 最后删，最后才 `rmtree`；任何一步失败都不会留下"没有清单的目录"。

### 12. 索引扫描遇到 EACCES 子树会整段丢弃，库"少了一半"且无提示

`tiklocal/services/library.py:121-148`（修复前）`os.walk` 的 `onerror` 直接返回，权限不足的子目录里的既有索引行在下一次 `replace_snapshot` 时被删除——用户只是暂时没权限，作品却从库里消失。

**修复**：`scan_source()` 记录不可读前缀（`skipped`，`:142-148`）；`LibraryIndexer` 把它并进 `preserved_prefixes` 传给 `replace_snapshot()`（`library_index.py:719-738`），这些前缀下的行不参与删除，结果里回 `skipped_prefixes`。

（尚未把这些前缀回给前端：`/api/library/sync` 的响应还没带 `skipped_prefixes`，见 backlog。）

### 13. 媒体探测失败静默

`library_index.py:902-940`（修复前）`probe_media_dims` 抛错只 `continue`，缩略图/时长永久缺失且无日志。

**修复**：`MediaProbeWorker` 对失败条目记 `WARNING` 并保留"待重试"标记（`:940`），下次同步再试。

### 14. `run.py` 的框线字符在非 UTF-8 重定向输出下崩溃

`tiklocal/run.py` 首次启动打印初始密码时用了 `┌─┐│└─┘`；stdout 被重定向到文件/管道时 Python 按 locale 编码（cp1252/cp936）写，`UnicodeEncodeError` 会在密码已生成、还没打印出来时终止进程——用户既没看到密码也无法再次获取。

**修复**：启动时对 `sys.stdout`/`sys.stderr` 调 `reconfigure(errors='replace')`（`run.py`），横幅改纯 ASCII（`+---...---+`）。

### 15. `error.html` 引用了不存在的 CSS 变量

第 06 轮新增的错误页用了 `var(--text-muted)` 等未在 `output.css` 里定义的变量，暗色下正文与背景同色。

**修复**：改用已定义的 `--color-text-secondary` / `--color-surface`，并在生产构建后复核对比度。

### 16. `json_storage` 把"瞬时共享冲突"当成损坏，整份 store 被改名

`tiklocal/services/json_storage.py`（修复前）`read_json_file()` 的 `except OSError: quarantine_corrupt_file(...)`：Windows 上另一个进程正在 `os.replace` 时，读者会拿到 `PermissionError`/`FileNotFoundError`，这份完全正常的 store 就被改名成 `.corrupt`，下一次保存再把默认值落盘——用户的下载配置/电台画像凭空清空。

**修复**（`json_storage.py:74-108`）：`OSError` 改为 3×20 ms 重试，仍失败则返回默认值但**不动原文件**；只有 `UnicodeDecodeError`、`JSONDecodeError` 与类型不符才隔离（`:78-81`、`:100-107`）。`write_json_atomic()` 的 `os.replace` 也加了 5×30 ms 重试，`_prune_quarantine()` 只保留最近 3 份 `.corrupt*`。新增 `tests/test_json_storage.py`（4 个用例：重试后成功、隔离非法 JSON、UTF-16 隔离、并发写不丢数据）。

---

## backlog（本轮确认、暂不修）

- **Windows 保留名与超长路径**：`CON.mp4`、`aux.jpg`、>259 字符路径仍会让 yt-dlp 失败；需要下载命名层做保留名改写与长路径前缀（`\\?\`），影响面涉及命名规则与已有文件重命名，单独一轮处理。

## 验证

- `pytest -q`：**0 failed / 412 passed / 6 skipped**（79.59s）。
- 新增测试：`tests/test_download_tools.py`（4）、`tests/test_json_storage.py`（4）、`tests/test_round07_regressions.py`（3）、`tests/test_feed_mix.py` 追加 1 个、`tests/test_collections.py` 追加 1 个。
- 实测探针：`.smoke-audit/probe_feed_cursor.py`（120 张图连续 5 页、每页写 impression → 去重 120、重复 0、缺项 0）。
