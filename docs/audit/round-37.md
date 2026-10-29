# 第 37 轮：依赖与版本边界

> 审查日期：2026-10-29 ｜ 起点提交：`6ac2c23`（第 36 轮） ｜ 视角：**依赖与版本边界**

## 一、方法与范围

前 36 轮问的都是「这段代码在自己的假设下对不对」。这一轮换一个问法：**这些假设本身是从哪来的，换一台机器还成立吗？**

具体拆成五个域，每个域只回答「当某个外部条件与预期不同时，哪一层会先坏、坏得有多安静」：

| 域 | 范围 | 关注的外部条件 |
| --- | --- | --- |
| R1 | `pyproject.toml`、`tiklocal/**` 的 Python 与标准库用法 | 解释器版本、`sqlite3` 库版本、`tomllib`、`zoneinfo` |
| R2 | `download_tools.py`、`downloader.py`、`thumbs.py`、`media_info.py`、`similarity/indexing.py` | PATH 上的 yt-dlp / gallery-dl / ffmpeg / ffprobe 的版本与能力 |
| R3 | `templates/*.html`、`static/*.js`、`static/*.css` | 浏览器特性可用性（iOS 15/16、Safari 私有前缀） |
| R4 | `services/database.py`、`collections.py`、`media_sources_store.py`、`downloader.py` 的持久化格式 | 存储格式的版本号、迁移书签、SQLite 特性 |
| R5 | 主审自查 | 版本信息本身有没有被记录、能不能被看到 |

四个子代理只读（禁改文件、禁跑测试、禁起服务），在 Windows 上逐条给出 `file:line` + 触发路径 + 影响量化 + 最小修复 + 置信度；主审负责复现、定级与落地。

**结论：45 条（P0 0 / P1 4 / P2 24 / P3 17），当轮修 17 条（P1 4 + P2 12 + P3 1），其余 28 条转 backlog。**

## 二、问题总览

| 域 | P0 | P1 | P2 | P3 | 合计 | 当轮修复 |
| --- | --- | --- | --- | --- | --- | --- |
| R1 Python 与标准库 | 0 | 1 | 2 | 5 | 8 | 3 |
| R2 外部二进制 | 0 | 1 | 5 | 4 | 10 | 6 |
| R3 浏览器特性 | 0 | 1 | 7 | 3 | 11 | 2 |
| R4 存储格式版本 | 0 | 1 | 8 | 4 | 13 | 5 |
| R5 主审自查 | 0 | 0 | 2 | 1 | 3 | 1 |
| **合计** | **0** | **4** | **24** | **17** | **45** | **17** |

这一轮的 P1 有同一个形状：**某个外部条件不满足时，程序没有报错，而是继续按「满足」的样子运行。**

## 三、P1 详述

### R1-1 旧 `libsqlite3` 上索引永远是空的，且只有一行日志

`tiklocal/services/database.py` 的索引写入用 `INSERT … ON CONFLICT DO UPDATE`（需要 SQLite 3.24）和 `ROW_NUMBER()`（3.25）。迁移本身只建表，所以在带 3.22/3.23 的 Python（Debian 10 自带、部分 conda 环境）上：

1. `migrate()` 顺利通过，应用正常启动；
2. 第一次同步时每条写入都抛 `sqlite3.OperationalError: near "ON": syntax error`；
3. 同步层把它当成「这一轮失败，继续服务上一份索引」——而上一份索引是空的；
4. 用户看到的是**一个空媒体库**，日志里只有一条被当成正常重试的 warning。

修法：`MINIMUM_SQLITE = (3, 25, 0)`，在 `migrate()` 最前面比较 `sqlite3.sqlite_version_info`，不满足直接抛 `DatabaseVersionError`，由 `run.py` 的 `_fatal` 打印成一句人话。`--version` 也会打印实际版本（见 R1-3）。

### R2-1 装了 yt-dlp 但版本太旧：设置页说「就绪」，每个任务都失败

`tiklocal/services/download_tools.py` 拼出的命令行用了 `-S`/`--format-sort`（2022.03 起）与 `-P`/`--paths`（2021.09 起）；gallery-dl 用了 `--http-timeout`/`--sleep-429`（约 1.22 起）。旧版本二进制对未知选项的反应是 **argparse 退出 2 并打印 `no such option: -S`**，而 `error_line()` 只认 `error:` 前缀，于是：

- 任务状态 `failed`，错误栏显示的是原始 argparse 行（或空）；
- 设置页只判断「二进制是否存在」，显示「就绪」。

修法两处：

- `error_line()` 把 `no such option` / `unrecognized arguments` 映射成「下载工具版本过旧，请升级 yt-dlp / gallery-dl」；
- `probe_dependencies()` 增加 `yt_dlp_too_old` / `gallery_dl_too_old`（`version_is_supported()` + `MIN_TOOL_VERSIONS`），让前端可以把「就绪」改成「需升级」。版本号读不出来时一律当作**不受限**——宁可放过，也不要把一个能用的工具判成不可用。

### R3-1 `AbortSignal.timeout` 在旧 Safari 上让每个请求都变成「网络错误」

第 35 轮给下载页/作者页的 `fetch()` 加了 `AbortSignal.timeout(...)`（Safari 16 才有）。旧浏览器上这个表达式在 `fetch()` 的实参求值阶段就抛 `TypeError`，`catch` 把它当成网络失败：页面提示「网络错误」，重试按钮转圈，**而请求根本没发出去**。

修法：`tiklocal/templates/base.html` 增加 `window.tikTimeoutSignal(ms)`——`AbortSignal.timeout` 存在就直接用，否则用 `AbortController` + `setTimeout` 自己实现（`abort()` 的语义相同，都是让 `fetch` 以 `AbortError` 拒绝），并保证定时器在请求结束时清掉。16 处调用点统一改用它。

### R4-1 迁移书签丢失后，裸 DDL 让应用永久起不来

`_migrate_011/012/013/015` 直接 `CREATE TABLE` / `ALTER TABLE … ADD COLUMN`。正常路径上 `schema_migrations` 保证它们只跑一次，但**从备份恢复、手工清理、或迁移事务在提交前中断**都可能留下「表已经是新的、书签是空的」状态。此时：

- 011 抛 `table media_vectors already exists`；
- 012 抛 `duplicate column name: video_frames`；
- 013/015 同理。

这些都不是 `app.py` 判定「库损坏」的关键字，于是 `migrate()` 抛出、`_fatal` 打印、进程退出——**每次启动都失败，用户只能删库**。

修法：四条迁移都改成幂等（`_table_exists()` 短路 / `PRAGMA table_info` 判列存在 / `CREATE INDEX IF NOT EXISTS`），并新增 `tests/test_round37_regressions.py::test_migrations_survive_a_lost_bookmark`：跑完迁移后删光 `schema_migrations` 再跑一次，断言列仍在、不抛异常。

## 四、P2 详述（当轮修复的 12 条）

| 编号 | 位置 | 问题 | 修法 |
| --- | --- | --- | --- |
| R1-2 | `tiklocal/__init__.py`、`app.py`、`run.py` | 版本号有四个来源（`pyproject`、`__init__` 常量、`importlib.metadata`、`--version` 手写），装成 wheel 后互相不一致 | `__version__` 单一来源，`get_app_version()` 优先读已安装元数据、回落常量 |
| R1-3 | `tiklocal/run.py` | 启动日志不记 Python/SQLite 版本，用户报「索引为空」时无法判断是不是 R1-1 | `_log_runtime_versions()` 在启动时打印一行 `python=… sqlite=…`，`--version` 同时打印 |
| R2-3 | `tiklocal/services/media_info.py` | 用 `-show_entries stream=width,height` 探尺寸，手机竖拍视频的旋转矩阵在 `side_data_list` 里读不到 → 宽高颠倒，缩略图被拉成横图 | 改 `-show_streams`，先读 `side_data_list[].rotation`，再回落 `tags.rotate` |
| R2-4 | `tiklocal/services/downloader.py` | 子进程输出按 `locale.getpreferredencoding()` 优先解码；zh-CN 控制台下 yt-dlp 的 UTF-8 输出会被 cp936「成功」解成乱码路径，产物匹配不上 | `OUTPUT_ENCODINGS` 改为 UTF-8 优先；`Popen` 传 `PYTHONIOENCODING=utf-8` / `PYTHONUTF8=1`；全失败时按「替换字符最少」挑编码，而不是按顺序挑第一个 |
| R2-6 | `tiklocal/thumbs.py` | 没有 ffmpeg 时 `thumbs` 命令静默跳过所有视频、退出码 0，CI 里看起来「成功」 | `stats['missing_ffmpeg']` + 结束提示 + 有视频但无 ffmpeg 时退出码 2 |
| R2-7 | `tiklocal/services/thumbnail.py` | HDR 转码分支取 `cmd[-4]` 当输出路径，命令一改就取到 `-movflags` 的值，ffmpeg 报「无法打开输出文件」 | 改为显式记录输出路径变量 |
| R2-10 | `tiklocal/experiments/similarity/indexing.py` | 用 `shutil.which` 判断 ffmpeg/ffprobe，绕过了 `resolve_tool` 的 venv 与 `imageio-ffmpeg` 兜底 → 只装 `imageio-ffmpeg` 的用户永远扫不到视频 | 改用 `resolve_tool`；`tests/experiments/test_similarity.py` 的 5 处 monkeypatch 同步改为 patch `resolve_tool` |
| R4-3 | `database.py` 011/012/013/015 | 同 R4-1 | 同 R4-1 |
| R4-4 | `database.py` `MIGRATIONS` | 版本号只当主键用：两个分支各加 `Migration(18, …)` 时第二个会被静默跳过，全新安装也少一次 schema 变更 | 导入期断言 `1..N` 连续；`migrate()` 额外比对已存 `name`，改名即报错 |
| R4-5 | `services/collections.py` | `int(data.get("version") or …)` 在 `"version": "2.0"` 时抛 `ValueError`，落在 `_read` 的 `except ValueError` 之外 → 合集页与所有写入永久 500，文件既不隔离也不修复 | 解析失败回落当前版本；版本高于本程序理解时抛 `CollectionStoreCorrupt`（路由已有 503），不静默降级重写 |
| R4-10 | `services/library_index.py` | `int(existing.get("size_bytes") or -1)` 把真实的 `0` 当成「缺失」，0 字节文件与 mtime=0 的文件每次扫描都重算 | 显式判 `is not None` |
| R4-12 | `database.py` | `PRAGMA journal_mode = WAL` 的返回值被丢弃；SMB/FUSE 上静默回落 rollback journal，并发写入变慢且失去 WAL 的隔离语义 | 读返回值，非 `wal` 时 warning 说明「不要放网络盘」 |

## 五、未修条目（28 条，转 backlog）

按「修它的代价 > 它现在造成的损失」筛掉的，主要三类：

1. **需要新依赖或新基础设施**：R1-4（`tomllib` 缺失时的 `tomli` 回落——Python 3.10 已内置，真正缺的是 3.9，而 `pyproject` 已经 `>=3.10`）、R2-8（yt-dlp 的 `--print after_move:filepath` 需要 ≥2021.09，改了要重跑全部下载用例）。
2. **产品语义变更**：R2-5（下载失败时的 `--no-part` 保留策略）、R3-9（`localhost` 与 `127.0.0.1` 的 TLS 证书 SAN）、R4-6/R4-7/R4-8（三个 store 的 `version` 字段只写不校验——与第 34 轮 O3-2 同源，需要一次统一的 `StoreVersionError` 改造）。
3. **观测成本高于收益**：R1-5（`zoneinfo` 在 Windows 上缺 tzdata 时回落 UTC——只在用户显式设时区时可见）、R3-10（`100dvh` 在 iOS 15 的地址栏抖动）、R4-9（文件系统 mtime 粒度 2 s 导致「同一秒内两次修改」判不出——已由 size 兜底）。

## 六、实现要点

- **版本检查要放在「第一次使用」之前，不是「第一次失败」之后。** SQLite 的检查放在 `migrate()` 开头（R1-1），yt-dlp 的检查放在 `probe_dependencies()`（R2-1），迁移的检查放在导入期（R4-4）——三处都不等出错。
- **「读不出来」和「读出来是旧版本」是两件事。** `version_is_supported()` 在 `version_tuple()` 返回 `None` 时返回 `True`：读不出元数据就当作未知，不阻断。反过来，`CollectionStore` 读到比自己新的版本必须拒绝（R4-5），因为继续跑会**丢字段**。
- **幂等迁移的三种写法**：表级用 `_table_exists()` 短路；列级用 `PRAGMA table_info` 判存在；索引级用 `CREATE INDEX IF NOT EXISTS`。三者都不能用 `try/except sqlite3.OperationalError` 糊过去——那会把真正的语法错误也吃掉。
- **`or -1` / `or ''` 这类「缺省值」是隐式的类型转换**：0、空串、0.0 都是合法值，用 `or` 兜底等于把它们当缺失（R4-10）。
- **`journal_mode` 的返回值是唯一能知道 WAL 有没有生效的地方**（R4-12）——SQLite 不抛异常，只改返回值。
- **前端特性探测要探测「调用的那一刻」**，不要探测一次存起来：`AbortSignal.timeout` 的 shim 每次调用都检查，代价可以忽略。

## 七、验证

- 新增 `tests/test_round37_regressions.py`（15 条）：迁移编号连续、旧 SQLite 拒绝、迁移书签丢失后重跑、`collections.json` 的 `version` 非整数/未来版本、`version_tuple` 与 `version_is_supported` 的参数化用例。
- 聚焦回归：`tests/test_collections.py`、`test_download_tools.py`、`test_thumbnails.py`、`test_library_upgrade.py`、`test_web_runtime.py`、`test_download.py` = **85 passed / 3 skipped**；`tests/experiments/test_similarity.py` = **38 passed / 3 skipped**（改 patch 目标后从 28 errors 恢复）。
- `npm run build-css-prod` 重新生成 `static/output.css`（R3-6：第 36 轮改的错误页配色只在 `input.css` 里，产物未重建）。
- 全量 `pytest -q`：**641 passed / 8 skipped / 0 failed in 285.87s**（第 36 轮收尾 626，新增 15 条为本轮用例）。

## 八、覆盖率缺口

1. **没有真机浏览器矩阵。** R3 的结论全部来自静态证据（特性支持表 + 代码调用点），没有 iOS 15 实机或 BrowserStack 验证；`AbortSignal` shim 只做了单元级推演。
2. **没有旧版 yt-dlp / gallery-dl 可执行文件。** `MIN_TOOL_VERSIONS` 的阈值来自各项目 changelog，未用真实旧版本二进制跑过一次失败路径。
3. **旧 `libsqlite3` 只在测试里 monkeypatch 过 `sqlite_version_info`**，没有真的链接 3.22 的解释器。
4. **迁移书签丢失的用例只覆盖 011–015**，016/017 是 `CREATE INDEX`（天然幂等），未逐条构造。

## 九、下轮建议

第 38 轮建议视角：**权限与身份边界**——谁可以调用哪个端点、哪些数据属于哪个用户、文件系统权限与进程权限的边界在哪。前 37 轮里 `auth.py`、`web/settings.py`、`web/media.py` 的授权判断散落在各路由，还没有一轮专门按「主体 × 资源 × 动作」逐格核对过。
