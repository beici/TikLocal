# 第 31 轮审查报告：规模下的读取路径

日期：2026-10-29
起点基线：`6ff0d12`，`pytest -q` = **606 passed / 8 skipped / 0 failed**
本轮视角：**一万条、十万条时读取路径还对不对、多快**（索引是否被用上、每页是否重复算同一件事、可达集是否被硬截断）

## 一、方法与范围

五域并行（teammate 名额已满，改用只读 `subagent`）：

| 域 | 覆盖 |
|----|------|
| L1 | `library_index.py` 的读方法、22 个调用点、迁移与索引、`web/{flow,library,media}.py` 的读取路径 |
| L2 | 推荐与混合流（`recommendation.py`、`flow.py` 的 mix 分支、memory 卡、深链起始流） |
| L3 | 搜索与聚合（`web/search.py`、时间线、图集分页、跨域统计） |
| L4 | 数据库索引与迁移（`database.py` 全部迁移、`EXPLAIN QUERY PLAN`、写锁与并发） |
| L5 | 主审自查（图集随机序、`records()` 调用点、`_to_library_record` 成本） |

测量方法：用真实 `AppDatabase.migrate()` 建 schema，灌 2 万～10 万行合成 `media_items`（40% video / 50% image / 10% audio，mtime 跨三年），按生产状态跑一次 `ANALYZE`，每条查询取 `EXPLAIN QUERY PLAN` + min-of-3 计时；另用微基准测 `Path.resolve()`（本机 **275 µs/文件**）。

## 二、汇总

| 域 | P0 | P1 | P2 | P3 | 说明 |
|----|----|----|----|----|------|
| L1 索引读取与分页 | 0 | 0 | 2 | 1 | 默认页缺统计信息 → 整库临时 B 树；blog 过滤无索引 |
| L2 推荐与混合流 | 0 | 2 | 2 | 1 | 可达集被 1500 硬截断；每页重算同一序列 |
| L3 搜索与聚合 | 0 | 0 | 3 | 2 | 视频 tab 不过滤类型；offset 截断后无限重复 |
| L4 数据库索引与迁移 | 0 | 2 | 4 | 4 | blog/大文件回表；`replace_snapshot` 写锁 3.6–4.8 s |
| L5 主审自查 | 0 | 0 | 1 | 0 | `image_random` 整表取行再洗牌 |
| **合计** | **0** | **4** | **12** | **8** | 24 条，去重后 24 条；修复 10 条（P1 4 + P2 6），转 backlog 14 条 |

## 三、P0 / P1 详述

### L2-1（P1）默认 Feed 的可达集被 1500/类型硬截断

`tiklocal/services/recommendation.py:140-141` 先用 `rng.sample(pool, POOL_CAP=1500)` 采样，尾段又**只从采样池**取。库大于 3000 条时第 2 页就 `has_more=false`：实测 1 万条的库里 **83% 的文件永远不会出现**，与「无限下滑」的产品预期直接冲突。

修法：尾段改从 `weighted_pool`（全量候选）减去已选项再按权重键排序，采样池只用于多样性挑选。

### L2-2（P1）每页重算一段与页码无关的确定性序列

`flow.py` 每页用 `limit=end+1` 调 `select()` 两次；序列只由 `seed` 决定，因此第 10 页（`end=240`）要把前 240 个多样性槽位重挑一遍、再把整个池重新加权排序：1 万条库实测 **684 ms/页**，10 万条 0.7–1.2 s。

修法：`flow.py` 新增 `_selection_for()`，按「选择器名 + 类型 + seed + blog + session」缓存有序列表，容量按 2 的幂增长（32→64→…），命中且长度够就切片；TTL 10 s、LRU 8。深页从「重排」变成「切片」。

### L4-1（P1）blog 过滤与每源封面每次都做 10 万行回表

`blog_rows()` 的 `GROUP BY source_id` 走 `idx_media_items_source_parent` + 临时 B 树，每源封面查询与 `COUNT` 走 `idx_media_items_source_mtime_uri` 但该索引不含 `media_type` → 3.3 万次回表。10 万行实测 `blog_rows()` **112–116 ms**、`page(source_id=…)` **46 ms**（COUNT 36.2 ms），而 `_blog_chips()` 在每次 `/library` 渲染里无条件调用。

修法：给 017 的 `idx_media_items_source_mtime_uri` 补尾列 `media_type`，查询变 `COVERING INDEX`：112 → **23.3 ms**、46 → **9.2 ms**、COUNT 36.2 → 4.0 ms。

### L4-2（P1）「大文件」页每次 455 ms，几乎全是 COUNT 回表

`page(mode='big_files')` 的 `COUNT(*) WHERE media_type='video' AND size_bytes >= ?` 走 `idx_media_items_size`（不含 `media_type`），每个 ≥10 MB 的行都回表判类型：10 万行 COUNT **451.7 ms**、整页 **455.4 ms**。

修法：`idx_media_items_size_order(size_bytes DESC, mtime DESC, uri, media_type)`，COUNT 451.7 → **6.9 ms**，整页 → **14.6 ms**。

## 四、本轮修复的 P2

| 编号 | 位置 | 问题 | 修法 |
|------|------|------|------|
| L1-1 | `library_index.py` `page()` | 无统计信息时 `media_type IN (3)` 无法用 `(media_type, mtime, uri)` 满足 ORDER BY，10 万行 22.1 ms/页并建整库临时 B 树（≈90 MB） | 迁移 017 加 `capture_order(capture_month, captured_at DESC, uri)` + `ANALYZE`；sync 有变化时再 `ANALYZE`。20k 行实测 0.06 ms |
| L1-2 | 同上 + `blog_rows()` | `source_id` 过滤没有任何索引 | 同迁移加 `source_mtime_uri`（见 L4-1） |
| L3-1 | `web/search.py:88` | `_works_page` 不传 `media_type`，「视频」tab 首屏 24 张里约 19 张是图片/相册，`counts['videos']` 也算进图片 | 透传 `media_type='video'` |
| L3-3 | `web/search.py` `api_search` | `offset` 被 `read_int_arg(maximum=MAX_OFFSET)` 截断后，越界请求永远返回最后一页且 `has_more` 恒真 | 先读原始 offset，越界时返回空页 + `truncated: true` + `has_more: false` |
| L4-4 | `library_index.py:465,477` | 「那年今日」用 `substr(captured_local_date,6,5) IN (...)`，全表扫 46–55 ms，首页每次渲染 | 迁移 017 加表达式索引 `substr(captured_local_date,6,5)`：46 → **7 ms** |
| L5-1 | `library_index.py` `page(image_random)` | 整表取行再 `shuffle`，10 万行 **1028 ms / 89.6 MB**，只为取 24 行 | 只取 `uri` 列洗牌，再按本页 24 个 uri 回表 |

## 五、未修的 P2（转 backlog）

- **L2-3** memory 卡退化：`flow.py:614` 只在 `page % 2 == 1` 且只从本页记录里挑「一年前的图片」，深页时命中率趋近 0（实测约每 334 页才出现一次）。
- **L2-4** 深链/blog 起始流每页对整条时间线做无上限 `collapse_media_entries`：10 万图集库单页 **2.6 s / 52 MB**。
- **L3-2** 时间线 year_rows 两次 `GROUP BY` + 临时 B 树，10 万行 **159 ms**，每次 `/library` 都跑。
- **L4-3** `timeline_months()` 的 year_rows 全表扫 + 临时 B 树 **66 ms**；加 `(capture_year, media_type, mtime)` 覆盖索引可降到 17.7 ms，但每个新索引让 `replace_snapshot` 写锁再涨 5–8%，本轮先不加。
- **L4-5** `media_vectors` 缺 `(model, mtime)` 索引，`vectors()` 把 90 MB 向量 BLOB 塞进临时 B 树：**902 ms**；加索引后 302 ms。
- **L4-6** `replace_snapshot` 单事务 10 万条持写锁 **3.6–4.8 s**，撞上 activity 写入的 3×5 s 重试 → 请求挂 ~15 s 后 500。修法是分批提交，属结构性改动。

## 六、P3（全部转 backlog）

L1-3 收藏页每页绕过 `favorites` 的 mtime 缓存（`web/library.py:205` 调 `load()` 而非 `_load_cached()`）；L2-5 位置切片式翻页对库变化敏感（滚动中新增文件 → 跨页重复 1 条并被静默丢弃）；L3-4 多词搜索是「所有词都命中」而不是 OR；L3-5 `photo_groups` 的 total 含死行；L4-7 `records()`/`time_states()` 是无 WHERE 无 LIMIT 的全表读（10 万行 **862 ms** / ≈150 MB）；L4-8 `_migrate_015` 是唯一不检查列是否存在的 `ADD COLUMN`，失败后每次启动都失败；L4-9 `_sweep_orphan_state` 用前缀 `LIKE` 清孤儿，四张表全扫（应改范围查询）；L4-10 `idx_media_affinity_recent` 没有查询使用，纯写放大。

## 七、实现要点

1. **三个索引与 `ANALYZE` 必须同批上线**：只加 `ANALYZE` 会在 1 万条时把月视图翻成全表扫（0.14 → 6.0 ms/页），只加索引则统计信息缺失时 SQLite 仍选错索引。
2. **`ANALYZE` 放在快照事务之外**：放在 `replace_snapshot` 的 `with connect()` 里时，紧接着的 `DELETE` 在测试环境下会抛 `sqlite3.OperationalError: unable to open database file`（事务内 `ANALYZE` 改写统计表后 SQLite 需要新的临时文件句柄）。改成事务提交后用独立连接执行，并 `except sqlite3.OperationalError` 记 warning —— 统计信息缺失只会退化成慢查询，不该让一次同步失败。
3. **选择序缓存必须以选择器名为键**：`forgotten` 与 `weighted` 两个 feed 共用同一个 `seed`，只按 `(kind, seed, blog, session)` 做键会让两个 feed 互相串味。
4. **桶大小取「不小于 limit 的 2 的幂」**：`select(limit=N)` 的前 N 项对任意 `M ≥ N` 都是 `select(M)` 的前缀（挑选阶段消耗同一随机流，尾段在其后追加），所以缓存较长的一次结果就能服务所有较短的请求。
5. **`image_random` 的置换只依赖有序 uri 列表**：因此洗牌 `uri` 单列与洗牌整行得到同一个页序，回表只取本页需要的 24 行。

## 八、验证

- 聚焦测试（`test_recommendation` / `test_feed_mix` / `test_library_upgrade` / `test_search`）：**63 passed / 1 skipped**。
- 全量 `pytest -q`：**610 passed / 8 skipped / 0 failed**（较基线 +4 个用例：尾段可达性、选择序缓存前缀、搜索越界窗口、image_random 页序稳定）。
- 索引探针（2 万行、跑过 `ANALYZE`）：blog chips **5.25 ms**（covering）、blog page **0.02 ms**、blog count **1.26 ms**、big files **1.63 ms**、big page **0.04 ms**、monthday **4.66 ms**，计划分别命中 `idx_media_items_source_mtime_uri` / `idx_media_items_size_order` / `idx_media_items_monthday`。
- 测试调整：`tests/test_round12_regressions.py` 的 i18n 用例原先用 `MEDIA_ROOT=Path('.')` 建 app，每次运行都把整个仓库扫进索引，并与上一测试的后台扫描抢同一个数据目录（表现为偶发 `unable to open database file`）。该用例只需要一个请求上下文，改为 `tmp_path`。

## 九、覆盖率缺口

- L2 的 P2/P3 只做了定级，未修（深链 collapse、memory 卡退化）。
- 本轮没有前端渲染/DOM 域的覆盖，L4 是数据库域而非 UI 域。
- 10 万行下 `replace_snapshot` 的写锁只测量、未改造；`records()` 的全表读仍是多个页面的共同底座。
- 合成数据不含真实的 `search_text` 分布，`page(search=…)` 的 119 ms 只做了计划分析。

## 十、下轮建议

**并发写与后台任务生命周期**：`replace_snapshot` 的长写锁、`MediaProbeWorker` 与请求线程抢同一把锁、下载 worker 与扫描线程同时改 `media_items`、`ANALYZE`/`VACUUM` 这类维护操作该在什么时机跑。
