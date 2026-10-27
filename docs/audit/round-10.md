# 第 10 轮 · 并发一致性、文件命名边界、错误面与首屏数据

- 日期：2026-10-27
- 审查方式：5 个只读 subagent（首页与首页接口、错误处理与日志一致性、文件命名与文件系统边界、移动端与键盘可达性、并发一致性），每条要求「文件:行号 + 逐字代码 + 复现 + 影响」，Lead 逐条甄别后自己改代码。
- 结论：**18 条发现 → 修复 18 条**（P1×2 / P2×10 / P3×6）。其中 2 条以「跳过并告警」收口而非支持（见第三节备注）。

## 一、首页与首页数据（3 条，全部修复）

| # | 级别 | 问题 | 修复 |
| --- | --- | --- | --- |
| 1 | **P1** | 「On This Day」先全局 `LIMIT 200` 再按年分组：同一天媒体超过 200 条时，最老年份被最近年份挤掉且无任何报错（视频无 EXIF 回退 mtime，批量导入必然共享同一天） | `library_index.on_days()` 新增 `per_year` 参数，SQL 用 `ROW_NUMBER() OVER (PARTITION BY substr(captured_local_date,1,4) ...)` 在**每年内部**取前 N；路由改为 `per_year=8` |
| 2 | P2 | `/api/collections` 每个收藏夹跑一次完整分页查询（含 COUNT）+ 重解析 JSON + 逐个 `resolve_path`，20 个收藏夹 535 ms，且是首页首屏 5 路之一 | 一次 `existing_uris()` 拿到所有卡片涉及 URI 的存在集合；计数改为集合交集；封面候选只取前 12 个且不再 stat（索引已答「存在与否」）。实测 80 张卡片 **19.5 ms** |
| 3 | P3 | `?date=9999-12-31` / `0001-01-01` 等边界日期在 `±3 天` 扩窗时抛 `OverflowError` → 500 + 完整 traceback | 解析后把日期夹到 `[date.min+3d, date.max-3d]`，实测 200 |

## 二、错误面与日志一致性（5 条，全部修复）

| # | 级别 | 问题 | 修复 |
| --- | --- | --- | --- |
| 1 | P2 | `favorites.json` 被截断后功能**永久**不可用：`load()` 每次都抛，而 `save()` 先 load 再写 → 所有收藏写入 503，设置页静默显示 0 收藏，且无备份、无自愈 | `services/favorites.py` 改用 `read_json_file(path, [], expected_type=list)`：坏文件隔离为 `*.corrupt` 并回退空集合，写入恢复 |
| 2 | P2 | `/api/download/jobs/delete` 把 `str(OSError)` 原样回给浏览器，泄漏绝对路径与 Windows 用户名；前端从不读该字段 | 失败项改固定文案 `download.error.file_move_failed`，真实异常只进 `app.logger.warning(..., exc_info=exc)` |
| 3 | P3 | 同类「写盘失败」返回码分裂：collections 落到 500 通用错误（还记一条未处理异常 traceback），favorites 是 503 | `POST /api/collections` 与下载队列 `OSError` 统一 → 503 + `common.error.storage_unavailable`（新增 zh/en 键） |
| 4 | P3 | 记录删了、文件一个没删，接口仍 `success: true`，前端只弹「已删除 N 条记录」 | 前端读 `data.files.failed`，非零时补一条 `download.select.failed_files` 警告（新增 zh/en 键） |
| 5 | P3 | `/api/thumbnail` 生成失败返回 500，与本项目 502/503 约定不一致 | 改为 502（单文件可解释失败，不是服务器 bug） |

## 三、文件命名与文件系统边界（4 条，全部修复）

| # | 级别 | 问题 | 修复 |
| --- | --- | --- | --- |
| 1 | **P1** | gallery-dl 任务把私有 cookie 副本（`temp_dir/cookies.txt`）当成下载产物移进媒体库根目录：用户会话被写进最容易被同步/共享的目录，且「一张图都没下到」的任务显示成功 | 新增 `downloader.gallery_media_outputs()`：排除工具日志与 cookie 副本，且只接受媒体扩展名（`GALLERY_MEDIA_SUFFIXES`） |
| 2 | P2 | 两个任务同名落盘时 `next_available_path()`（check）与 `os.replace()`（act）之间是竞态，后到的直接覆盖先到的，零异常、内容丢失 | `download_tools.claim_available_path()` 用 `os.open(..., O_CREAT|O_EXCL)` 原子占名，失败顺延序号；`_move_file_to_media_root()` 改用之，失败清理占位文件并返回空 |
| 3 | P2 | 文件名含孤立代理项（`bad\ud800name.mp4`）→ sqlite3 绑定抛 `UnicodeEncodeError`，`create_app` 直接崩，服务再也起不来 | `replace_snapshot()` 逐条 `uri.encode('utf-8')` 试编码，失败 `LOGGER.warning` 后跳过，不再拖垮整次扫描 |
| 4 | P3 | 尾随空格文件名被 `normalize_media_uri()` 的 `strip()` 归一成另一个 URI：文件在库里消失，而按该 URI 请求会返回同名兄弟文件的内容 | 同处检测 `name != name.strip()` 并告警跳过（见备注） |

> 备注：第 3、4 条选择「告警 + 跳过」而不是「完整支持」。这类名字是压缩包/异常拷贝工具留下的损坏产物，正确支持需要让 URI 键、收藏、回收站、缩略图缓存全部改成保留原始字节，改动面远大于收益；跳过能让它们从索引里消失且日志可查，不会张冠李戴。

## 四、移动端与可达性（3 条，全部修复）

| # | 级别 | 问题 | 修复 |
| --- | --- | --- | --- |
| 1 | P2 | 设置页「抖音模式」开关完全没有样式（`.settings-switch` 类名从未定义），只是浏览器默认 checkbox | `settings.css` 补 `.settings-switch`：track 44×26、label 44×44 命中区、`[aria-checked="true"]` 轨道态；实测点击后 `aria-checked` 正确翻转 |
| 2 | P2 | 触屏上多个按钮命中区小于 44 px | `base.css` / `download.css` / `radio.css` 新增 `@media (pointer: coarse)`：`.saved-tab`、`.mobile-mode-link`、`.app-button`、`.download-action`、`.download-icon-btn`、`.room-sound`、`.room-trigger` 提到 44 px，进度条加大 11 px 内边距 |
| 3 | P2 | 暗色下次要文本对比度不足（`--download-faint` 0.30、`.saved-eyebrow` 0.38） | 提到 0.45 / 0.58，并给暗色危险操作补 `#e69a93` 语义色 |

## 五、并发一致性（3 条，全部修复）

| # | 级别 | 问题 | 修复 |
| --- | --- | --- | --- |
| 1 | P2 | `UrlListStore` 的 add/clear/replace 是「读-改-写」且无锁：两个标签页同时提交会静默丢行；所有写者共用一个固定 `.tmp` 名，并发时互相覆盖（WinError 32 → 500） | 新增模块级 `_LIST_LOCK`（RLock）包住 add/remove/replace/clear/push；`_write()` 改用 `tempfile.mkstemp()` 生成唯一临时名 + `os.replace` |
| 2 | P2 | 下载完成回调的 `register_uris()` 与全量扫描的 `replace_snapshot()` 无互斥：扫描把「扫描期间新出现的行」当成不存在而删除，下载完的文件从库里消失 | `register_uris()` 进入索引器已有的 `_sync_lock`（原实现体改名 `_register_uris_locked`），与 `sync()` 串行 |
| 3 | P3 | 作者档案 `save_profile()` 是读-改-写且无锁、固定 `.json.tmp`：绑定主页链接与同步作者并发时互相覆盖字段 | 新增 `_PROFILE_LOCK`（RLock）+ 改用 `write_json_atomic()` |

## 六、验证

- 新增 `tests/test_round10_regressions.py`（5 个用例）：每年配额保留所有年份、孤立代理项/尾随空格被跳过、`claim_available_path` 20 线程不重名、gallery 产物排除 cookie 与日志、收藏文件损坏后自愈。
- 改写 `tests/test_media_state.py::test_corrupt_favorite_file_is_quarantined_instead_of_locking_writes`（原用例断言「损坏即 503」，与修复目标相反）。
- 全量 `pytest -q`：**432 passed / 6 skipped / 0 failed**。
- 实测：`/api/collections` 80 张卡片 19.5 ms；`/api/library/on-this-day?date=9999-12-31` 200；设置页开关 `aria-checked` 正确翻转。

## 七、转 backlog

- 尾随空格文件名与孤立代理项文件的**完整支持**（需要 URI 键按原始字节保存，影响收藏/回收站/缩略图缓存）。
- `/api/collections` 剩余的 `find_existing_uri()`/`canonicalize_uri()` 逐候选开销（已从 535 ms 降到 19.5 ms，进一步优化收益有限）。
