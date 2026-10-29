# 第 25 轮 R4 域：推荐 / 电台 / 作者 / 元数据

| ID | 级别 | 位置 | 一句话结论 |
|---|---|---|---|
| R4-1 | P1 | `tiklocal/web/flow.py:334-345` | 话题流每翻一页都重建全库记录、对每个专辑封面各查一次 `album_members`，20k 库单页约 30MB 峰值加数千次 SQL。 |
| R4-2 | P2 | `tiklocal/services/recommendation.py:79-87` | 候选缓存键取未校验的 `blog` 查询串且从不删键，进程内键空间只增不减。 |
| R4-3 | P2 | `tiklocal/services/metadata.py:15-17` | 标注元数据每次读写都整份解析并重写 JSON，20k 条约 10MB 且全程持锁串行。 |
| R4-4 | P2 | `tiklocal/services/embedded_metadata.py:25` | 只解析 JPEG 头部注释却整文件 `read_bytes`，每次打开大图详情内存峰值等于文件大小。 |
| R4-5 | P2 | `tiklocal/services/radio_energy.py:225-226` | 超 4 小时曲目提前断流致 ffmpeg 非零退出、恒返回 None，且每次请求重解 4 小时音频。 |
| R4-6 | P2 | `tiklocal/services/captions.py:430-436` | AI 标注前先全分辨率解码再缩到 1536px，大 PNG 单请求峰值可达 300MB 以上。 |

P3：3 条（详见对话）——`tiklocal/services/radio.py:258` 全缓存命中仍每请求新建 8 线程池；`tiklocal/services/radio.py:114-121` 每个播放事件重写整份 1200 条 profile；`tiklocal/web/author.py:420-444` 批量同步上限 200 × 每次 150s 串行占住请求线程。

## 已排除

- `tiklocal/services/radio_energy.py:82`：包络缓存已用 `cache.stat().st_mtime_ns < source.stat().st_mtime_ns` 校验，改文件不会命中旧值。
- `tiklocal/services/recommendation.py:20,24,140-146`：候选池与挑选量有 `POOL_CAP=1500` / `DIVERSITY_PICKS=240` 硬上限，`rng.sample` 不物化整池。
- `tiklocal/services/author_profiles.py:33,193,202-212`：头像下载有 4MB 上限、唯一临时名与 `with urlopen`，不会打爆内存也不会互相覆盖。
- `tiklocal/services/radio_energy.py:187-192`：解码超时会 `_kill_process` 且 `join(60)` 有界，ffmpeg 不会被永久钉住（残留 `stdout` 未 close 已登记）。
- `tiklocal/services/json_storage.py:26,130-152`：损坏文件走 quarantine、持久 OSError 抛 `StoreUnreadableError(OSError)`，`record()` 不会用 `{}` 覆盖真实 profile。
