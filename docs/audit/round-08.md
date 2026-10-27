# 第 08 轮审查报告（2026-10-27）

起点基线：`pytest -q` = **414 passed / 6 skipped / 0 failed**（第 07 轮收尾后的干净基线）。

本轮换域，避开前七轮已覆盖的路径，5 个只读 subagent 并行：

| 域 | 成员 | 主要发现 |
|----|------|----------|
| SQLite 数据层（迁移/索引/并发） | `audit-data-layer` | 库文件损坏即无法启动、删源后索引残留、写争用 500 |
| 鉴权、会话、配对、CSRF | `audit-core-routes` | 注销不吊销、限流单桶、401 不跳登录 |
| 配置、CLI、部署 | `audit-web-routes` | CLI 目录被配置顶掉、端口占用误报权限、类型不校验 |
| 播放器与媒体解码、缩略图质量 | `audit-download-pipeline` | EXIF 尺寸、缩略图放大、缺 playsinline |
| 跨 store 一致性（收藏/回收站/集合/索引） | `audit-frontend-shell` | 永久删除留引用、恢复失败 500、集合计数不降 |

确认 **19 条**（P2 14 / P3 5），修复 **17 条**，转 backlog **2 条**（见 `backlog.md`）。
另在修「数据库损坏」时发现一条报告没提的缺陷：**所有 SQLite 连接从不关闭**（P2-13）。

---

## P2

### 1. EXIF 旋转照片入库尺寸是旋转前的，竖拍照片在网格里被压扁

`tiklocal/services/media_info.py:27-30` 用 `img.size`，而缩略图侧 `tiklocal/services/thumbnail.py` 已经 `exif_transpose`。

复现：写一张 `Orientation=6` 的 4000×3000 照片 → `/api/library/items` 的 `width/height` = 4000×3000，缩略图 = 3000×4000；`/library` 的 `object-fit: cover` 按 4:3 裁切竖图，人像头部被切掉。

**修复**：入库前先 `ImageOps.exif_transpose`（`media_info.py:33`），与缩略图使用同一坐标。

### 2. 视频缩略图把高度钉死在 360，小视频被放大

`tiklocal/services/thumbnail.py:346-350`（修复前 `scale=-2:360`）。

复现：320×240 的 mp4 → ffmpeg 输出 480×360（放大 1.5 倍），文件更大且更糊；竖屏小视频被拉成 360 高。

**修复**：`-vf scale=-2:'min(ih,640)'` —— 只缩不放，同时保留 PIL 路径 `thumbnail((640, 640))` 的不放大语义。

### 3. `<video>` 缺 `playsinline`，iOS 上详情页视频被强制全屏

`tiklocal/templates/detail.html:81-82`、流内视频由 JS 创建（`tiklocal/static/flow_page_controller.js:1043`、`:1057`、`:1225`、`:1250`）。

复现：iOS Safari 点播放 → 进入系统全屏播放器，页面上的控制栏失效。

**修复**：模板与 JS 创建的 video 元素都加 `playsinline webkit-playsinline`。

### 4. 永久删除只删文件，收藏/集合里的引用留着，同名新文件会「继承」收藏状态

`tiklocal/web/media.py:115-135`（修复前）`purge()` 只做 `trash_service.purge()` + 缩略图清理。

复现：收藏 `album/a b.jpg` → 加入集合 → 回收站永久删除 → `favorites.json` 仍含该 URI；再放一个同名文件进来，它直接显示为已收藏、且出现在集合里。

**修复**：`purge()` 追加 `favorite_service.set_favorite(uri, False)` 与 `collection_store.remove_items(...)`，整体 best-effort（失败只记日志，不让删除报错）。

### 5. 删除成功后索引写失败 → 500 + 幽灵卡片（点开 404）

`tiklocal/web/media.py:170-176`（修复前 `media_index.delete(entry['uri'])` 无保护）。

复现：monkeypatch `media_index.delete` 抛异常 → `POST /delete/...` 500，但文件已在回收站；列表刷新后卡片消失、再刷新又出现，点开 404。

**修复**：`try/except Exception` 包住索引删除并记 WARNING，删除仍返回 200（下一次同步也会剪掉 unseen 行）。

### 6. 回收站恢复时索引写入失败 → 500，文件回到磁盘却从 App 里消失

`tiklocal/web/media.py:181-196`（修复前）`library_indexer.register_uris([uri])` 无保护。

复现：monkeypatch `MediaIndexStore.upsert` 抛异常 → `POST /api/trash/<id>/restore` 500，文件已恢复但列表里看不到，用户会重复点恢复。

**修复**：try/except → 返回 `{'uri', 'url', 'indexed': False}`（成功路径 JSON 形状不变），前端按 `indexed` 提示「已恢复，索引将在下次同步后生效」。

### 7. 集合卡片上的「N 项」读的是存储列表，删除后永远不减少

`tiklocal/web/library.py:153-158`（修复前 `item_count = len(collection['items'])`）。

复现：集合 2 项 → 永久删除 1 项 → 卡片仍显示 2 项，点进去只有 1 项。

**修复**：卡片计数改用与详情头同一查询（`_build_library_page(collection_id=..., limit=1)['total']`），索引不可用时回退存储计数。

### 8. 401 时不跳登录页，前端只弹一个 toast，页面停在旧数据

`tiklocal/static/base.js:131-142`（修复前各调用方各自处理 401，多数只 `toast(error)`）。

复现：会话过期后在库页点删除 → toast「请先登录」→ 页面仍显示旧数据，用户以为删除成功。

**修复**：`base.js` 统一包装 `fetch`：`/api/` 响应 401 且当前不在登录页时 `location.href = '/login?next=...'`，并用 `window.__tiklocalRedirecting` 防重复跳转。

### 9. 数据库文件损坏/不可写时整个应用无法启动，没有隔离或重建路径

`tiklocal/services/database.py:361-368`、`tiklocal/app.py`、`tiklocal/run.py:345`。

复现：把 `instance/tiklocal.sqlite3` 写成 200 字节垃圾 → `sqlite3.DatabaseError: file is not a database`（栈顶 `database.py:366`）；截断到 1/3 → `database disk image is malformed`；CLI 只 `except ValueError`，用户看到裸 traceback 退出，不知道删哪个文件能恢复。

**修复**：`app.py` 新增 `_open_database()`：`migrate()` 捕获 `sqlite3.DatabaseError`，命中 `malformed` / `not a database` / `corrupt` / `file is encrypted` 时把文件改名为 `<name>.<时间戳>.corrupt`（连同 `-wal`/`-shm` 一起清掉），用空库重建并写 WARNING；`locked` / `readonly` 等非损坏错误仍然抛出。

### 10. 从配置里删掉一个媒体源，它的索引行永远留着

`tiklocal/services/library_index.py:86-94`、`:754`。

复现：run1 配置 `[a, b]` → `total=4`；run2 只留 `[a]` → `total` 仍是 4，`@b/*` 卡片照旧返回，`/media/@b/b1.mp4` 与 `/detail/@b/b1.mp4` 都 404（缩略图命中缓存所以还有图），`POST /api/library/sync` 返回 `deleted: 0`。

**修复**：`replace_snapshot(..., configured_source_ids=...)` 追加 `DELETE ... WHERE source_id NOT IN (当前配置的源)`；扫描失败的源仍由 `synced_source_ids` 保护（不误删拔盘中的源）。

### 11. 写争用下 `database is locked` 直接冒到 `/api/activity`，500 且丢事件

`tiklocal/services/database.py:434-441`。

复现：12 线程 × 60 批 `record_many(50)` + 2 线程 `replace_snapshot(8000 行)` → 720 次里 15 次 `OperationalError: database is locked`。

**修复**：`record_many()` 对 `locked` 做 3 次有界重试（50/100 ms 退避），其余异常照旧抛出。

### 12. 所有 SQLite 连接从不关闭（报告未提，修 P2-9 时发现）

`tiklocal/services/database.py:362`（修复前 `connect()` 直接返回 `sqlite3.connect(...)`）；24 处调用全部写成 `with self.database.connect() as conn:`，而 `sqlite3.Connection.__exit__` **只提交/回滚，不关闭**。

后果：每次查询泄漏一个句柄，WAL 文件长期被占用；这也是 P2-9 里「文件无法改名」的直接原因（`WinError 32`）。

**修复**：`connect()` 改为 `@contextlib.contextmanager`：正常退出 `commit()`、异常 `rollback()`、`finally: close()`；建连移到 `_open_connection()`。改名逻辑补 `gc.collect()` + 3 次重试，并清掉异常对象的 `__traceback__`（失败帧里的连接句柄会让文件继续被锁）。

### 13. CLI 指定的媒体目录会被 config.yaml 里 `id: default` 的源静默顶掉

`tiklocal/config.py:37-38`（修复前）：

```python
if media_root and all(item.get('id') != 'default' for item in sources):
    sources.insert(0, {'id': 'default', 'name': 'Default', 'path': str(media_root)})
```

复现：config.yaml 写 `media_sources: [{id: default, path: D:/cfg/default}]`，再跑 `tiklocal serve E:\newlib --port 18002` → 实际 `media_root=D:/cfg/default`，CLI 目录完全消失，连「目录不存在就 exit 1」的校验都被绕过。README 示例正是 `id: default`，按示例配置的用户此后所有命令行路径都失效。

**修复**：显式目录存在时**替换**该源的 path（找不到才插入），`config.py:37-48`。

### 14. 端口被占用时先打印 URL 和初始密码，再裸 traceback 退出

`tiklocal/run.py:332`（先打印）、`:369`（后绑定）。

复现：先占住 `0.0.0.0:18099`，再启动 → stdout 已输出 `Open: http://0.0.0.0:18099` 与初始密码横幅，随后 stderr `PermissionError: [WinError 10013]`；用户会去查防火墙，而真实原因是端口占用，新生成的密码已被丢弃。

**修复**：改用 `waitress.server.create_server()` 先绑定、再打印横幅；`OSError` → 「cannot listen on host:port ... retry with --port <free port>」并 `exit 1`。

---

## P3

### 15. `auth` 子命令在 `TIKLOCAL_INSTANCE` 指向文件时抛裸 `FileExistsError`

`tiklocal/run.py:227` 直接 `AuthStore(get_auth_path())`，绕过第 03 轮给 serve/thumbs 加的 `resolve_data_dir()` 校验。

**修复**：`main()` 解析完子命令后统一调用 `resolve_data_dir(parser)`，四个子命令行为一致。

### 16. config.yaml 的值不做类型校验：裸 `TypeError`，`port: true` 静默变成 1

`tiklocal/run.py:287`（`Path(media_root)`）、`:331`（`':' in host`）、`:270`（`int(raw_port)`）。

复现：`media_root: 123` → `TypeError: expected str, bytes or os.PathLike object`；`host: 123` → `TypeError: argument of type 'int' is not a container`；`port: true` → `int(True)==1` 通过 1..65535 校验。

**修复**：新增 `validate_config_types()`：`media_root`/`host`/`name`/`download_source` 必须是字符串，`port` 必须是整数且排除 `bool`，错误写明字段名与实际类型并 `exit 2`。

### 17. `thumbs`/`dedupe` 不展开 `~`，而 `serve` 会

`tiklocal/run.py:181`、`:197`（修复前 `Path(media_root)`）。

复现：`tiklocal thumbs "~"` → 「Media directory is unavailable: ~」，而 `serve "~"` 正常。

**修复**：两处统一 `Path(media_root).expanduser()`。

### 18. 详情页/图片页 404 回的是英文纯文本

`tiklocal/web/media.py`（`detail_view`/`image_view`）直接返回 `"File not found", 404`，中文界面下透出英文。

**修复**：`detail_view` → `render_template('error.html', status=404, message=t('detail.error.file_not_found'))`；`image_view` 缺参 400 / 缺文件 404 同样走 `error.html` + `image.error.missing_uri` / `image.error.file_not_found`。

### 19. 删除接口对 `OSError` 一律回 500 并回显原始异常文本

`tiklocal/web/media.py:157-160`（修复前 `return {'error': str(e)}, 500`）。

**修复**：`WinError 32/33`、`EACCES/EBUSY/EPERM` → 409 + `detail.error.file_in_use`，其余 → 500 + `detail.error.delete_failed`；不再把 `str(e)`（含绝对路径）送到浏览器。

---

## 决策与 backlog

- **注销不吊销服务端会话**（P2，转 backlog）：`auth.py` 的签名 cookie 没有会话表，吊销需要 token family / session 存储 + 迁移；前端已清 cookie 并跳登录，风险限于「旧 cookie 在有效期内仍可用」。改动面覆盖登录/配对/CSRF 三处，需单独一轮设计。
- **登录限流按 IP 单桶**（P2，转 backlog）：单机部署下按 IP 桶足够；改成按用户 + 指数退避需要先引入失败计数存储。当前已有 429 + `Retry-After`。

## 验证

- 新增 `tests/test_round08_regressions.py`（7 个用例）：数据库损坏隔离后仍能启动、删源后索引行被清理、集合卡片计数随删除下降、永久删除清掉收藏与集合引用、恢复时索引失败仍返回 200 且 `indexed: false`、显式目录替换配置 default 源、config 类型错误 `exit 2`。
- 全量 `pytest -q` = **419 passed / 6 skipped / 0 failed**（145.76s）。
- 实测：DB 写成垃圾后启动 → 生成 `tiklocal.sqlite3.<时间戳>.corrupt`，`/api/library/items` 正常返回；`thumbs "~"` 正常；端口占用时只输出一行错误并 `exit 1`（不再打印 URL 与密码）。
