# 第 21 轮审查报告（HTTPS 与启动链 / 媒体来源与设置页 / 统一搜索 / 认证与配对 / 抖音功能与媒体路由）

日期：2026-10-29
起点基线：579P/7S/0F（第 20 轮结束后，397.10 s）
审查域：HTTPS 与启动链（tls/run）、媒体来源与设置页（media-sources）、统一搜索（search）、认证与配对（auth）、抖音功能与媒体路由（douyin）

> 说明：5 个域全部交付报告，共 35 条：P0×0 / P1×0 / P2×11 / P3×24；独立复审（§十一）另发现 1 条 P2，合计 36 条（P0×0 / P1×0 / P2×12 / P3×24）。其中 C 域 #2 经复核实为伪阳性（见 §三），实际成立 35 条。本轮修 5 条 P2 + 4 条 P3，其余 26 条转 backlog。
>
> 本轮审查重点是第 20 轮之后新增的 51 个文件（+6959/−141）：`services/tls.py`(+812 全新)、`services/media_sources_store.py`(+362 全新)、`web/search.py`(+410 全新)、`static/search_page_controller.js`(+582)、`static/settings_page_controller.js`(+297)、`run.py`(+309)、`web/settings.py`(+205)。

## 一、HTTPS 与启动链（2×P2 + 8×P3）

| # | 级别 | 问题 | 位置 | 处置 |
|---|------|------|------|------|
| 1 | P2 | 端口探测 socket 未设 `SO_REUSEADDR`，与真实服务器不一致：Werkzeug 的 `BaseWSGIServer` 是 `allow_reuse_address = True`（`werkzeug/serving.py:713`），上次运行遗留 TIME_WAIT 时探测 `bind` 报 EADDRINUSE → 打印「端口被占用，换 `--port`」并 exit 1，而服务器本可正常绑定。仅 `--tls` 分支探测，HTTP 不探测，行为不一致 | `tiklocal/run.py:191-199`（`probe_listen`），调用点 `:608` | 已修：见 §七。探测改为「对齐 Werkzeug 的 bind 能力」：设 `SO_REUSEADDR` 后 bind，成功再用一次 connect 探活（0.0.0.0→127.0.0.1、::→::1，0.5 s 超时）——Windows 上 reuse-address socket 可与正在 LISTEN 的 socket 并存，只有探活才能保住「端口被占用」这条唯一有价值的提示 |
| 2 | P2 | `--dev` 完全不可用：`main()` 在 `create_app()` 之前取 instance 锁（`tiklocal/run.py:595-599`），而 werkzeug reloader 会以 `WERKZEUG_RUN_MAIN=true` 重跑整个 `main()`，子进程再取同一把锁必然 `InstanceLockError` → `Error: The instance directory ... is already in use` + exit 1。`--dev --tls`（`docs/https.md:78` 承诺）同样不可用。**回归**：`git blame` 指向 7844fec（第 16 轮的修复提交），已跨 5 轮未发现；同文件 `:670` 对 `download_manager.start()` 已有同型守卫，说明作者知晓子进程重跑 | `tiklocal/run.py:591-599`、`:675-679` | 已修：`dev_child = args.dev and os.environ.get('WERKZEUG_RUN_MAIN') == 'true'` 时跳过取锁（父进程持有，`app.extensions['instance_lock'] = None` 无人读取，句柄由父进程保活） |
| 3 | P3 | 续期阈值固定 30 天而 `days` 允许 1..3650：`--tls-days ≤30` 时每次启动都判为「快过期」并重生成，指纹每次都变 | `tiklocal/services/tls.py:424` | 转 backlog |
| 4 | P3 | 证书与私钥两次 `os.replace` 非成对原子，`docs/https.md:47-48`「cannot leave a half-written pair」措辞过强（单文件原子成立） | `tiklocal/services/tls.py:494-495` | 转 backlog（文档措辞与真实保证不一致，非崩溃路径） |
| 5 | P3 | `tls generate` 不读 `config.yaml` 的 `hostnames`（serve 路径 `:469`/`:617` 才读）→ 预生成证书下次 serve 被 `_metadata_covers` 判为不覆盖而重生成 | `tiklocal/run.py:432-439` | 转 backlog |
| 6 | P3 | `days=args.tls_days or TLS_DEFAULT_DAYS`：显式 `--tls-days 0` 被 `or` 当「未提供」→ 静默 825 天，`tls.py:379-380` 的 1..3650 校验对 0 永不可达 | `tiklocal/run.py:623`、`:437` | 已修：改 `is None` 判断（两处），0 现在会走到范围校验并 exit 1 |
| 7 | P3 | 出网探测拿到的源地址直接进 SAN，未过 `_is_lan_ipv4` → 公网 IP 进证书，与 `tls.py:195-197` docstring「Public addresses … are left out」矛盾 | `tiklocal/services/tls.py:247-248` | 转 backlog |
| 8 | P3 | 文档教 Android「Install a certificate → CA certificate」，但自签 leaf 是 `CA:FALSE`，该入口会被系统拒绝 | `tiklocal/services/tls.py:543`、`docs/https.md:104-108` | 转 backlog（文档/部署指引） |
| 9 | P3 | `validate_certificate_pair` 只验可读与配对，不查 notAfter/SAN → 过期自定义证书照常启动、握手才失败、启动零提示 | `tiklocal/services/tls.py:305-307`（经 `run.py:614`） | 转 backlog |
| 10 | P3 | 容器内 `--tls`（默认端口 8443）时健康检查仍打 `http:8000` → 恒 unhealthy；`EXPOSE 8000`、compose 只发布 8000 | `Dockerfile:38-39`、`:35`、`compose.yaml` | 转 backlog |

## 二、认证与配对（1×P2 + 2×P3）

| # | 级别 | 问题 | 位置 | 处置 |
|---|------|------|------|------|
| 11 | P2 | 未认证的 `POST /login`（或任意已登录 unsafe 请求）携带**非 ASCII** CSRF token 时 500：三条取值路（`X-CSRF-Token` 头原始字节、表单 `_csrf_token`、JSON `_csrf_token`）都经 `str()`，`secrets.compare_digest` 对非 ASCII str 抛 `TypeError: comparing strings with non-ASCII characters is not supported`，未被捕获 → 500 + 完整 traceback 进 2 MiB×3 轮转日志（`tiklocal/app.py:608`）。无越权（请求仍被拒），但未认证即可刷日志、错误码语义错 | `tiklocal/auth.py:101`（配合 `:89-96`） | 已修：两侧 `encode('utf-8')` 后比较（`supplied` 用 `errors='replace'` 兜孤立代理），保持常量时间 |
| 12 | P3 | 两已登录会话同时改密码：都过 verify、都读 revision=N、各写 N+1，后写覆盖，两请求都 200；`_save` 临时名固定，并发 `os.replace` 在 Windows 可能 PermissionError，而调用方只捕 `ValueError` → 500 但密码可能已落盘 | `tiklocal/services/auth.py:90-101`、`tiklocal/web/settings.py:85-99` | 转 backlog（需 AuthStore 加锁 + `tempfile.mkstemp`，同 `DeviceAuthStore` 模式） |
| 13 | P3 | `retry_after()` 每个登录/改密码/配对请求都调 `_active(key)`，defaultdict 为每个 `remote_addr` 建键；只有成功才 `clear()` pop → 任何失败都留下空 deque 且永不回收，内存随失败过的源 IP 数单调增长 | `tiklocal/auth.py:20`、`:38-43` | 转 backlog。**注意**：不能在 `_active` 里 pop——`record_failure` 会追加到已脱离 dict 的 deque，计数清零、限流可绕过 |
| 35 | P2 | 5 个配对/电台端点的 `request.get_json(silent=True) or {}` 未走 `json_body()`：非空 JSON 数组 body → `payload.get()` → `AttributeError` → 500 + 完整 traceback；其中 `/api/v1/pair`、`/api/v1/pair/claim` 在 `before_request` 白名单里，**未认证即可触发**。无越权、无状态变更，纯 500 与日志噪音（本轮已修的 B6 是同一类的已认证版本，这条更宽） | `tiklocal/radio_client.py:96`、`:116`、`:165`、`:272`、`:286` | 已修（独立复审发现）：改用 `json_body()` |

## 三、统一搜索（1×P2 + 5×P3，另驳回 1 条）

| # | 级别 | 问题 | 位置 | 处置 |
|---|------|------|------|------|
| 14 | P2 | 滚动加载会把**新词**的第 2 页追加进**旧词**的网格：`loadMore()` 用 `readQuery()`（输入框现值）+ `activeTab`，而输入框内容在结果出来后才改、提交前不重置分页（`input` 监听只在空串时 `resetPaging()`） | `tiklocal/static/search_page_controller.js:373-378`、`:331-336`、`:117-122` | 已修：见 §七。`renderResults()` 记住 `payload.query`/`payload.tab` 为 `resultsQuery`/`resultsTab`，`canLoadMore()`/`loadMore()` 一律用它们 |
| — | ~~P2~~ | ~~user tab 声称只出首页，第 25 位作者不可达~~ | `tiklocal/web/search.py:347` | **驳回（伪阳性）**：作者块由 `blog_rows()` 按 `source_id` 分组（`tiklocal/services/library_index.py:552-584`），作者数 ≤ `MAX_SOURCES=24`（`media_sources_store.py:33,188-189` 强制），前端 `PAGE_SIZE=24`，故 `cap=24` 恒 ≥ 作者数；`counts['users']` 与 `len(users)` 只在 `limit<24` 时不等，而前端从不发该参数 |
| 15 | P3 | 每个 general/user 请求都跑 `blog_rows()`（1 条 GROUP BY + 每 source 1 条 `LIMIT 1`）+ 每 source `resolve_profile()`（读 JSON + stat 头像），而 general 第 2 页起客户端已丢弃 users | `tiklocal/web/search.py:338-345`、`library_index.py:552-584` | 转 backlog（`offset>0` 且 `tab=general` 时跳过作者块） |
| 16 | P3 | 命中来自文件名/目录名时仍挂 `&caption=`，`flow.py:405-412` 收窄序列为空会回落到推荐流（`caption_matches` 只比对 `post_title(uri)`） | `tiklocal/web/search.py:226`、对照 `tiklocal/web/flow.py:208-213`、`:405-412` | 转 backlog（改用 `media_naming.post_title(uri)` 判定） |
| 17 | P3 | 卡片日期自己拼：`captured_local_date` 无形状校验、`mtime_ts=0` 显示 1970-01-01 | `tiklocal/web/search.py:194-203` | 已修：改用 `tiklocal/web/media_payloads.py::post_date_of()`（已有 `len==10 and [4]=='-'` 与 `year >= 1970` 守卫），删掉本地 `_date_label()` |
| 18 | P3 | 声称验证 LIKE 元字符的测试没让任何元字符到达索引（`q` 里没有 `%`/`_`/`\`），`_like_term()` 的转义分支零覆盖，删掉转义测试仍全绿 | `tests/test_search.py:176-180` | 已修：新增 `%`/`_`/`\`/`％` 四条断言 |
| 19 | P3 | OFFSET 分页在索引刷新期间会漏条（`ORDER BY mtime DESC, uri` + `LIMIT/OFFSET`） | `library_index.py:392`、`:414-417` | 转 backlog（需 keyset 游标，改动大） |
| 20 | P3 | tab 只有 `aria-controls` 无 id、panel 无 `aria-labelledby`；键盘只映射 ArrowLeft/Right，无 Home/End | `tiklocal/templates/search.html:42-57`、`search_page_controller.js:554-566` | 转 backlog |

## 四、媒体来源与设置页（4×P2 + 5×P3）

| # | 级别 | 问题 | 位置 | 处置 |
|---|------|------|------|------|
| 21 | P2 | `_persist_sources()` 把 `load()` 的 `StoreUnreadableError`（OSError）吞成 `stored=None` 后照常 `save()`：读不出来但**完好**的 `media_sources.json` 被整表覆盖，`removed_ids` 丢失。与 `json_storage.read_json_file()` 的契约正面冲突（该文件明确写「returning the default would let the caller erase real data on save」并 `raise StoreUnreadableError`） | `tiklocal/web/settings.py:139-145` | 转 backlog（`_apply_change` 已有 `except OSError → 500 source_error_save`，最小修法=删掉这里的 try/except；本轮因需同批改动 B2 的运行态回写语义而合并到下一轮） |
| 22 | P2 | `resolve_startup_sources()` 在保存的默认目录不可达时用启动目录「仅本次」顶替（`media_sources_store.py:276-280`），但 `_apply_change` 以**运行态**列表回写 → 任何一次增删改都把启动目录永久写成默认目录；外置盘未挂载 → 改名 → 原路径丢失 | `tiklocal/web/settings.py:147-158`、`media_sources_store.py:248-286` | 转 backlog（需给回退来源打标记；`MediaSource`/`StoredMediaSources` 均为 frozen dataclass，要扩字段 + 迁移） |
| 23 | P2 | `validate_folder()` 无保留目录判断：可把 `<home>`、`<home>/.tiklocal`、盘符根加为来源，而 `serve_media` 只做包含性校验不做后缀白名单 → `/media/@home/.tiklocal/auth.json`（口令哈希 + secret_key + 设备令牌）、`~/.ssh/id_rsa` 可下载 | `media_sources_store.py:163-181`、`tiklocal/web/media.py:421-441` | 转 backlog（需在来源校验里拒绝等于/包含 `get_data_dir()`、`instance_path` 的路径，属策略决策） |
| 24 | P2 | `settings_view()` 直接调 `library_stats()`，其 `safe()` 对 `sqlite3.DatabaseError` 显式 re-raise → 索引损坏时 `/settings/` 500，改目录的唯一入口打不开；与同文件 `api_media_sources` 注释「A damaged index must not hide the folder list」自相矛盾 | `tiklocal/web/settings.py:32-40`、`:235-245` | 转 backlog（与第 18 轮「`safe()` 吞异常」条目合并处理：需要「统计不可用」的用户可见提示，前端配合） |
| 25 | P3 | store 已落盘、运行态已切换后 `sync()` 抛非 busy 异常 → 500，前端显示失败且对话框不关，重试撞 duplicate/not_found | `tiklocal/web/settings.py:172-187` | 转 backlog |
| 26 | P3 | 改密码端点 `request.get_json(silent=True) or {}` 未用 `json_body()`：JSON 数组/字符串 body → `AttributeError` → 500（同文件其它端点都用 `json_body()`，其 docstring 正是为这个坑写的） | `tiklocal/web/settings.py:64` | 已修：改用 `json_body()` |
| 27 | P3 | 读-改-写无锁：两标签页并发加目录后写者整文件覆盖 | `tiklocal/web/settings.py:147-158` | 转 backlog（`_apply_change` 加模块级 `threading.Lock`） |
| 28 | P3 | 移除来源后 `<source>/.tiklocal-trash` 仍在磁盘，回收站列表与保留期清理只遍历现役来源 → 文件永久占盘且不可见 | `media_sources_store.py:228-245`、`tiklocal/services/trash.py:86-112` | 转 backlog（与第 13 轮「源移出后回收站失联」同源，需存储格式迁移） |
| 29 | P3 | 默认来源改路径时只迁移缩略图缓存身份，`@default/rel` 形态的收藏/合集/播放进度/回收站条目全部改指新目录，同名文件静默错配 | `tiklocal/web/settings.py:161-166` | 转 backlog |

## 五、抖音功能与媒体路由（2×P2 + 3×P3）

| # | 级别 | 问题 | 位置 | 处置 |
|---|------|------|------|------|
| 30 | P2 | 非抖音模式下从搜索结果进入 `/flow?start=…&caption=…`：服务端已按 caption 窄化，但 chip/清除/返回全在默认 `display:none` 的 `.douyin-topbar` 内（仅 `body.douyin`/`body.tag-feed` 下 `display:flex`），`syncCaptionFilter()` 又不像 `syncTopicHeader()` 那样加 body class → 时间线无声变少或空，无提示无出口，只能手改 URL；空态还复用「添加媒体并刷新索引」文案 | `tiklocal/static/flow_page_controller.js:719-728`、`tiklocal/static/flow.css:992`、`:1003-1004`、`tiklocal/web/flow.py:405-412` | 转 backlog（需 `caption-feed` body class + CSS + 专用空态文案） |
| 31 | P2 | 弹层打开时 ArrowDown/Up/Space 仍作用于底层 feed（`showItem` 不关弹层）；作品 A 的 `/api/media/info` 响应到达时只校验 `mediaSheetIsOpen()`，把 A 的 path/尺寸/日期写进正在展示 B 的弹层 → 「复制路径」复制的是上一件 | `tiklocal/static/flow_page_controller.js:2302`、`:635`、`:658`、`:661-668` | 转 backlog（守卫补 `mediaSheetIsOpen()`；`showInfoSheet` 加请求序号或 AbortController） |
| 32 | P3 | 弹层无焦点归还、无焦点陷阱：打开时聚焦 close，关闭后焦点落 body；焦点离开弹层后 Escape 完全失效（监听挂在 `#media-sheet` 自身，document 级 handler 无 Escape 分支），`role="dialog" aria-modal="true"` 下 Tab 可走到背后控件 | `tiklocal/static/flow_page_controller.js:571-577`、`:2249-2254` | 转 backlog |
| 33 | P3 | 「全部下载」用 N 个 `setTimeout` 触发 `<a download>`，全部丢失用户手势：Chrome 会弹「允许多个文件下载」许可，拒绝时只落第一个；重复点击无 busy 门 | `tiklocal/static/flow_page_controller.js:596-604`、`:678-706` | 转 backlog（需先实测浏览器策略；恢复服务端流式 zip 是备选） |
| 34 | P3 | `/api/media/info` 把服务器绝对路径回给浏览器（前端展示 + 复制），与同文件 `/delete` 注释「`str(e)` 会泄漏绝对路径」的政策相反；默认 `AUTH_ENABLED=False` + `host=0.0.0.0` 时局域网无凭据可读 | `tiklocal/web/media.py:483` | 转 backlog（改回 canonical uri 或 `source_id + rel_path` 会改变前端展示语义） |

## 六、本轮修复取舍

P0/P1 为 0，P2 共 12 条（含独立复审新增 1 条）。按规则每轮修 2–3 条 P2，本轮修 5 条，理由：

- **A1**：第 20 轮之后新写的代码，缺陷方向与修复相反（探测比服务器更严格），且会让用户被误导去换端口。
- **A2**：第 16 轮修复引入的**回归**，`--dev` 100% 不可用，按「回归当轮必修」处理。
- **C1**：搜索结果与滚动加载串查询，用户可见的数据正确性问题，改动 <20 行。
- **D1**：未认证即可触发的 500 + traceback 日志洪水，属安全相邻。
- **D4**（`radio_client.py`，独立复审发现）：与刚修的 B6 同一类缺陷，且 5 处都在未认证可达的端点上；修法就是换成 `json_body()`，一行一处。

附带修 4 条 P3（`A6`、`B6`、`C5`、`C6`），均为 ≤5 行或纯测试改动。

## 七、修复实现要点

- `tiklocal/run.py`：新增 `listener_present(family, address)`（wildcard→loopback 映射 + 0.5 s connect 探活）；`probe_listen()` 设 `SO_REUSEADDR` 后 bind，bind 失败或有监听者都走同一段错误输出（单出口，socket 先关再打印）；`dev_child` 跳过 instance 锁；两处 `days=` 改 `is None` 判断。
- `tiklocal/auth.py`：`csrf_is_valid()` 两侧 `encode('utf-8')` 后 `secrets.compare_digest`。
- `tiklocal/static/search_page_controller.js`：新增 `resultsQuery`/`resultsTab`（`teardownPaging()` 清空），`renderResults()` 从 `payload.query` 取并记录，`canLoadMore()`/`loadMore()` 改用它们。
- `tiklocal/web/search.py`：删 `_date_label()` 与 `import datetime`，改用 `media_payloads.post_date_of()`。
- `tiklocal/web/media_payloads.py`：`post_date_of()` 现在把「1970-01-01」当作未知（索引对不可表示的 mtime 就钳到这一天，档案解压出来的 mtime=0 也走这条），并跳过 `mtime_ts <= 0` 的记录。这是 #17 的另一半：只换 helper 不够，helper 自己也得认得出「没有日期」。
- `tiklocal/web/settings.py`：改密码端点改用 `json_body()`。
- `tiklocal/radio_client.py`：5 处 `request.get_json(silent=True) or {}` 改为 `json_body()`（`tiklocal.web` 是只依赖 flask 的叶子模块，无循环导入）。

## 八、验证

- 新增 12 条测试：`tests/test_tls.py` 5 条（`listener_present` 识别 wildcard 监听者；探测不比真实服务器更严格；`--dev` reloader 子进程不重复取锁；**非 `--dev` 启动仍拒绝已占用的 instance 目录**；`tls generate --days 0` 报范围错误）、`tests/test_search.py` 3 条（卡片日期取名字里的拍摄日；**没有可用日期时不显示 1970-01-01**；`%`/`_`/`\`/`％` 不作通配符）、`tests/test_auth.py` 2 条（改密码端点收到 JSON 数组 body 回 400 而非 500；**非 ASCII CSRF token 回 403 而非 500**）、`tests/test_radio_client.py` 2 条（**配对端点与电台端点收到 JSON 数组 body 不 500**）。
- 回归测试有效性：`test_dev_reloader_child_does_not_take_the_instance_lock` 在修复前必然 exit 1（测试自己先持锁）；`test_a_second_serve_on_the_same_instance_is_refused` 反向锁住同一处——`dev_child` 若写成恒真，它会红；`test_password_change_rejects_a_non_object_body`、`test_pairing_ignores_a_non_object_body_instead_of_crashing`、`test_native_radio_endpoints_ignore_a_non_object_body` 在修复前都是 500；`test_an_explicit_zero_lifetime_is_an_error_not_the_default` 在修复前会静默按 825 天生成；`test_a_non_ascii_csrf_token_is_a_rejection_not_a_crash` 在修复前是 500（`compare_digest` 的 `TypeError`）；`test_a_card_without_a_usable_date_shows_no_date` 在修复前显示 `1970-01-01`。
- `test_probe_listen_is_never_stricter_than_the_real_server` 在本机（Windows）不构成回归检测：实测 Windows 上普通 `bind` 对只含 TIME_WAIT 子连接的本地端口本就会成功（`netstat` 有 TIME_WAIT 行而 `bind` 通过），因此该缺陷只在 POSIX 上可复现。这条测试守的是不变量「探测不得比 `allow_reuse_address` 的真实服务器更严格」，POSIX 上能捕获旧实现；Windows 上真正的行为变化由 `test_port_taken_reports_one_actionable_line` 覆盖——加了 `SO_REUSEADDR` 后 bind 会成功，全靠探活才能仍报端口占用。
- 全量：`pytest -q` = **591 passed / 7 skipped / 0 failed**（307.71 s，起点 579P/7S）。
- 改动文件：`tiklocal/run.py`、`tiklocal/auth.py`、`tiklocal/radio_client.py`、`tiklocal/web/search.py`、`tiklocal/web/media_payloads.py`、`tiklocal/web/settings.py`、`tiklocal/static/search_page_controller.js` + 4 个测试文件。

## 九、覆盖率缺口

- 本轮 5 域全部交付，无缺口。
- `--dev` 只做了「不撞锁」的验证（stub 掉 `Flask.run`），未起真实 reloader 观察子进程重启；`--dev --tls` 未单独冒烟。
- 端口探测只在本机（Windows）验证，POSIX 的 TIME_WAIT 路径未实测（见 §八）。
- `radio_client.py` 的修复只经 Flask 测试客户端验证，未用真实原生客户端（QR 配对 → 收藏/反馈）冒烟；端点契约未变（非对象 body 从 500 变 4xx），风险低。
- `--tls-days` 只测了 0 这一个边界，1 与 3650 仍靠 `tls.py` 自身的范围校验。

## 十、转 backlog

本轮 35 条成立发现中修 9 条（5×P2 + 4×P3），其余 26 条转 backlog（见 `backlog.md` 第 21 轮段）。其中 4 条 P2 的暂不修理由集中在：需要扩 `StoredMediaSources` 字段并迁移（#22）、需要策略决策（#23 保留目录）、需要前端配合（#24 统计不可用提示）、需与 B2 同批改动（#21 读不出来时的回写语义）。

## 十一、独立复审

阶段 F 派了一个 fresh context 的独立 reviewer（deepseek-v4.1-flash），只做静态复审 + 定点实测，不给改文件权限。结论：**6 处修复判定全部「正确」**，另发现 1 条 P2 与 3 条测试质量问题。

修复判定要点：

- `run.py` 的 `listener_present`/`probe_listen`：实测 Windows 上 TIME_WAIT 根本不阻 `bind`，所以这条修复在 Windows 是 no-op、只在 POSIX 生效；有真监听且占位者未设 reuse 时 reuse bind 仍失败（WinError 10013）→ busy 照报；**占位者也是 Werkzeug/TikLocal（设了 reuse）时 reuse bind 会成功，此时 connect 探活是唯一手段**，故 `listener_present` 是 load-bearing。残余取舍：占位者绑在具体非回环地址且设 reuse 时会漏报，但这与「真实服务器能否绑定」一致。
- `dev_child` 跳锁：锁由父进程在 reloader 循环里全程持有，子进程整个生命周期（含重启）无锁窗口；全仓 grep 确认无人读 `app.extensions['instance_lock']`。
- `days … if args.X is None`：args 默认均为 None，无其它 `or TLS_DEFAULT_DAYS` 站点。
- `auth.py` bytes 比较：实测 `secrets.compare_digest('abc', 'abç')` 抛 TypeError（原缺陷确认）；全仓其它 `compare_digest` 只比 hexdigest。
- `search_page_controller.js` 的 `resultsQuery`/`resultsTab`：所有 reset 路径一致（`teardownPaging()` 清 `resultsQuery` + `hasMore=false`），`resultsTab` 只在 `renderResults()` 里与 `resultsQuery` 同时写入，`runSearch()` 有 seq 守卫，`appendVideos()` 有 `renderedKeys` 去重兜底；`payload.query` 就是第 1 页实际用的 term，不会错位。
- `post_date_of` + `json_body()`：`json=[NEW_PASSWORD]`（**非空**数组）确实复现旧的 500（空数组是 falsy，会退化成 `{}`，所以测试必须用非空数组）。

复审提出的 4 条意见，本轮全部处理：

1. **P2 `radio_client.py` 5 处同类 500**（未认证可达）→ 已修，见 §二 #35。
2. `test_a_card_carries_the_capture_day` 无鉴别力（旧 `_date_label` 也返回同一结果）→ 补 `test_a_card_without_a_usable_date_shows_no_date`。**这条新测试第一次跑是红的**：证明只换 helper 不够，`post_date_of` 对索引钳出来的 `1970-01-01` 同样没有判断力，于是连 `post_date_of` 一起修（见 §七）。
3. 修复 #4（CSRF 非 ASCII）无测试、修复 #2 只有负向测试 → 补 `test_a_non_ascii_csrf_token_is_a_rejection_not_a_crash` 与 `test_a_second_serve_on_the_same_instance_is_refused`（后者锁住 `dev_child` 不被写成恒真）。
4. `test_probe_listen_is_never_stricter_than_the_real_server` 在 Windows 恒真 → 已在 §八 明确写清覆盖边界，不假装它是回归检测。

复审同时确认：新增测试里没有 HTML/CSS/JS 源码文本断言，也没有实现细节断言。
