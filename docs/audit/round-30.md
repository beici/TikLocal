# 第 30 轮审查报告：性能与规模

日期：2026-10-29
基线：`audit-round-30-start`（第 29 轮收尾，`606 passed / 8 skipped`）
视角：**性能与规模**——一万条、十万条时还对不对；每请求/每扫描的 CPU、内存、IO 与语句数是否随库增长而线性放大。

## 一、方法

1. 四个只读子代理分域审查（teammate 名额已满，改用 `subagent`）：K1 索引与 SQL、K2 JSON 存储与服务层、K3 缩略图与媒体探测、K4 前端渲染与 DOM。
2. 主审（K5）自查渲染路径与索引形状，并对可疑点做**实测**（`Path.resolve()` 计时、`image_random` 分页计时）。
3. 所有"性能问题"都要求给出**放大因子**（每次操作多少次 stat/语句/字节）与**触发频率**，只报能在真实规模下感知的项。
4. 只读审查不跑服务、不改文件；修复集中在"同一请求内重复做同一件事"这类低风险改动。

## 二、五域汇总

| 域 | 范围 | P0 | P1 | P2 | P3 |
| --- | --- | --- | --- | --- | --- |
| K1 | `library_index`/`database` 的 SQL 与索引 | 1 | 6 | 7 | 4 |
| K2 | JSON 存储与服务层（收藏/合集/电台/下载） | 2 | 3 | 5 | 6 |
| K3 | 缩略图、媒体探测、`/thumb` | 0 | 2 | 7 | 3 |
| K4 | 前端渲染、DOM、监听器 | 0 | 2 | 3 | 4 |
| K5 | 主审自查（渲染路径、索引形状、实测） | 0 | 0 | 0 | 1 |

去重后本轮共 **52 条**：P0 3、P1 13、P2 22、P3 17（K5 的 `resolve()` 发现与 K2 的 P0-1 合并）。

## 三、P0 与处置

**K1-P0 `records()` 无 LIMIT 的全表 `SELECT *` + `fetchall`**
`tiklocal/services/library_index.py:285-298` 的 SQL 没有任何上限，调用点是 tag feed（`web/flow.py:342`）、深链 caption feed（`:391`/`:395`）、作者页（`web/author.py:114`）与电台（`services/radio.py:205`）。8k 库时代码注释自己记的是 400ms，线性外推到 10 万条是单请求数秒、峰值 100–250MB。
处置：**转 backlog（本轮最大遗留）**。正确修法是 keyset 分页（`after_uri`）而不是简单加 LIMIT——tag feed 需要"取前 N 条"，深链 caption feed 需要"取到目标为止"，两者的语义不同，改错会让 feed 少条目。留给第 31 轮作为主线。

**K2-P0-1 电台每首候选做两次 `resolve()`**
`services/radio.py:205-211` 对每个音频 `resolve_path()`（内部 `resolve_target_path` 有 `(source.path/rel).resolve()` 与 `source.path.resolve()` 两次 realpath），随后又 `is_file()` 一次；`records(media_type='audio')` 本身是全表。实测 `Path.resolve()` 在本机 **275µs/文件**、`root.resolve()` **242µs/次** → 1 万首 ≈ **7.9s/次**，而 `collect_candidates()` 在开台、切台、队列见底 refill（约每 9 首一次）都会被调用。
处置：**已修**。`LibraryService` 现在缓存解析后的根（`_resolved_root_by_id`），`resolve_target_path()` 不再每次重算 `source.path.resolve()`；新增 `existing_media_path()` 做"只要确认还是文件"的廉价探测（词法拼接 + 一次 `is_file()`，不做 realpath），`radio.collect_candidates()` 改用它。安全边界不变：真正发文件的 `/media`、`/thumb`、`/detail` 仍走 `resolve_path()` 的 resolve 校验，符号链接逃逸照样被拒。

**K2-P0-2 feed 每页按全库规模建 dict、且每页读三次 `favorites.json`**
`services/recommendation.py:64-71`、`:101`、`:239`：一次 `/api/feed/mix` 会为 video/image 各建一份 N 条 `{'uri','mtime'}` 列表、再建一份 N 条 `weighted_pool`，并三次 `favorites.load()`（每次都整份解析 + 全量 canonicalize），`POOL_CAP=1500` 的裁剪发生在建池之后。
处置：**转 backlog**。修法（候选池只存 tuple、favorites 提到请求级解析一次）需要动 feed 的选择算法，风险高于本轮批次；已记入 backlog 并标注"与 K2-P1-1 同一根因：JSON 服务无请求级缓存"。

## 四、P1 与处置

| 编号 | 位置 | 问题 | 处置 |
| --- | --- | --- | --- |
| K1-P1-1 | `library_index.py:543` | 每页都 `COUNT(*)`，与取行是两条独立语句 | 转 backlog（需改 `total` 语义） |
| K1-P1-2 | `library_index.py:796-809` | 搜索条件 `(album_key IS NULL OR album_index = 0)` + `LIKE '%term%'` 双重不可索引 | 转 backlog（需 FTS 或前缀索引） |
| K1-P1-3 | `library_index.py:574-623` | `/library` 每次一次全表 + 一次全索引 + 8 次整月排序 | 转 backlog |
| K1-P1-4 | `library_index.py:42-145` | 索引重建单事务 2N 条语句 + 4×S 次孤儿全扫，10 万文件 20–60s | 转 backlog（分块提交 + 孤儿清扫移出） |
| K1-P1-5 | `library_index.py:522-524` | ORDER BY 缺第三列/无 `size_bytes` 索引 → 整分区排序 | **已修**（migration 016） |
| K1-P1-6 | `embedding.py:126-152` | `media_vectors`/`video_frames` 在请求路径无界读 BLOB | 转 backlog |
| K2-P1-1 | `collections.py:104-148` | `/library` 每次渲染为每个集合的每个 item 建 dict，随后全部丢弃 | **已修** |
| K2-P1-2 | `radio.py:146` | 音频元数据缓存无上限、无淘汰 | **已修** |
| K2-P1-3 | `downloader.py:603` | `download_payload` 无尺寸上限且全量 `json.dumps` 在锁内 | 转 backlog |
| K3-P1-1 | `thumbnail.py:549` | `/thumb` 在请求线程排队生成，槽位无超时 | 转 backlog（要改占位图契约） |
| K3-P1-2 | `app.py:466` | 启动期同步全量扫描，零变化也重写全部行 | 转 backlog（后台扫描 + 条件更新） |
| K4-P1-1 | `library_page_controller.js:399-411` | 宽度变化 1px 即清空 grid 重建全部卡片 | 转 backlog（复用节点重排） |
| K4-P1-2 | `library_page_controller.js:501-521` | `?focus=` 深链逐页串行拉全库 | 转 backlog（链接带 offset） |

## 五、已修的 P2

1. **K2-P2-1/P2-2 收藏读取**：`is_favorite()` 原先是"为一个布尔值读整份 `favorites.json` 并把全部条目 canonicalize 一遍"，库页每张卡片问一次。新增按 `(mtime_ns, size)` 失效的缓存；`save()` 也不再对每个条目重复 `_normalize`（`load()` 已规范化过）。
2. **K2-P2-4 合集摘要**：`_collection_copy(include_items=True)` 原先对已规范化的 items 再规范化一次，读路径成本翻倍；现在直接浅拷贝。
3. **K3-P2-2 `/thumb` 的 304**：原来先 `read_bytes()` 再判条件，每个 304 都白读一张缩略图；现在先按 `(mtime_ns, size)` 算 ETag 命中就直接 304，只有真要发正文才读盘。
4. **K4-P2-8 视频海报解码**：`library_page_controller.js` 的视频分支补 `img.decoding = 'async'`（图片分支早已有）。

## 六、实现要点

- **索引（migration 016）**：`idx_media_items_type_mtime_uri(media_type, mtime DESC, uri)` 与 `idx_media_items_size(size_bytes DESC, mtime DESC, uri)`。前者补上 `ORDER BY mtime DESC, uri` 的第三列，后者让「大文件」视图不再整库排序。迁移用 `CREATE INDEX IF NOT EXISTS`，老库首次启动自动补建。
- **电台候选**：`existing_media_path()` 只做词法拼接 + `is_file()`，注释写明"真正的 resolve 校验留在发文件路径上"，避免以后有人把它当成安全边界使用。
- **电台元数据**：`OrderedDict` + `move_to_end` + 上限 `METADATA_CACHE_LIMIT = 2000`；命中时也 `move_to_end`，否则 LRU 退化成 FIFO。
- **合集读取**：`_read(normalize_items=False)` 只统计 URI 与封面，不建 item dict；`list()` 用它，`get()`/写路径仍走默认 `True`。`_normalize_collection` 用 `seen` 集合同时算 `item_count` 与封面兜底，避免两条路径算出不同结果。
- **收藏缓存**：写操作后置 `_cache_stamp = None`，不依赖 mtime 粒度；返回的 set 标注为只读。

## 七、验证

- 相关 5 个测试文件：`63 passed, 1 skipped`（`test_collections`、`test_radio`、`test_thumbnails`、`test_media_state`、`test_library_upgrade`）。
- 全量：见 README「每轮收尾基线」。
- 实测（临时脚本，未入库）：`Path.resolve()` 275µs/文件、`root.resolve()` 242µs/次；`image_random` 分页 2000 行 17ms（说明分页本身不慢，慢的是全表取行）。

## 八、覆盖率缺口

- 没有 1 万/10 万规模的夹具：所有"线性放大"结论都是按实测单次成本外推的，没有端到端压测。第 31 轮若修 `records()`，应同时生成一个可复现的大库夹具（脚本生成空文件即可，不需要真实媒体）。
- 前端性能只有静态阅读，没有 DevTools trace；K4 的 resize 重建与 `?focus=` 串行拉取都需要真实浏览器才能量化。
- SQLite 查询计划是按 DDL + SQL 形状推断的，没有 `EXPLAIN QUERY PLAN` 实测输出。

## 九、下轮建议

**第 31 轮视角：查询与分页的正确性 + 大规模路径的端到端验证。** 主线是把本轮最大的遗留（K1-P0 `records()` 无上限、K1-P1 每页 `COUNT(*)`、K1-P1 搜索不可索引）一次做对，并配一个生成 10 万行索引的测试夹具来锁住回归。次线是 K3-P1-2（启动期同步全量扫描）与 K4-P1-1（resize 全量重建），两者都是"用户每次都能感知"的卡顿。
