# 第 32 轮审查报告：时间与时区

日期：2026-10-29
起点基线：`5b1177a`，`pytest -q` = **610 passed / 8 skipped / 0 failed**
本轮视角：**时间与时区**——拍摄时间从哪来、换机器/换时区后还对不对、展示层的"多少年前"和日期边界是否自洽。

## 一、方法与范围

五域并行（teammate 名额已满，改用只读 `subagent`）：

| 域 | 覆盖 |
|----|------|
| M1 | 拍摄时间抽取（`library_index.py` 的 EXIF/文件名/mtime 三级回退、`tiktok_downloader.py` 的 slug 生成、sidecar 字段名） |
| M2 | 时间缓存（`_capture_payload`、`time_states`、`unchanged` 判据、回忆卡选片） |
| M3 | 展示层（首页 on-this-day、时间线月份、详情页日期、推荐流时间窗） |
| M4 | 时区与 DST（容器 TZ、naive/aware 混用、跨时区迁移、复数文案） |
| M5 | 主审自查（未来时间钳制、回收站保留期、缩略图本地时间、设备版本号） |

## 二、汇总

| 域 | P0 | P1 | P2 | P3 | 说明 |
|----|----|----|----|----|------|
| M1 拍摄时间抽取 | 0 | 2 | 3 | 4 | EXIF 子 IFD 从未被读；sidecar 时间字段名不匹配 → 全部落 1970 |
| M2 时间缓存 | 0 | 0 | 3 | 3 | 墙钟与 epoch 都由扫描时区决定，换时区后永不重算 |
| M3 展示层 | 0 | 0 | 3 | 5 | 「1970 年」幽灵章节；`years_ago` 用日历年差 |
| M4 时区与 DST | 0 | 0 | 1 | 4 | 在途补丁自身残留两处（0 年前、链接月份） |
| M5 主审自查 | 0 | 0 | 1 | 4 | 未来 mtime 不钳制 → 时间线最前 |
| **合计** | **0** | **2** | **11** | **20** | 33 条，去重后 33 条；修复 13 条（P1 2 + P2 8 + P3 3），转 backlog 20 条 |

## 三、P0 / P1 详述

### M1-1（P1）EXIF 的 DateTimeOriginal 从未被读到

`_image_capture_time()` 用 `exif.get(36867)` 取拍摄时间，但 Pillow 的 `Exif.get()` 只读 IFD0，而 `DateTimeOriginal`/`DateTimeDigitized` 位于 Exif 子 IFD（指针 0x8769）。结果是永远取不到，回退到 IFD0 的 306（文件修改时间），**并且仍标 `high` 置信度**：导出或编辑过的照片会整批以"高置信度"落进导出那个月，用户在时间线上看到的月份与拍摄月份无关。

修复：先读 `exif.get_ifd(0x8769)` 取 36867/36868（`high`），再回退 IFD0 同名标签（`medium`），最后 306（`low`）。`TIME_METADATA_VERSION` 提到 **2**，让旧行全部重算。

### M1-2（P1）下载作品的创建时间字段名不匹配，全部落进 1970

`tiktok_downloader.py` 读 `create_timestamp`，而 sidecar 实际写的是 `create_time`（`_WORK_ITEM_KEYS` 里也没有前者）→ `format_timestamp_slug(None)` 回落字面量 `1970-01-01 00.00.00`，索引器又按文件名采信 → 所有下载作品堆进 1970 章节。

修复：`normalize_work_items` 兼容 `create_time`/`createTimestamp`；`format_timestamp_slug` 支持毫秒归一、把 `time.localtime` 放进 `try`、无时间返回 `''`；`build_work_filename` 无日期时不写前缀。

## 四、P2 详述（本轮已修）

- **M2-1 / M2-2**：`captured_local_date` 是 naive 墙钟、`captured_at` 是 `.timestamp()`，两者都由**扫描时**的服务器时区决定；`time_states()` 不存时区，`unchanged` 只看 size/mtime/version，所以换时区后旧行永不重算。判据还是 `>= 1`，版本号退化成布尔，升版本也强制不了重算。→ 新增 `_capture_is_current()`：版本 `==` 常量、`captured_local_date` 非空、`captured_at<=0` 时信任、否则用 `fromtimestamp(captured_at)` 回推日期比对。
- **M2-3**：回忆卡按 `mtime_ts` 选片/分组/写文案，而 on-this-day 与时间线按拍摄时间 → 同一张图两个入口矛盾。→ 新增 `_capture_epoch(record)`（优先 `captured_at`，回退 `mtime_ts`），`build_memory_card` 与 `old_images` 全部改用它。
- **M3-1 / M3-2 / M3-3**：容器未设 TZ、`on_days`/`timeline_months` 无 1970 下界、「1970 年」章节、`years_ago` 用日历年差（元旦前后把两天前标成"1 年前"）。→ 查询加 `captured_at > 0` 与 `HAVING year <> ''`/`month_key <> ''`；`years_ago` 改真实天数并新增 `days_ago`/`date` 字段；前端磁贴与标题都按 `>= 1` 选文案。
- **M4-1**：在途补丁把 `years_ago` 改成可等于 0，磁贴侧兜住了、标题侧漏了 → 「12月30日 · 0 年前」。→ 标题同样 `>= 1 ? agoLabel : dayLabel`。
- **M5-1**：未来 mtime（时钟错乱/NAS 时间跳变）不钳制 → 时间线最前与「未来」章节。→ `_capture_payload` 新增 `_clamp_future()`，超出"现在 + 5 分钟"按 now 存。

## 五、P2/P3 未修（转 backlog）

| 编号 | 位置 | 问题 | 级别 |
|----|----|----|----|
| M1-4 | `library_index.py` 1970 下界 | 行内 `captured_local_date='1970-01-01'` 与 `captured_at>0` 自相矛盾 | P2 |
| M1-5 | `media_info.py:45-68` | 视频分支完全不读 ffprobe `creation_time`，相机视频只能落 mtime | P2 |
| M1-6 | `_image_capture_time` | 不读 `OffsetTimeOriginal`(36881)，跨时区拍摄的墙钟偏移丢失 | P3 |
| M1-7 | 同上 | 只有日期没有时刻时默认 12:00，与"当天 00:00"语义不一致 | P3 |
| M1-8 | `tiktok_downloader.py` | 文件名时间分隔符不含点，`2024-01-01.12.00.00` 解析不到 | P3 |
| M2-4 | `media_payloads.py:25` | 只屏蔽 `'1970-01-01'` 字符串，UTC 以西会漏出 `1969-12-31` | P3 |
| M2-5 | `library_index.py:763` | `captured_at or mtime` 把 0.0 换成负 mtime | P3 |
| M3-4 | 月视图 vs 网格 | 月视图按 `captured_at`、网格按 mtime，两套语义 | P2 |
| M3-5 | `_capture_payload` | EXIF 墙钟当本地时间转 epoch，跨时区导入会整体偏移 | P3 |
| M3-6 | `recommendation.py` | 时间窗用 365 天近似，闰年/DST 边界偏移一天 | P3 |
| M5-2 | `trash.py:96-100` | 保留期按字符串比较 `deleted_at`，混入非 UTC 格式会误判 | P3 |
| M5-3 | `thumbnail.py` | 指纹用 `datetime.fromtimestamp` 本地时间，换时区后全部失效重算 | P3 |
| M5-4 | `device_auth.py` | `_derive_device_name` 的版本号只写不读 | P3 |
| M5-5 | `run.py` | `TIKLOCAL_TIMEZONE` 未设置时依赖宿主 TZ，容器里是 UTC | P3 |

（M4-2/M4-3/M4-4/M4-5 与 M3-7 当轮已修：链接月份、`1 day left`、`1 minute`、DST 不存在时刻的缓存收敛、详情页 `post_date_of`。）

## 六、实现要点

- `_capture_is_current()` 只比**日期**而非到秒：春季跳变里不存在的墙钟会被 `timestamp()` 归一化，严格比较会让该行每次扫描都判过期、永久重算。
- `TIME_METADATA_VERSION` 用 `==` 而不是 `>=`：`>=` 把版本退化成布尔，升版本永远无法失效旧缓存。
- 未来时间钳制只影响 `captured_at`，不动 `captured_local_date` 的原始墙钟——展示仍显示相机写的时间，只有排序与章节归属被拉回。
- `_capture_epoch()` 优先 `captured_at` 而不是 `mtime_ts`，让回忆卡与时间线/on-this-day 用同一套时间。

## 七、验证

- 相关 4 个测试文件：**78 passed / 1 skipped in 20.46s**（`test_library_upgrade`、`test_tiktok_downloader`、`test_round15_regressions`、`test_web_runtime`）。
- i18n 与登录：**35 passed in 16.28s**（`test_i18n`、`test_auth`）。
- 全量 `pytest -q`：见 README 收尾基线。

## 八、覆盖率缺口

- 没有"换时区后重扫"的端到端用例：`_capture_is_current` 只有单测，真实迁移（`TZ` 变化 + 既有 10 万行）未跑过。
- DST 不存在时刻只有逻辑推演，没有用 `zoneinfo` 构造真实跳变时刻的用例。
- EXIF 子 IFD 的用例只覆盖 `DateTimeOriginal`，`DateTimeDigitized` 与 `OffsetTimeOriginal` 未覆盖。

## 九、下一轮建议

**并发写入与幂等**：同一文件被下载器与手动导入同时登记、`replace_snapshot` 与删除并发、`favorites`/`collections` 的读改写与 CLI 交叉。本轮已经看到 `_capture_is_current` 这类"缓存判据"是最容易留死角的地方，下一轮可以把所有"判据"（版本号、mtime、size、hash）列成表逐个验证。
