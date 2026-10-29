# 第 25 轮 R1 域：索引 / 数据库 / 搜索

| ID | 级别 | 位置 | 一句话结论 |
|---|---|---|---|
| R1-1 | P2 | `tiklocal/services/library_index.py:1105-1111` | `Executor.map` 先建满 19k 个 Future，与 records、time_states 同刻存活，19k 库冷扫峰值 55-60 MB |
| R1-2 | P2 | `tiklocal/services/database.py:523,534` | 活动事件的 `metadata`/`uri` 未截断（其余字段都截），8 MB 单请求 × 2 万行保留，库文件可涨到数 GB |
| R1-3 | P2 | `tiklocal/services/database.py:631-684` | `media_affinity` 每 uri 一行且只增不减，重下载/改名累积孤儿行，10 万行约 20 MB |

P3：3 条（详见对话）

## 已排除

- `AppDatabase.connect()` 是 `@contextmanager` 且 `finally: conn.close()`（`database.py:400-417`），全包无 `with conn:` 误用，每请求句柄都释放、WAL 能正常 checkpoint。
- 全包 `lru_cache|functools.cache` 0 命中；跨请求可变状态只有 probe 的 `_backoff`/`_warned`（已登记）与 init 定长的 `LibraryService._resolved_roots`，无隐性缓存增长。
- 读路径结果集全部有界：`page()` `safe_limit=max(1,min(limit,96))` + `LIMIT ? OFFSET ?`，`web/search.py` limit≤48、q≤200，`_read_page_options` limit≤96。
- SQL 变量上限安全：`profiles_for`/`positions_for`/`existing_uris` 均按 500 分块，`unprobed` 的 exclude 截断 400，不会撞 999 变量上限。
- `replace_snapshot` 的 19k 次 upsert 在单个 `connect()` 事务内（单次 commit），`media_index_seen` 为 TEMP 表随连接关闭释放；probe 空闲时阻塞在 `_wake.wait()` 不自旋。
