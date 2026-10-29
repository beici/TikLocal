# 第 27 轮审查报告：数据生命周期与删除语义

- 日期：2026-10-29
- 起点基线：`7fb15e0`（第 26 轮收尾），`pytest -q` = 602 passed / 8 skipped / 0 failed
- 视角：**数据生命周期与删除语义**——删除、恢复、再删除、改名与移动、软删除与回收站、
  索引与磁盘对账、孤儿行与孤儿文件
- 结论：**P0 1 / P1 2**，全部当轮修复；P2 与 P3 修 19 条，转 backlog 19 条

## 一、方法

前几轮按代码层切域，这轮改成**按生命周期阶段切**，因为同一个对象会在「删除」的四个
不同环节各自漏一次：移动文件、写回收站清单、删索引行、清派生数据。八个只读审查域：

| 域 | 范围 |
|----|------|
| F1 | 索引与对账：`library_index.py`、快照/增量、`rename`、孤儿扫描 |
| F2 | 下载链路：`downloader.py`、`web/downloads.py` 的任务删除与产物归属 |
| F3 | 收藏/合集/元数据/缩略图/回收站：`favorites.py`、`collections.py`、`metadata.py`、`thumbnail.py`、`trash.py` |
| F4 | 前端删除/恢复/播放：详情页、合集页、flow、`playback_resume.js` |
| F5 | 自查（我）：删除后派生数据的全链路追踪 |
| G1–G3 | 二轮聚焦：索引清理、设置页与回收站交互、播放器与 bfcache |

域级明细本轮并入主报告（不再单独拆 `round-27-rX.md`）：八域的结论表在第二节，
逐条机制在第三至五节，转 backlog 的条目在第八节与 `backlog.md` 里各留一行。

## 二、发现汇总

| 域 | P0 | P1 | P2 | P3 |
|----|----|----|----|----|
| F1 索引与对账 | 0 | 0 | 2 | 4 |
| F2 下载链路 | 0 | 1 | 4 | 2 |
| F3 收藏/合集/元数据/缩略图/回收站 | 0 | 0 | 3 | 7 |
| F4 前端删除/恢复/播放 | 0 | 1 | 2 | 5 |
| F5 自查 | 0 | 0 | 3 | 2 |
| G1 索引清理二轮 | 0 | 0 | 4 | 2 |
| G2 设置页与回收站二轮 | 1 | 1 | 3 | 3 |
| G3 播放器与 bfcache 二轮 | 0 | 0 | 2 | 3 |
| **合计** | **1** | **2** | **21** | **28** |

52 条里有 14 条是跨域同一根因（缩略图指纹、purge 清理范围、回收站目录命名各被两到三个
域独立命中），本轮修 19 条、转 backlog 19 条，其余已并入这两处。

## 三、P0 与 P1

### P0-1　大小写不敏感文件系统上「去重」会把文件全删掉（`tiklocal/dedupe.py`）

`_scan_files()` 对每个扩展名调 `root.rglob(f'*.{ext}')`。Windows 与 macOS 默认大小写
不敏感，`*.mp4` 与 `*.MP4` 匹配的是同一批文件，`a.mp4` 因此被登记两次。同一份内容被算
成「两个副本」，`select_files_to_keep()` 选一个保留、另一个删除——而两个路径指向**同一个
文件**，于是「保留」的那份也被删了。这是 CLI `tiklocal dedupe --delete` 的数据丢失路径，
不是显示问题。

修法：扫描结果按 `os.path.normcase(os.path.abspath(path))` 去重，保留首次出现的真实大小写；
`select_files_to_keep()` 再补一道交集守卫（即使上游分组出错也不让同一文件同时出现在
keep 与 delete）；`delete_files()` 修 `size_freed` 对同一 inode 的重复计数。

验证：临时目录里造 `a.mp4` 与 `b.MP4`（内容相同）+ 一个不重复的 `sub/c.mp4`，
`_scan_files` 得到 3 条且 normcase 唯一，`find_duplicates` 只成一组，keep=`a.mp4`、
delete=`b.MP4`，交集为空；`delete_files` 的 `size_freed` 等于 10 而不是 20。

### P1-1　移除媒体源会把整个回收站一起带走（`tiklocal/web/settings.py`）

「移除媒体源」删的是配置里的那一行，但回收站目录 `.tiklocal-trash` 就住在媒体源目录里。
用户在设置页移除一个源，几十天里攒下的可恢复文件随目录一起消失——界面只提示「已移除」。
修法：移除前检查 `Path(source.path) / TRASH_DIR`，有条目就返回 409 并说明先清空回收站
（`settings.library.source_trash_not_empty`，带条目数）。

### P1-2　删除正在播放的媒体时播放器先于文件释放（`tiklocal/static/detail_page_controller.js`）

`confirmDelete()` 直接调 `TikLocalUI.trash(...)`，`<video>` 仍在播放、仍持有文件句柄。
Windows 上服务端的 `shutil.move` 拿到 `WinError 32`，接口 409，用户看到「删除失败」——
而文件其实没动。修法：确认后先 `pause()`、清 `src`、`load()` 释放句柄，再发删除请求；
删除失败时把 `src` 恢复回去并提示。

## 四、P2 详述（已修 8 条）

1. **回收站移动留下 `.info.json` 孤儿**（`services/trash.py`）：删除 `clip.mp4` 时
   `clip.mp4.info.json` 留在原目录，永久占盘；更糟的是下次有同名文件落进来，它会继承旧
   文件的来源与标题。修法：`sidecar_paths()` 收集两种命名（`clip.info.json`、
   `clip.mp4.info.json`），清单里记 `sidecars`，移动/恢复/永久删除时一并处理；单个
   sidecar 移动失败不阻断删除，只从清单里剔除。
2. **永久删除不清媒体元数据**（`services/metadata.py`、`web/media.py`、`app.py`）：标题、
   标签、简介都按 uri 存在 `media_metadata.json`，purge 只清收藏与合集。修法：新增
   `MetadataStore.delete(key)`；`register_media_routes` 接收 `image_metadata`，
   purge 时删除对应条目（连同 `media_metadata` 的两种键写法）。
3. **元数据文件损坏时静默清空**（`services/metadata.py`）：`except json.JSONDecodeError:
   return {}` 让下一次保存用「只含新条目」的 dict 覆盖整份文件，几百条标题标签一次性
   消失；非 UTF-8 则每次请求都抛，功能永久 503。修法：改用 `read_json_file(...,
   expected_type=dict)`——损坏时隔离副本并返回默认值，读失败抛 `StoreUnreadableError`。
4. **purge 后同名新文件继承旧收藏**（`web/media.py`）：收藏与合集按 uri 存，purge 时若
   同名文件已经被重新下载回来（备份恢复、重下），旧条目会挂到新文件上。修法：purge 后
   先 `library_service.resolve_path(uri)`，文件已存在就跳过收藏/合集/元数据的清理。
5. **purge 不清索引状态表**（`services/library_index.py`）：`media_items` 删了，
   `media_state`（收藏/偏好/亲和/字幕）与 `media_vectors` 的行还在。修法：新增
   `forget_uri(uri)` 一次清三张表，purge 与「重新登记」路径共用。
6. **合集页请求失败显示成空合集**（`static/collections_page_controller.js`）：`fetch`
   失败被 `catch` 吞掉，页面渲染成「这个合集还没有内容」。修法：失败时显示错误态并给出
   重试按钮，不再把网络错误说成空数据。
7. **恢复被占用文件返回 500**（`web/media.py`）：`api_trash_restore` 只捕 `KeyError`，
   `shutil.move` 的 `WinError 32` 直接变成 500。修法：捕 `OSError` → 409 + 可读文案。
8. **启动期回收站清理可能拖垮启动**（`web/media.py`）：`purge_expired()` 在注册路由时
   同步跑，只有 `KeyError`/`OSError` 被跳过，数据库锁或条目损坏会直接冲出 `create_app()`。
   修法：补 `except Exception` 并记 warning——一个坏条目不该让整个应用起不来。

## 五、P3（已修 8 条）

- `static/ui_feedback.js`：`trash()` 现在返回布尔，调用方能区分「已移入回收站」与「失败」。
- `static/detail_page_controller.js`：恢复失败不再跳转，停留在详情页并提示。
- `static/library_page_controller.js`、`static/flow_page_controller.js`：监听 `pageshow`
  的 `persisted`，从 bfcache 后退回来时重新拉一次列表，避免看到已删除的条目。
- `static/settings_page_controller.js`：`online` 事件触发时刷新回收站列表；空回收站
  部分失败时提示「已删 N 项，M 项仍被占用」而不是谎报全部成功。
- `static/playback_resume.js`：记住的播放位置对应的文件已不存在时丢弃该记录。
- `tiklocal/dedupe.py`：keep/delete 交集守卫、`size_freed` 去重计数（见 P0-1）。
- `tiklocal/i18n.py`、`tiklocal/i18n_pages_media.py`：新增 4 个键（源删除受阻、恢复失败、
  空回收站部分失败、详情页删除失败）。

## 六、实现要点

- `sidecar_paths()` 只返回 `is_file()` 的路径，因此「文件没有 sidecar」与「sidecar 是
  目录」都走同一条空列表分支，不需要额外判断。
- 回收站清单里的 `sidecars` 是**文件名**而非路径：条目目录名带时间戳，恢复时按当前
  条目目录重新拼，避免把绝对路径写进 JSON。
- `forget_uri()` 与 `delete()` 分开：前者用于「文件没了但 uri 还要被重新登记」的路径，
  后者是物理删除索引行。两者共用同一份表清单，避免下次又漏一张表。
- `purge()` 里的「同名文件已存在」检查放在派生清理**之前**，否则会先把新文件的缩略图删掉。

## 七、验证

- `pytest -q tests/test_trash.py tests/test_captions.py tests/test_media_sources.py`：
  **64 passed**（`test_trash.py` 新增 2 个用例：sidecar 随删除移动与恢复、恢复被占用
  文件返回 409）。
- 去重探针（一次性脚本，未入库）：大小写重复目录的扫描/分组/删除行为符合预期，见 P0-1。
- 全量 `pytest -q`：**604 passed / 8 skipped / 0 failed**，224.23s（新增 2 个用例）。

## 八、转 backlog（19 条）

见 `backlog.md` 的「第 27 轮新增」。挑三条说明为什么这轮不做：

- **rename/move 不迁移收藏键**：需要 uri 级联迁移 + 事务，且 CLI 改名与 web 改名共用
  同一段逻辑，改动面大于收益。
- **缩略图指纹只看 mtime**：正解是「mtime + size + inode」组合，需要先测扫描期成本；
  只加 mtime 之外一项会让每次扫描多一次 stat。
- **下载历史删除不清理 sidecar**：与回收站共用 `sidecar_paths()` 即可，但删除历史还要
  触发索引删除，属结构性改动，留到下一轮「删除语义」复访。

## 九、覆盖率缺口

- 没有 JS 运行时测试：本轮 8 条前端修复靠人工走查 + 逐行核对调用点，没有自动化回归。
- 回收站与索引的对账只在单进程下验证过；多进程（waitress 多线程 + CLI 同时跑）未覆盖。
- `dedupe` 的 P0 只在 Windows 语义下复现过，macOS 的大小写不敏感行为未实测（同一 API）。

## 十、下轮建议

视角：**并发与多进程一致性**——waitress 多线程、CLI 与 web 同时运行、`jobs.json` 与
索引库的交叉写。这是本轮唯一没有覆盖到的生命周期维度：删除语义在多进程下会重新出现
（两个进程同时 purge、索引重建与删除交错）。
