# 第 25 轮 R3 域：媒体服务与缩略图

| ID | 级别 | 位置 | 一句话结论 |
|---|---|---|---|
| R3-1 | P1 | `tiklocal/services/thumbnail.py:613-648` | ≥48MP 非 JPEG 冷缓存先全尺寸解码，4 路并发峰值 2.9GB 可 OOM |
| R3-2 | P1 | `tiklocal/web/media.py:373-377` | If-Range 请求绕过 file_wrapper，暂停的视频把 worker 钉死 120s |
| R3-3 | P2 | `tiklocal/services/thumbnail.py:353-390` | 缩略图缓存无条目上限与淘汰，外部删除的媒体缩略图永不回收，可涨到数 GB |
| R3-4 | P2 | `tiklocal/services/media_info.py:27-33` | exif_transpose 强制整图解码并复制，60MP 图峰值 360MB 且不占解码槽位 |
| R3-5 | P2 | `tiklocal/web/media.py:531-543` | `/thumb` 先 read_bytes 再判 304，no-cache 下每次条件请求都白读整份缩略图 |
| R3-6 | P2 | `tiklocal/web/media.py:287-288` | 每次 GET /api/trash 把回收站目录全量扫两遍，2000 条约占线程 1-2s |
| R3-7 | P2 | `tiklocal/services/trash.py:88-104` | manifest 缺 deleted_at 时抛 KeyError，建 app 即崩、服务起不来 |

P3：1 条（详见对话）

## 已排除

- `/media` 纯 Range（无 If-*）与无 Range 的 200 都是真流式：`wsgi.file_wrapper` → waitress `ReadOnlyFileBasedBuffer`，`waitress/task.py:439-454` 交给 channel 异步 flush，不占请求线程。
- `_BoundedReader` / `_iter_reader`（`media.py:91-129`）有 `finally: handle.close()`，客户端断开时 waitress 调 `app_iter.close()` 触发，无句柄泄漏。
- `_GENERATE_SLOTS`、`_locks`（固定 8 个）、`_inflight`（leader 在 finally 里 pop）、`_TEMP_COUNTER` 均有界，不随文件数单调增长（`thumbnail.py:61/167/292-296/416`）。
- `tiklocal/web/media_payloads.py` 全文纯字符串与日期拼接，无 IO、无缓存、无资源生命周期，整文件无问题。
- `/thumb` 单次 `read_bytes()` ≤ ~200KB（640px JPEG 含 ICC），60 格约 12MB 峰值，不构成 RSS 问题——只计 R3-5 的读放大。
- `trash._delete_folder`（`trash.py:78-86`）先删 payload 再删 manifest、最后 rmtree，中途失败只留可被 `_read` 忽略的半删条目，不静默丢数据。
