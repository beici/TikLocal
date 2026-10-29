# 第 43 轮审计：升级与向后兼容

- 视角：**升级与向后兼容**（旧版本写下的文件、旧布局的缓存、旧版命令行用法、旧版前端约定、被手工编辑过的配置）
- 起点基线：`670 passed / 8 skipped`（第 42 轮收尾，tag `audit-round-43-start` 打在 `e05a562`）
- 收尾基线：`676 passed / 8 skipped`（新增 6 条回归用例）
- 方法与范围：四个域并行只读审查（U1 数据库迁移与索引器、U2 配置与 CLI、U3 缓存与派生数据、U4 API 与前端契约）+ 我的 U5 自查（旧数据格式与平台边界），逐条复核后合并去重。

## 一、问题总览

| 域 | 范围 | 已修 | 转 backlog |
| --- | --- | --- | --- |
| U1 | `database.py` 迁移、`library_index`、`json_storage`、`library` | 6 | 2 |
| U2 | `run.py` 与 `config.yaml`、下载配置兼容 | 3 | 2 |
| U3 | 缩略图缓存布局、作者档案、电台能量缓存 | 3 | 1 |
| U4 | 设置页扫描回执、前端本地状态与 i18n | 3 | 0 |
| U5 | 自查：旧文件编码、空参数启动 | 2 | 0 |
| 合计 | | 17 | 5 |

本轮唯一的 P1 在 U1：**索引器把「一个文件都没扫到」当成「用户删光了所有文件」**，于是整源的索引行连同续播点、观看历史一起被删掉。其余问题都是「旧版本写下的东西，新版本读不懂或读懂了却改写回去」。

## 二、P1 修复

### 1. 扫描为空不再等于「源被清空」（U1-1）

`tiklocal/services/library_index.py` 的扫描循环里原来只有一个守卫：

```python
if paths and not records:
    raise OSError(errno.ENOENT, 'source vanished during scan', str(source.path))
```

它挡得住「走完但每个 stat 都失败」，挡不住 **`paths` 本身为空**。而 `tiklocal/services/library.py` 在遍历时会把 junction / reparse 点（OneDrive 按需文件、目录联接、符号链接）从 `folders` 里**静默剔除**——当一棵树的子目录全是占位符或联接时，扫描结果就是「0 个文件」，紧接着的快照判定该源已空，`delete` 掉整源的行，孤儿清扫再把 `playback_positions`、`media_events` 一并带走。用户看到的是一次重启之后「媒体库空了、续播全没了」。

两处一起改：

- `library.py` 不再静默丢弃：被过滤的目录以 `@source/相对路径/` 前缀记进 `skipped`，扫描回执里能看到「这些目录被跳过了」；
- `library_index.py` 新增守卫：`paths` 为空但 `store.source_row_count(source.id) > 0`（新增方法，`SELECT COUNT(*)`，不物化任何行）且目录**并非真的空**时，抛 `ENOENT` 让该源按「暂不可用」处理——不删行、不扫续播点。用户真的清空目录时 `os.scandir` 为空，同步照旧执行，所以「删除所有文件」仍然生效。

## 三、P2 修复

### 2. 不认识的存储版本不再被覆盖（U1-2）

`tiklocal/services/json_storage.py` 的 `read_json_file()` 新增 `max_version` 参数：读到 `version` 高于本版本能理解的存储时，抛 `StoreUnreadableError` 而不是「按我懂的部分读进来、再把我懂的部分写回去」。两个版本共用一个数据目录时，旧版本启动一次就会把新版本新增的字段抹掉。`media_sources_store.load()` 已传 `max_version=STORE_VERSION`，`app.py` 既有的 `except OSError` 分支会退回到启动配置并记一条警告（不静默）。

### 3. 帮助与「你是不是想输」不再被配置错误挡住（U2-1）

`run.py` 原来在解析 argv **之前**就 `validate_config_types(config)`，于是 `config.yaml` 里一个 `port: true` 会让 `tiklocal --help`、`tiklocal --version` 和 `tiklocal migrate` 的提示全部变成「port 必须是整数」。现在校验挪到 argv 归一化之后，并对 `-h/--help/--version` 跳过；`tiklocal serve` 仍然照旧校验（错误照样是退出码 2）。

### 4. 一个坏字段不再重置整份下载配置（U2-3）

`validate_download_config(data, partial=True)` 是对整份载荷作答的，所以手改出来的 `max_concurrent: "abc"` 会让设置页显示、并在下次保存时写回**全部默认值**——cookie 目录、gallery 归档路径、TikTok 段一起丢。`DownloadConfigStore._load()` 改走新增的 `_salvage_download_config()`：整体校验通过就用整体结果，否则逐字段校验，只丢掉不过的那一个。

### 5. 旧版缩略图残留会被清掉（U3-1）

`ThumbnailService` 认得两代布局（v2 `sha1(root|uri|fmt)`、v1 `sha1(uri)`），但 0.3–0.13 的 `sha1(root|uri)` 既没人读、也没人删：用户每删一个媒体文件就永久留下一个预览（报告里的库是 0.8–1.5 GB，全被算进设置页的缓存体积）。新增 `_v1_path_for_key()`，在「两代现行布局都没有」时顺手 `unlink(missing_ok=True)`，`delete_thumbnail()` 也覆盖这一代。

### 6. 非 ASCII 作者不再互相覆盖（U3-2）

`author_profiles._safe_key()` 原来把每个非 ASCII 字符都换成 `_`，于是 `旅行` 与 `家人` 都得到 `_`：后同步的作者档案与头像覆盖前一个，页面显示错误的昵称。现在保留可读前缀并追加 `sha1(raw)[:8]`；同时新增 `existing_profile_path()` / `existing_avatar_path()`，在老键（`_.json` / `_.jpg`）仍存在时回退读取，所以**升级后老档案仍然可见**（`web/author.py` 的头像路由也改用它）。

### 7. 换过媒体根之后电台不再画错波形（U3-3）

`RadioEnergyService._cache_path()` 只用 `canonicalize_uri(uri)`（`@source/rel`）做键，键里不含源指向哪里。切换 `MEDIA_ROOT` 后同一个键指向了另一个文件，而 `_read_cache()` 只比 mtime——新文件更旧时校验通过，电台直接画出上一个库的波形。键改为 `canonicalize_uri(uri) + '|' + 解析后的绝对路径`。

### 8. 设置页的扫描回执键名与 `/api/library/sync` 一致（U4-1）

`web/settings.py::_scan_after_change()` 的默认字典用了 `'unavailable': []`，而所有读方（以及 `/api/library/sync`）用的是 `unavailable_sources` / `source_errors`。索引器缺席时前端读不到失败列表，只能显示「已重扫 0 项」。现在默认字典与真实回执同键。

### 9. BOM 不再吃掉 URL 列表的第一行（U5-1）

`url_list.normalize_url()` 里的 `str.strip()` 不把 U+FEFF 当空白，Notepad / PowerShell `Out-File` 写下的 BOM 让第一行解析成 scheme `\ufeffhttps`，被丢弃后**下次保存就永久消失**。现在先 `replace('\ufeff', '')` 再 `strip()`。

## 四、P3 修复（顺手）

- **U1-P3-1** `_migrate_011_simplify_media_vectors()` 在「`media_vectors` 已存在」的早退分支里只 `return`，而 `photo_groups` 是同一步建的：书签在两步之间丢失就会留下「版本说已应用、表却不存在」的库，`/flow` 的相关推荐每次请求都 `no such table`。早退分支现在先 `CREATE TABLE IF NOT EXISTS photo_groups`。
- **U1-P3-2** `_migrate_015_add_search_text()` 原来无条件 `SELECT uri, filename, rel_path FROM media_items` 再整表 UPDATE；19k 行等于把整张表重写一遍（书签丢失后重跑尤其痛）。现在加 `WHERE search_text IS NULL`。
- **U1-P3-3** `_migrate_017` 的 `ANALYZE` 失败会让整个迁移回滚，而重试会以同样方式失败——实例再也起不来。现在 `try/except sqlite3.DatabaseError` 记警告继续；索引同步下次快照会再跑一次。
- **U2-P3-1** `run.py`：`media_sources[0]['path']` 缺 `expanduser()`，文档化的 `path: ~/media` 首次启动会把 `MEDIA_ROOT` 和启动横幅写成 `<cwd>\~\media`。
- **U4-P3-1** `static/download_page_controller.js` 读到不在 `PAGE_SIZES` 里的 localStorage 值时只是丢弃，于是每次进页面都白算一次、用户的选择永远不生效。现在一次性改写成默认值。
- **U4-P3-2** `static/settings_page_controller.js` 写 `lang` cookie 后直接 `reload()`；浏览器拦截 cookie 时刷新回来还是旧语言、零提示。现在写完立刻回读 `document.cookie`，没有就弹一条 `settings.language.cookie_blocked` 错误提示并中止刷新（`i18n.py` 补了中英文案）。`static/i18n.js` 也把 localStorage 里的语言值按服务端同一白名单校验后再用。
- **U5-2** `run.py`：`len(argv) == 0` 与「未知子命令」共用一个分支，裸 `tiklocal`（无参数、配置合法）会走到 `argv[0]` 抛 `IndexError`。拆成两个分支。

## 五、未修清单（转 backlog，5 条）

| 条目 | 位置 | 为什么暂不修 |
| --- | --- | --- |
| U1-P3-3 旧版 `favorite.json`（v1，单文件收藏）仍被接受，与 `favorites.json` 并存时行为未定义 | `tiklocal/services/favorites.py` | 需要先确认是否还有用户在用旧文件名，删除兼容会静默丢收藏 |
| U1-P3-4 存储文件的**类型**变化（list → dict）会被当成损坏隔离 | `json_storage.read_json_file` | 隔离是安全侧；区分「旧格式」与「真损坏」需要每类存储自己的迁移函数 |
| U2-2 已删除的 HTTPS 相关 CLI 开关没有别名提示 | `tiklocal/run.py` | 属产品决定（是否长期保留旧写法），且需要同步帮助文本与文档 |
| U2-P3-2 设置页保存时读 store 失败会丢掉 `removed_ids`，被删目录下次启动复活 | `tiklocal/web/settings.py:167-173` | 需要改成「读失败即中止保存」，会改变现有错误路径的语义，留待专门一轮 |
| U3-P3-1 内嵌元数据只读头部 64 KB 的上限没有版本标记 | `tiklocal/services/embedded_metadata.py` | 与缓存键绑定，改上限会让所有旧缓存失效（一次性重扫），收益低 |

## 六、验证

- 新增 `tests/test_round43_regressions.py`（6 条）：BOM 首行不丢 URL；高版本存储被拒绝且原文件字节不变；坏字段不重置整份下载配置；两个非 ASCII 作者键不同；老键档案仍可读；**扫不到文件但索引非空的源不删行**。
- 聚焦：`tests/test_media_sources.py tests/test_round43_regressions.py tests/test_library_upgrade.py` → `67 passed / 1 skipped`；`tests/test_web_runtime.py` → `5 passed`。
- 手工探针：`media_sources: path: ~/...` 首次启动派生路径已展开；`config.yaml` 写 `port: true` 时 `--help`/`--version` 正常、`tiklocal migrate` 给出「migrations run automatically on `tiklocal serve`」、`tiklocal serve` 仍以退出码 2 报配置错误。
- 全量：`676 passed / 8 skipped`（起点 670，新增 6 条）。

## 七、结论

本轮没有 P0。唯一的 P1（扫描为空 = 源被清空）是「一个过滤条件把整棵树吞掉」与「空快照即删除」两条各自合理的规则叠在一起造成的破坏性后果——现在两者之间多了一道「索引非空且目录非空」的闸门。其余修复集中在**升级路径**：旧缓存布局、旧档案键、旧存储版本、旧命令行写法、旧前端本地状态，全部按「读得懂就继续读，读不懂就拒绝写」处理。

下一轮视角：**错误信息与用户可理解性**（报错是否指出下一步动作、是否泄漏实现细节、是否有可复制的标识）。
