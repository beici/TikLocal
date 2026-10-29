# 第 42 轮审计：跨进程与跨实例一致性

- 视角：**跨进程 / 跨实例一致性**（CLI 与运行中的服务器、两个 TikLocal 进程、两个浏览器标签页、两台设备、崩溃后遗留的锁与临时文件）
- 起点基线：`659 passed / 8 skipped`（第 41 轮收尾，tag `audit-round-42-start` 打在 `83508d4`）
- 收尾基线：`669 passed / 8 skipped`（新增 10 条回归用例）
- 方法与范围：五个域并行只读审查（W1 实例与数据目录竞争、W2 CLI 与运行中的服务器、W3 多进程文件系统原语、W4 多标签页与多设备、W5 时钟与数值自查），逐条复核后合并去重。

## 一、问题总览

| 域 | 范围 | 已修 | 转 backlog |
| --- | --- | --- | --- |
| W1 | 实例锁、数据目录、损坏库隔离、日志文件、atexit | 5 | 2 |
| W2 | `run.py` 子命令与运行中的服务器、CLI 输出契约 | 5 | 3 |
| W3 | 临时目录、原子替换、cookie 私有副本、多进程文件原语 | 4 | 3 |
| W4 | 多标签页、多设备、共享 cookie 与共享列表 | 5 | 5 |
| W5 | 自查：时钟基准与数值边界 | 3 | 0 |
| 合计 | | 22 | 13 |

两条 P1 都在 W4：`save_position()` 把「刚划开就划走」当成「已看完」删掉整行；登录时轮换 CSRF token 让其余标签页永久 403。

## 二、P1 修复

### 1. 瞥一眼不再抹掉另一台设备的进度（W4-1）

`tiklocal/services/database.py` `save_position()` 原来把三种情况压成一个 `keep=False` 分支：

```python
if position < RESUME_MIN_POSITION or position > duration - RESUME_END_MARGIN:
    DELETE FROM playback_positions WHERE uri = ?
```

`tiklocal/static/playback_resume.js` 在切条时无条件上报上一条、快速查看器每切一条都 `track`，所以设备 B 划开 2 秒划走就会删掉设备 A 在 40:00 的那一行——100% 丢失、不可恢复、无提示。

现在拆成三段：时长太短 → 删行（不值得记）；位置太靠前 → **什么都不做并返回 `True`**（保留别人的行）；已到片尾 → 删行；其余 upsert。

### 2. 登录不再把其他标签页锁在 403 里（W4-2）

`tiklocal/auth.py` 登录成功路径先 `session.clear()` 再 `ensure_csrf_token()`，新 token 只写进当前响应；cookie 是全标签页共享的，其余标签页 `<meta name="csrf-token">` 仍是旧值 → 收藏/保存/删除全部 403 且永不自愈，连 `/logout` 表单也落在 403 页（而 cookie 还有 30 天）。

现在保留原 token（`preserved_token = session.get('_csrf_token')` → `session.clear()` → 写回），并给 CSRF 失败加了可操作的出口：

- `csrf_error_response()` 对 `/logout` 走旁路：清会话 + 跳登录页（用户永远能登出）；
- API 分支返回 `{'success': False, 'code': 'csrf', ...}, 403`；
- `tiklocal/static/base.js` 的 fetch 包装器识别 `code === 'csrf'`，用 `sessionStorage` 做 10 秒闩锁后 `location.reload()`（刷新即拿到新 token），401 闩锁也补了 3 秒后自动释放。

## 三、P2 修复

### 3. 隔离损坏库时先搬 WAL（W1-F1）

`tiklocal/app.py` `_quarantine_database()` 原来先搬主库、后搬 `-wal`/`-shm`。副文件被别的进程持有（`vectorize` 的 SQLite 连接、DB Browser、杀毒扫描）时 `WinError 32` 会让函数 `return None`，而主库已经不在原地——下次启动建一个 0 字节新库并重放旧 WAL，正是这段代码注释里警告的「实例永久坏掉」。

现在顺序反转（先 `-wal`/`-shm`、最后主库），每步失败都 `_restore_moved()` 回滚；`_move_with_retry()` 3 次重试（`gc.collect()` + 0.05 s）。

### 4. 下载页的清理不再覆盖别人的列表（W4-3）

入队成功后前端发 `action:'clear'`、部分失败后发 `action:'replace', lines: failedUrls`，两者都是整表操作：另一个标签页或手机刚 `add` 的 20 条被抹掉。

现在 `UrlListStore.remove_many(lines)` 在锁内做集合差集写回，`/api/download/urls` 支持 `action:'remove'` + `lines`，前端只删自己消费掉的那些行（`consumedUrls = queuedItems.concat(skippedItems)`）。顺带修掉 `Number(list)` 恒为 `NaN` 导致「已入队」分支永不触发的问题。

### 5. 下载工作目录不再被当成媒体、被当成重复文件（W3-F1 / W2）

`_cleanup_stale_temp_dirs()` 在 `DownloadManager.__init__` 里 `rmtree` 超过 6 小时的工作目录——两个进程（`serve` + `download` CLI）同开一个媒体根时，B 进程会把 A 正在写的目录删掉；`dedupe` 的 `_scan_files` 只排除回收站，会把半成品当重复文件哈希。

现在清理带「最近有活动就不动」的年龄判据，`dedupe` 同时排除 `TRASH_DIR` 与 `DOWNLOAD_TMP_DIR`。

### 6. 两个进程只允许一个创建 `auth.json`（W1-F5）

`AuthStore.ensure()` 是 check-then-create：两个进程同时首次启动各写一份，服务端内存里留着 A 的密钥、磁盘上却是 B 的，重启后所有会话失效。

现在用 `os.open(claim, O_CREAT|O_EXCL|O_WRONLY, 0o600)` 抢 `<auth.json>.claim`，抢不到就轮询等对方写完再 `_load()`；超时视为崩溃残留，删掉重试最多 3 轮，`finally` 里关 fd 并清理 claim。

### 7. 日志不再被两个进程写进同一个文件（W1-F4）

固定文件名 + 每个子命令都 `configure_logging()` → 两个进程持同一句柄，轮转时 `WinError 32` 后 `stream=None` 且永不重开，日志静默死亡。

现在文件名带 pid（`tiklocal.<pid>.log`），并按 mtime 只保留最新 4 个 `tiklocal.*.log*`。

### 8. 两个进程不再互相写坏对方的原子替换（W3）

`device_auth.json` 与回收站 `entry.json` 原来各自手搓 `write_text` + `os.replace`，临时文件名固定：第二个进程（dev reloader、CLI）会和第一个交错写字节，发布出一个加载即抛异常的文件——配对的设备一次性全部失效；单次 `os.replace` 也没有重试，杀毒软件持句柄时配对直接 500。

两处都改走 `write_json_atomic()`（唯一临时名 + 重试 + 权限），`entry.json` 的写回同样换成它。

## 四、P3 修复（择要）

| 问题 | 位置 | 修复 |
| --- | --- | --- |
| `atexit.register(media_probe.stop)` 每次 `create_app` 重复注册 | `tiklocal/app.py` | 模块级 `_PROBE_ATEXIT_REGISTERED` 守卫 |
| `migrate()` 把 busy_timeout 用尽当致命错误 | `tiklocal/app.py` | `_migrate_with_busy_retry()` 3 次退避 + 人话错误 |
| `thumbnails` 全失败仍 exit 0 | `tiklocal/run.py` / `thumbs.py` | 有失败且零产出 → `SystemExit(2)`；进度头打印媒体根 |
| 近似子命令被改写成 `serve <word>` | `tiklocal/run.py` | 近义命令提示表（`migrate`/`vectorise`/`thumb`/…） |
| `thumbs --overwrite` 因替换失败报 failed | `tiklocal/services/thumbnail.py` | 替换失败但旧缓存非空 → 返回旧文件 |
| 跨卷/被占用时 `replace` 一次失败即放弃 | `tiklocal/services/downloader.py` | 4 次退避重试 + `shutil.move` 兜底 |
| cookie 临时文件权限过宽 | `tiklocal/services/downloader.py` | `os.open(..., 0o600)`；私有副本 `chmod 0600` |
| 旧缓存目录的「认领」是读空再写，两个进程都会认领 | `tiklocal/services/thumbnail.py` | `os.open(marker, O_CREAT\|O_EXCL)` 独占创建，输家重读判归属 |
| 旧缓存搬家失败时返回可能已不存在的路径 | `tiklocal/services/thumbnail.py` | 只有 `canonical.is_file()` 才返回它 |
| `vectorize` 注释声称可与服务器并存 | `tiklocal/experiments/similarity/cli.py` | 改为事实 + 冲突时提示先停服务器 |
| `pull()` 在锁外读列表 | `tiklocal/services/url_list.py` | stat/read/parse/写回全进 `_LIST_LOCK` |

## 五、未修清单（13 条，见 backlog 第 42 轮段）

- W1-F2 实例锁只覆盖 `serve`/`dedupe --execute`，`vectorize` 只锁到 `migrate()` 结束；
- W1-F3 `.legacy-root` 读空→写自己无锁、`legacy.replace()` 失败时返回可能已被搬走的路径；
- W2 三条（CLI 输出与退出码的边界：`--help` 措辞、部分失败路径的退出码、静默成功）；
- W3 三条：跨卷 `os.replace` 的语义、cookie 目录归属校验、临时目录名的可预测性；
- W4-F6/F7：`/api/activity` 无幂等键（重试自造 skip + impression）、`/flow` 只在加载时读 `douyin_mode`；
- W4-4/W4-5：登出只在提交表单的标签页清电台状态（另一标签页每 5 s 写回）、隐藏的 `/flow` 标签页不暂停。

## 六、验证

- 新增 `tests/test_round42_regressions.py`（10 条）：`remove_many` 保留他人链接、API `action:'remove'`、登录保留 CSRF token、过期 token 仍可登出、403 带 `code: 'csrf'`、`AuthStore.ensure` 只创建一次且清理 claim、`_quarantine_database` 先搬 WAL 且失败回滚、日志文件名含 pid、日志保留 4 份、`_scan_files` 跳过下载工作目录。
- 改了两条既有用例的期望：`tests/test_playback_resume.py`（瞥一眼不再删行，新增跨设备用例）、`tests/test_auth.py`（过期 token 的 `/logout` 仍 302）。
- 聚焦：`tests/test_auth.py tests/test_playback_resume.py tests/test_download_urls.py tests/test_web_runtime.py tests/test_thumbnails.py tests/test_downloader_lifecycle.py` → **58 passed / 35.77 s**。
- 全量：**669 passed / 8 skipped / 0 failed**。

## 七、本轮结论

- 两条 P1 都属于「一次误操作静默毁掉别人的状态」：一条删进度、一条锁死写路径。共同修法是**把「不做什么」也当成一个明确结果返回**（瞥一眼返回 `True` 但不动行），以及**共享 cookie 的凭证不能在单标签页里轮换**。
- 跨进程问题集中在「谁拥有临时目录/日志文件/auth 文件」这三个共享名字上：修法统一为「独占创建 + 年龄判据 + 原子替换」。
- 下一轮视角：**升级与向后兼容**（旧数据库、旧配置文件、旧缓存目录、旧 API 响应在新版本上的行为）。
