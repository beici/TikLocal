# 第 36 轮审计：首次运行与空状态

日期：2026-10-29　起点：`0ce5e47`（第 35 轮）　视角：第一次打开时会发生什么

## 一、方法与范围

七个域里四个由只读子代理并行审查，我（Lead）跑第五个域（空态探针，真起服务）。

| 域 | 范围 |
| --- | --- |
| Q1 | 首次运行与冷路径：没有配置、没有数据库、没有媒体目录时启动 |
| Q2 | 空数据渲染：库为空、回收站为空、某一天没有照片时的页面 |
| Q3 | 空态与错误态呈现：加载失败与「确实没有」是否可区分 |
| Q4 | 一次性操作：第一次添加文件夹、第一次下载、第一次收藏 |
| Q5 | 空态探针（我）：空库、缺失目录、指向文件的 `MEDIA_ROOT`、空库 CLI |

探针环境注意：venv 里的 python 不能写 `$env:TEMP`（DSH 沙箱只放行工作区），探针实例与媒体目录都建在仓库内并事后删除；登录要先取 `_csrf_token` 再 POST。

## 二、问题总览

| 域 | P0 | P1 | P2 | P3 |
| --- | --- | --- | --- | --- |
| Q1 首次运行 | 0 | 0 | 2 | 4 |
| Q2 空数据渲染 | 0 | 0 | 2 | 3 |
| Q3 空态与错误态 | 0 | 0 | 4 | 7 |
| Q4 一次性操作 | 0 | 0 | 2 | 3 |
| Q5 探针 | 0 | 0 | 0 | 0 |

去重后 25 条（Q3-2≡Q4-1、Q3-3≡Q4-3）：修复 12 条，转 backlog 13 条。

## 三、P1

无。

## 四、修复的 P2（4 条）

### Q1-1 `--media-source` 单独使用时多出一个 `default` 源

`tiklocal/run.py` 在没有显式目录时把 `media_path` 设成第一个源的路径，再原样传给 `create_app`；`build_media_sources(MEDIA_ROOT, MEDIA_SOURCES)` 于是给同一个目录又加了一个 `default` 源。后果是一串身份分裂：设置页多一行删不掉的重复项、下载记录写 `@default/...` 而扫描登记 `@photos/...`、收藏与续播点两边互不可见。

修法：`run.py` 在 `app_config` 里显式标记 `MEDIA_ROOT_DERIVED_FROM_SOURCES`，`create_app()` 只在没有这个标记时才把 `MEDIA_ROOT` 当独立源。这样「`MEDIA_ROOT` + 额外命名源」（测试与旧配置的合法组合）保持原样。

### Q1-2 实例数据库打不开时只留裸 traceback

只读数据目录、磁盘满、被更新版本写过的数据库，都会在 banner 已经说「正在启动」之后抛 `sqlite3.OperationalError`/`DatabaseVersionError`，用户看到的是栈而不是「哪个目录、为什么」。`run.py` 现在捕获 `OSError`/`sqlite3.Error`/`DatabaseVersionError` 并走 `_fatal()`：写日志、退出 1、一句话说明数据目录。

### Q2-1 回收站目录读不了时 `/api/trash` 直接 500

`tiklocal/services/trash.py` 的 `entries()` 对 `trash_dir.iterdir()` 没有守卫：网络盘断开或权限变化时整个回收站接口 500，前端只显示「加载失败」而没有「回收站不可用」。改为捕获 `OSError`、记 warning、返回空列表——空列表加上前端已有的空态文案，比 500 更接近事实。

### Q3-1 下载历史加载失败后永远停在骨架

`tiklocal/static/download_page_controller.js` 的 `refreshJobs()` 失败只 toast 一次然后退避重试，`#history-list` 的骨架屏（`templates/download.html:94-96`）没人清。首次加载就失败的用户看到的是永久「加载中…」。现在失败时把骨架换成错误态（`download.history_load_failed` + 重试按钮），重试成功再恢复正常渲染。

## 五、修复的 P2（续）：首页与设置页

### Q3-2 / Q4-1 首页货架失败后骨架永不清

`home_page_controller.js` 用 `Promise.allSettled` 并发取四个货架，失败分支只 `return`，`renderShelfUnavailable()` 从未被调用。首屏某一路失败（比如电台服务慢）时那块永远空着，用户以为库里没有内容。现在每路失败各自渲染「暂时无法加载」并给出重试。

### Q3-3 / Q4-3 凭据弹窗加载失败仍显示「正在加载凭据…」

`settings_page_controller.js` 的 catch 只关掉 spinner，文案没变，用户会一直等。改为显示错误文案 + 重试。

### Q4-2 库里明明有内容却显示「还没有媒体」

`library_page_controller.js` 在 scope 过滤（只看图片/只看视频）结果为空时也复用整库空态文案。改为按当前 scope 给不同文案，并在整库非空时提示「这个筛选下没有内容」。

## 六、修复的 P3（4 条）

- **Q1-3** `tiklocal serve --version` 之前不存在（只有顶层 `--version`），补上，脚本化部署能直接问版本。
- **Q1-5** 启动 banner 无条件打印 `Access authentication: enabled`，未设口令时也在说谎；改为读 `app.extensions['auth_enabled']`。
- **Q2-3** 索引跳过「文件名带空白」的判据用了 `strip()`，把**前导**空格的合法文件名也永久排除在索引外（Windows 只吞尾部空白）；改为 `rstrip()`。
- **Q3-4** yt-dlp 缺失时下载页 toast 用了 `download.alert.image_not_ready`（说的是图片组件），补 `download.alert.video_not_ready` 并改用它。
- **Q4-5** toast 容器在第一次 toast 时才创建，同帧插入的 `aria-live` 区域不会被读屏播报；改为脚本加载时就建好空容器。

## 七、未修（转 backlog，13 条）

| 条目 | 位置 | 为什么不在这轮修 |
| --- | --- | --- |
| Q1-4 `--help` 文案未统一 | `tiklocal/run.py` | 纯文案，需与文档一起改 |
| Q1-6 配置路径错误提示缺 `config.yaml` 示例 | `tiklocal/run.py` | 同上 |
| Q2-2 详情页没有「文件不见了」状态 | `templates/detail.html`、`detail_page_controller.js` | 需要新增整块 UI 与 i18n，改动面比其它条大 |
| Q2-4 字幕遇到非图片文件返回 502 | `tiklocal/web/captions.py` | 语义变更（502→400）要同步前端与用例 |
| Q2-5 时间线空日期按钮弹出空气泡 | `library_page_controller.js` | 需要先确认交互预期 |
| Q3-5 电台空态文案与库空态重复 | `i18n_pages_radio.py` | 文案类 |
| Q3-6 搜索「无结果」与「请求失败」同文案 | `search_page_controller.js` | 要新增错误态分支 |
| Q3-7 下载错误态下仍显示 `download.empty_copy` | `download_page_controller.js` | 与 Q3-1 同文件，留到下一轮一起理 |
| Q3-8/Q3-9 两处死 CSS | `radio.css`、`library.css` | 无功能影响 |
| Q3-10 空态图标尺寸不一致 | 多处 CSS | 视觉微调 |
| Q3-11 `download.empty_title` 在筛选为空时不准 | `download_page_controller.js` | 文案类 |
| Q4-4 `.collections-empty` 死选择器 | `collections.css` | 无功能影响 |

## 八、实现要点

1. **「有源」不等于「源就是根」**：`--media-source` 单独使用时 `MEDIA_ROOT` 只是占位，必须显式标记，否则 `build_media_sources()` 会把它当成第二个源。
2. **骨架屏是承诺**：任何 `loading` 状态都必须有失败出口，否则失败会被渲染成「永远在加载」。
3. **空态要区分「没有」与「没取到」**：两者共用一套文案时，用户无法判断该刷新还是该导入。
4. **只在必要时才 `strip()`**：文件名前导空格在 Windows 上是合法的，判据过宽会永久丢文件。

## 九、验证

- 新增 `tests/test_round36_regressions.py`（3 条）：命名源不再多出 `default`、失败后源列表回滚、带前导空格的 mp4 能进索引。
- 全量 `pytest -q`：**626 passed / 8 skipped in 228.43s**（第 35 轮基线 623，新增 3 条）。
- 手工探针：空库八个页面全 200；`MEDIA_ROOT` 指向不存在的目录会被创建；指向文件时 `available:false` 且页面仍 200；空库 `thumbs`/`dedupe` 退出码 0。

## 十、覆盖率缺口

- 没有真实浏览器跑空态截图，骨架屏与错误态的视觉验证靠代码阅读与单测。
- 「首次运行」只在 Linux 风格的临时目录上跑过，没有验证 Windows 首次启动时的证书生成路径。
- 一次性操作只覆盖了添加文件夹与空库 CLI，下载与收藏的首次路径仍是人工阅读。

## 十一、下轮建议

视角建议：**依赖与版本边界**——Pillow/FFmpeg/SQLite/浏览器 API 的版本假设在旧环境里是否成立（例如 `AbortSignal.timeout`、`Array.prototype.at`、`structuredClone` 在 Safari 15 上）。
