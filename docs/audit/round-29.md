# 第 29 轮审查报告：可观测性与故障诊断

- 日期：2026-10-29
- 视角：**出问题时，日志和界面能不能告诉用户与运维「发生了什么、下一步做什么」**
- 基线：`audit-round-28-end`（`fab667a`）
- 方法：四个只读 subagent 分域审查（teammate 名额已满 8，改用 `subagent`）+ 一轮自查；所有发现先复现/读码确认，再按 P0→P3 定级；P0/P1 当轮必修，P2 选修 9 条，其余转 backlog。

## 一、五域汇总

| 域 | 范围 | P0 | P1 | P2 | P3 |
| --- | --- | --- | --- | --- | --- |
| J1 日志与错误信息 | `logging_setup.py`、静默 `except`、用户文案、关键路径缺失的开始/结束日志 | 0 | 2 | 6 | 5 |
| J2 前端错误呈现 | 各页 `catch` 吞错、离线/超时文案、重试入口 | 0 | 1 | 7 | 6 |
| J3 诊断端点 | 现有 `/api/*` 能回答哪些「为什么」、缺什么 | 0 | 1 | 7 | 3 |
| J4 启动自检与配置 | 启动期校验、依赖探测、配置错误是否可发现 | 0 | 0 | 4 | 5 |
| J5 自查 | 慢请求、前端未捕获异常上报 | 0 | 0 | 2 | 0 |

去重后 **P1 4 / P2 21 / P3 18**（J1-P2-1≡J4-P2-1、J1-P2-2≡J4-P2-3、J1-P3-1≡J3-P2-3 三处合并）。当轮修复 **14 条**（P1 4 + P2 9 + 顺带修掉 1 条 P3），其余 **29 条**转 backlog（P2 12 + P3 17）。

## 二、P1（4 条，全部当轮修复）

### J1-P1-1 登录/限流/CSRF/设备令牌全程无日志

`tiklocal/auth.py` 全文没有一行日志：密码错 401、限流 429、CSRF 403、`/api/v1/*` 令牌 401 全部静默。开了 `AUTH_ENABLED` 并监听 `0.0.0.0` 时**没有任何登录审计**；用户看到「尝试次数过多」时，运维无法区分暴力破解、本人手滑，还是反向代理把全家设备塞进 `127.0.0.1` 同一个桶。

修复：新增模块级 `_LOGGER`，`login_view` 三个分支各一行（429 `warning` 带剩余秒数、失败 `warning`、成功 `info`），`csrf_error_response` 与 `unauthorized_response` 各补一行（带 method/path/remote_addr）。

### J1-P1-2 删除失败的 500/409 是「静默 500」

`tiklocal/web/media.py` 的 `delete_view` 在 `except OSError` 里**返回元组而不是抛异常**，因此全局 500 处理器不会运行；`e` 之后完全没被使用。磁盘满（ENOSPC 不在 `in_use` 判定里）与权限错误都被降级成「删除失败，请重试」，日志 0 行。

修复：该分支补 `app.logger.warning(..., exc_info=e)`（带 `winerror`/`errno`），`FileNotFoundError` 分支补 `info`。

### J2-P1 首页把请求失败渲染成「空库」成功态

`home_page_controller.js` 四条请求各自 `.catch(() => 空 payload)`：任一接口 5xx/超时/返回 `{success:false}` 时，整页渲染成「这里暂时很安静。添加媒体来源…」+「等待第一份媒体」+「还没有合集」。有上万条媒体的用户被告知去添加来源——**看起来像库被清空**，且无 toast、无重试、console 无输出。

修复：`fetchJson` 补 `success === false` 判定与 15 s 超时；`init()` 改 `Promise.allSettled`，逐区块渲染成功的结果、失败区块显示顶部错误条（含服务端原因与重试按钮）；统计失败时摘要显示「暂时读不到媒体库」而不是继续「正在唤醒」。

### J3-P1 三种「文件不在」共用同一句 404

文件在回收站（可恢复）、索引有行但磁盘没有（来源离线 / 应用外被移走）、索引从未见过——三种情况都答「文件不存在」。用户不知道该去恢复、去接硬盘，还是去重扫。

修复：`web/media.py` 新增 `missing_reason(uri)`（回收站命中 → `trashed` 带 `trash_entry_id`/`deleted_at`；有索引行且来源目录不存在 → `source_offline`；有索引行 → `missing_on_disk`；否则 `not_indexed`），`/api/media/info` 的 404 体里带上 `reason` 与对应文案，`/detail`、`/image` 也按原因选文案（新增 3 个 i18n key）。保留 404 语义而不是改 200：现有前端把非 2xx 当失败处理，改状态码会连带改三处调用方，收益不成比例。

## 三、P2 修复（9 条）

1. **J5-a 慢请求日志**：`app.py` 加 `before_request`/`after_request` 计时钩子，超过 `SLOW_REQUEST_SEC = 2.0` 记 `warning`（method/path/status/耗时）。这是 J1-P2-6「生产模式无请求日志」的最小可行解：全量 access log 会被缩略图请求淹没。
2. **J5-b 前端异常上报**：新建 `tiklocal/web/client_log.py`，`POST /api/client-log` 接收 `message`/`source`/`stack`/`url`，字段截断 + 每分钟 20 条限流；`base.js` 监听 `error` 与 `unhandledrejection` 上报。
3. **J1-P2-1≡J4-P2-1 索引同步结果落日志**：`app.py` 在 `sync()` 后按 `unavailable_sources` 有无选 `warning`/`info`，输出 `indexed`/`deleted`/失败源与 `source_errors`。此前该返回值写进 `app.extensions` 后**全仓无读取点**。
4. **J1-P2-2≡J4-P2-3 缩略图失败日志**：`thumbnail.py` 在 `_mark_failure` 后记一行 `warning`（含 `ffmpeg`/`ffprobe` 解析结果），按文件去重并上限 20 条——整页灰块此前日志 0 行。
5. **J1-P2-3 JSON 存储隔离日志**：`json_storage.quarantine_corrupt_file` 成功后 `warning` 记录「哪个文件被移到哪个 `.corrupt`」，否则「收藏/合集突然空了」无从判断是隔离还是被删。
6. **J1-P2-4 下载任务生命周期日志**：`downloader._run_job` 补开始 `info`（job id + url）、取消 `info`（保留文件数）、成功 `info`（文件数）、失败 `warning`（错误文案）。
7. **J3-P2-3≡J1-P3-1 500 响应带关联 ID**：`app.py` 生成 8 位 reference，同时写进日志行与响应体（API 为 `reference` 字段，页面追加在文案后），用户报障可对上具体那一条。
8. **J2-P2-1 重扫超时与服务端原因**：`settings_page_controller.js` 的 `/api/library/sync` 加 180 s 超时；`catch` 不再丢弃 `error.message`（并发扫描的 409「索引正在被占用」现在能看到），超时单独提示。
9. **J3-P2-4 `/api/media/info` 索引异常日志**：`except Exception` 里补 `warning(exc_info=True)`，此前「索引坏了」在界面上显示成「这个文件没有元数据」。

## 四、未修 P2（12 条，转 backlog）

- J1-P2-5 回收站/收藏/AI 配置的写入类 `OSError` 一律静默（同一产品内与 `downloads.py:401` 不一致）。
- J1-P2-6 生产模式无请求级日志（本轮只覆盖 >2 s 的慢请求；全量 access log 需先解决缩略图噪声与容量）。
- J2-P2-2 英文原始异常直出中文界面（`signal timed out` / `Failed to fetch`）——需要统一的 `describeError`。
- J2-P2-3 离线几乎没接线：`tiklocal:offline` 全库零监听，恢复后首页仍停在空库态。
- J2-P2-4 flow 丢弃服务端错误体（`throw new Error('HTTP '+status)`）。
- J2-P2-5 flow 字幕失败只闪红灯 1.4 s，`flow.error.title_load/title_generate` 是死文案。
- J2-P2-6 删除/恢复请求无超时（`ui_feedback.js`），服务端卡住时 Promise 永不 settle。
- J2-P2-7 撤销窗口仅 10 s 且只在落地页首载给一次。
- J3-P2-1 库页不告知「来源离线」，幽灵行照常渲染且可点。
- J3-P2-2 `skipped_prefixes` 不落盘、不展示，全仓无消费者。
- J3-P2-6 数据库损坏被隔离重建后应用内无告知（日志有，界面无）。
- J3-P2-7 `/api/search` 无法区分「索引为空」与「没有匹配」。

## 五、P3（17 条，摘要）

- J1-P3-2 内部英文异常直接进用户可见文案（绕过 `t()`）；P3-3 静默降级/丢条目（个别文件或整个功能无声消失）；P3-4 日志格式无线程标识、容量被客户端错误淹没；P3-5 日志写入本机绝对路径。
- J2-P3-1 `requestJson/requestForm` 裸 `response.json()`（非法 JSON 抛 SyntaxError 而非可读文案）；P3-2 并发保存 `catch(_)` 吞原因；P3-3 首页 `fetchJson` 无超时（已随 P1 修）；P3-4 client-log 每页只报前 5 条且无截断标记；P3-5 搜索失败不区分原因、重试按钮文案用 `search.submit`；P3-6 toast 互斥覆盖导致连续失败只留最后一条。
- J3-P3-1 扫描状态三元组不完整（扫描中不可见 + `last_synced_at` 零成功也刷新）；P3-2 缩略图失败只留 `.jpg.fail`，无计数、无单条查询；P3-3 回收站静默丢弃读不出的条目，恢复时反报「不在回收站」。
- J4-P3-1 未捕获异常永远不进 `tiklocal.log`（无 `excepthook`）；P3-2 `InstanceLockError` 文案指向不存在的 `--instance`；P3-3 启动日志不记录实际生效的配置文件路径；P3-4 `thumbs`/`dedupe` 丢弃统计、失败也返回 0；P3-5 `instance/config.py` 的 `MEDIA_ROOT` 永远被覆盖且语法错误静默。

## 六、实现要点

- **日志要能定位，不要只加噪声**：所有新增日志都带定位字段（uri、job id、source id、winerror/errno、reference），并对高频路径去重/限流（缩略图 20 条上限、client-log 每分钟 20 条、慢请求只记 >2 s）。
- **区分「没数据」与「读失败」**：首页错误条、统计「暂时读不到」、`/api/media/info` 的 `reason` 是同一原则的三处落地——空态文案绝不能兼作失败态文案。
- **错误要可关联**：500 的 reference 同时进日志与响应，避免「用户说 500、日志里有五条 warning」的经典僵局。
- **状态码保守**：`/api/media/info` 保留 404，只加结构化 `reason`，避免一次诊断改进演变成三处调用方的连锁改动。

## 七、验证

- 全量 `pytest -q`：**606 passed / 8 skipped / 0 failed**（第 28 轮基线 604，+2 为本轮 `tests/test_request_boundaries.py` 新增的 client-log 用例；另 1 条为日志换行注入防护）。
- `node --check`：`home_page_controller.js`、`settings_page_controller.js` 通过。
- 手工核验：`/api/media/info` 四种 reason 分支、`/api/client-log` 的限流与截断、500 handler 的 reference 格式（一次 `test_client` 触发未捕获异常，响应体与日志行 reference 一致）。
- 未做：真实断网/真实 NAS 超时下的浏览器行为（无 JS 运行时测试，见下）。

## 八、覆盖率缺口

- 前端仍无自动化运行时测试：首页错误条、重扫超时提示只做了语法检查与代码审查，靠人工点检。
- 「慢请求」阈值 2 s 未经真实负载标定；缩略图冷启动可能长期触发 warning，需观察一轮真实使用再调。
- J1/J4 的多条 P2（写入类 OSError 静默、excepthook、配置路径记录）是同类问题的不同落点，本轮只修了最可见的几条。

## 九、下轮建议

视角：**性能与规模**——不是「会不会错」，而是「一万条、十万条时还对不对」：全表物化、无索引查询、N+1、分页上限、缓存容量与淘汰、大库冷启动时间。这是唯一还没系统覆盖过的维度。
