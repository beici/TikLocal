# 第三阶段功能验证指南

本文件把第三阶段四个功能的验证步骤集中到一处，方便一次跑完：抖音信息流模式、TikTokDownloader 博主批量下载、合集筛选与批量整理、中英文双语界面。

## 适用范围

| 功能 | 入口 | 主要实现 |
| --- | --- | --- |
| 抖音信息流模式 | `/flow`（设置里开启） | `tiklocal/templates/flow.html`、`tiklocal/static/flow_page_controller.js`、`tiklocal/static/flow.css`、`tiklocal/web/flow.py` |
| 博主批量下载 | `/download` 的“博主”档位 | `tiklocal/services/tiktok_downloader.py`、`tiklocal/web/downloads.py`、`tiklocal/static/download_page_controller.js` |
| 合集筛选与批量整理 | `/library?view=explore`、`/favorite` | `tiklocal/web/library.py`、`tiklocal/services/collections.py`、`tiklocal/static/library_page_controller.js` |
| 中英文界面 | 设置 → 外观 → 语言 | `tiklocal/i18n.py`、`tiklocal/i18n_pages_*.py`、`tiklocal/static/i18n.js` |

## 1. 环境准备

需要 Python 3.10 以上、Poetry，以及一份本地媒体库。若要验证抖音模式与博主下载，媒体库里应有一个抖音博主导出目录，并以独立媒体源挂载：

```bash
poetry install

# bash
export TIKLOCAL_INSTANCE=/path/to/instance
poetry run tiklocal serve --port 8000 --media-source "blogger=/path/to/<博主导出目录>"
```

```powershell
# PowerShell
$env:TIKLOCAL_INSTANCE="E:\path\to\instance"
poetry run tiklocal serve --port 8000 --media-source "blogger=E:\path\to\<博主导出目录>"
```

- `TIKLOCAL_INSTANCE` 指定实例目录，避免污染 `~/.tiklocal`；测试脚本与临时实例都应指向仓库内或临时目录。
- 首次访问会要求设置访问密码，之后用该密码登录。
- 挂载为 `blogger` 的媒体源，其文件 URI 形如 `@blogger/<文件名>`，界面上的作者名取自源目录名，文案取自文件名。

### 1.1 可选外部工具（缩略图与下载引擎）

```bash
pip install imageio-ffmpeg gallery-dl yt-dlp   # 或：poetry add imageio-ffmpeg gallery-dl yt-dlp
```

- `imageio-ffmpeg` 自带一份 ffmpeg 二进制，视频缩略图（作者主页网格、图集封面、收藏页）靠它抓帧；
  缺失时 `/thumb` 只能返回 1×1 占位图，界面会出现黑块。
- `gallery-dl`（图片下载）与 `yt-dlp`（视频下载）用于 `/download` 的两个引擎；缺失时页面会显示
  “组件未就绪”，博主下载（TikTokDownloader）不受影响。
- 程序不依赖 PATH：`tiklocal/services/download_tools.py:resolve_tool()` 依次尝试
  环境变量（`TIKLOCAL_FFMPEG` / `TIKLOCAL_GALLERY_DL` / `TIKLOCAL_YT_DLP`）、`PATH`、
  当前解释器同目录与 `Scripts`/`bin`、`imageio_ffmpeg.get_ffmpeg_exe()`、最后
  `python -m yt_dlp` / `python -m gallery_dl`。

## 2. 自动化测试

```bash
poetry run pytest -q
```

第三阶段结束时的基线：**6 failed / 354 passed / 6 skipped**。这 6 个失败在本阶段之前就存在，与本阶段四个功能无关：

- `tests/test_auth.py::test_auth_store_hashes_password_and_increments_revision`
- `tests/test_library_upgrade.py::test_library_images_use_bounded_cached_thumbnails`
- `tests/test_media_state.py::test_dimensions_are_probed_in_background_and_refreshed_after_change`
- `tests/test_radio_client.py::test_device_auth_store_only_persists_token_hash`
- `tests/test_thumbnails.py::test_cli_cache_is_reused_by_web_and_isolated_between_sources[False]`
- `tests/test_thumbnails.py::test_cli_cache_is_reused_by_web_and_isolated_between_sources[True]`

分功能单独运行：

```bash
poetry run pytest tests/test_feed_mix.py tests/test_douyin_features.py -q   # 抖音模式
poetry run pytest tests/test_tiktok_downloader.py tests/test_download.py -q # 博主批量下载
poetry run pytest tests/test_download_urls.py -q                           # 下载链接清单
poetry run pytest tests/test_author_page.py tests/test_author_sync.py -q    # 作者主页与资料同步
poetry run pytest tests/test_collections.py -q                             # 合集筛选与整理
poetry run pytest tests/test_i18n.py -q                                    # 中英文界面
```

前端脚本改动后先做语法检查：

```powershell
Get-ChildItem tiklocal\static\*.js | ForEach-Object { node --check $_.FullName }
```

## 3. 功能一：抖音信息流模式

1. 打开 `/settings/`，在“外观”下找到“抖音信息流”开关并打开，页面提示已开启。
2. 打开 `/flow`，逐项确认：
   - 页面铺满屏幕，旧的侧边栏、浮动 dock、移动端头部、自定义控制条、字幕面板全部隐藏。
   - 顶部：`推荐`（高亮带下划线）/ `下载` 两个标签；话题流下变为返回箭头 + `# 话题 作品数`。
   - 右侧竖排：头像（内置默认头像，点击进入本地作者主页）、心形（点赞）、星形（收藏）、信息（详情），四个都是项目原生圆形按钮。
   - 左下角：`@作者名`（点击进入 `/author/<source_id>`）与文案；文案里的 `#话题` 与正文同色（白），可点击进入话题流。
   - 底部导航：首页 / 设置 / 我的。
   - 图集里带实况的条目显示“实况”角标。
3. 交互确认：
   - 点击右侧心形 = 点赞，旧点赞按钮状态同步，再点取消。
   - 双击画面点赞，右侧心形同步。
   - 点击星形打开合集选择弹窗，勾选后星形变为已收藏，取消后恢复。
   - 点击信息按钮进入详情页。
   - 长文案出现“展开”，点开后为全屏文案层，可用“收起”、Esc 或点击空白处关闭。
   - 上下滑动切换作品时，右栏心形/星形先复位再同步，不会残留上一个作品的选中态（`is-syncing` 期间关闭过渡与爆裂动画）。
   - 开启开关后全站都留在信息流里：作者主页、媒体库、收藏里的作品格子点进去不再打开原生播放页，
     而是跳到 `/flow?blog=<媒体源>&start=<媒体>` 并直接停在这条作品上；直接访问 `/detail/<媒体>` 或
     `/image?uri=<媒体>` 也会被重定向到同一条作品（图集会停在点开的那一张）。
     关闭开关后这两种链接恢复原来的播放页 / 看图页。
4. 话题流：点击文案里的任意 `#话题` → 地址变为 `/flow?tag=<话题>`，顶部只显示
   `# 话题 N 个作品` 与返回箭头，列表只剩文案含该话题的作品；点返回回到推荐流。
   不存在的标签显示“这个话题下还没有作品。”与“打开媒体库”入口。
5. 作者主页：点击右栏头像或 `@作者名` → `/author/<source_id>`，页面为深色抖音风：
   圆形头像（有缓存用真实头像，否则内置默认头像）、昵称、`抖音号:`、`作品 N` 统计、
   单 `作品` 标签页、三列方形网格。无关注/私聊按钮。
   - 网格封面全部走 `/thumb` 缩略图（视频由 ffmpeg 抓帧，图集取首图），滚动到底不应出现黑块；
     视频格右下角有播放角标。
   - 未同步资料时 `抖音号:` 显示目录里的数字 UID（本地回退）；同步后显示真实抖音号。
    - 共享根目录（一个媒体源里按博主各放一个导出文件夹，例如 `~/Downloads/douyin`）走另一条路径：
      `/author/<source_id>` 不再把目录名当昵称，而是列出这个源里的每位作者（头像、昵称、`N 个作品`），
      点进去是 `/author/<source_id>/<folder>` —— 只列该博主自己的作品，头像、抖音号、绑定链接也各留一份；
      根目录里不属于任何导出文件夹的散落文件仍然显示在同一页下方。
6. 关闭开关后刷新 `/flow`，旧布局恢复，抖音层消失。

数据来源校验：`GET /api/feed/mix` 的条目应带 `author_name`、`author_url`、`author_page_url`、`caption_text`；作者名来自媒体源目录名（共享根目录里来自导出文件夹名），文案来自文件名。`author_url` 是话题/博主过滤用的 `/flow?blog=<源>`，`author_page_url` 才是头像与昵称该跳的作者页。话题流用 `GET /api/feed/mix?tag=<话题>`，返回体多一个 `tag` 字段。

### 3.1 作者资料同步（需要 sidecar）

前置：`/settings/` → 下载 → TikTokDownloader 填好服务地址（可选 token）。未配置时设置卡片与作者页按钮都不出现，页面保持目录名派生的纯本地形态。

1. 打开 `/settings/`，在「作者资料」卡片里点「刷新」，列出全部媒体源：每行是头像、显示名、
   `抖音号:`（未同步时是目录里的数字 UID）、同步状态、本地作品数（`本地 N 个作品`），
   下面是一行**已绑定的主页链接**（输入框可直接改），右侧是「同步资料」「更新视频」；
   卡片顶部是「全部同步资料」「全部更新视频」。已经同步过的媒体源会自动填好主页地址。
2. 在输入框里粘贴链接，支持三种写法：
   - `作者主页链接`，例如 `https://www.douyin.com/user/<sec_uid>` —— 慢路径，需要先读作者作品列表（可能 20 秒以上，超时上限 150 秒）；
   - `作品链接`，例如 `https://www.douyin.com/video/<作品ID>` —— **推荐**，一次请求约 5~8 秒；
   - 批量区还支持 `媒体源ID|链接` 逐行写法，也可以只粘贴链接（用返回资料里的数字 UID 反查目录）。
   点「同步资料」后链接会绑定到这个媒体源，下次刷新直接预填，不用再粘。
3. 成功时该行状态变为「已同步」、`抖音号:` 变成真实抖音号（例如 `demo0001`）、头像换成真实头像；
   作者页 `/author/<source_id>` 的「同步」按钮打开两段式菜单（同步博主资料 / 一键更新最新视频），
   和设置页是同一套接口。
4. 「更新视频」= 一键更新最新视频：按已绑定的主页读取作者时间线（默认 18 条），
   用标题指纹和本地作品比对，只把**本地没有的**作品加入下载队列；全部已在本地时提示「已是最新」。
   实测 42 个作品读取约 13.9 秒、整轮 16.2 秒。
5. 安全校验：返回资料里的 `uid` 必须与目录名里的数字 UID 完全一致，不一致直接报错并且**不写入任何文件**。
6. 头像落在 `instance/authors/<key>.jpg`，资料（含 `profile_url`）落在 `instance/authors/<key>.json`，删掉这两个文件即回到默认头像 + 目录名派生。`<key>` 是该作者的主页缓存键：媒体源本身就装一位博主时是 `<source_id>`，共享根目录里是 `<source_id>/<folder>`，所以同一目录下的两位博主互不覆盖。
7. 服务不可达 / 主页读取超时时给出对应提示（超时会建议改用作品链接），不影响页面其它部分。

## 4. 功能二：TikTokDownloader 博主批量下载

1. 打开 `/download`，顶部三态开关为 Video（yt-dlp）/ Images（gallery-dl）/ 博主（TikTokDownloader）。
2. 选“博主”，出现“作品 / 喜欢 / 收藏”三个标签与数量下拉（18/36/60/100），输入框提示可粘贴博主主页、作品或合集链接。
3. 未配置服务时直接提交，页面应给出内联提示“TikTokDownloader 未配置，请先在设置中填写服务地址”，并提供跳到设置的入口，不抛异常。
4. 在 `/settings/` → 下载 → TikTokDownloader 中填写服务地址与可选 token，保存后 `/download` 状态变为可用。
5. 粘贴博主主页链接提交，任务进入下载列表；完成后文件按 `日期-类型-作者-标题` 命名，类型取 `视频` / `图集` / `实况`，多文件作品追加 `_序号`，使实况视频与图片能重新配对。
6. 失败路径：服务不可达、链接不合法、数量越界都应给出可读错误，且不影响其它任务。
7. 该功能的真实设备联调（局域网 sidecar 真机）本阶段未完成，留待统一测试。

### 4.1 下载链接清单（导入 / 导出 / 与服务器文件同步）

1. `/download` 顶部出现「链接清单」卡片：标题旁显示 `N 个链接`，工具栏为「全部下载」「导出」「清空」。
   - 超过 10 条时列表自动分页：底部分页条含「每页 10 / 20 / 50 / 100 条」下拉、上一页 / 下一页、
     页码按钮与「跳至 N 页」输入框，右侧显示「第 X / Y 页 · 共 N 条」；
   - 页大小默认 10，选择会记住（`localStorage['tiklocal.download.urlListPageSize']`）；不超过 10 条时分页条整体隐藏。
2. 展开「导入链接」，粘贴多行文本后点「导入」：
   - 空行与 `#` 开头的注释被忽略；非 http(s)、缺主机、超长（>2048）的链接被丢弃；
   - 重复链接自动去重，顺序保留，上限 5000 行；
   - 成功后提示「已导入 N 个链接。」，列表每行显示平台徽标（`DY` / `TT`）与「移除」按钮。
3. 「导出」直接下载 `download-urls.txt`（`text/plain` 附件），内容与清单一致。
4. 「与服务器文件同步」：填入服务器上的清单路径（例如 `/volume5/data/tiktok/TikTokDownloader-master/download/user-url.txt`），
   点「保存路径」记住它；「写回文件」把本地清单原子写入该文件，「从文件读取」把文件内容拉回本地清单。
   - 只接受绝对路径，且不能是目录；文件超过 2 MB 会拒绝读取。
   - 外部编辑器改完该文件后点「从文件读取」即可看到变化（与 TikTokDownloader 共用同一个 user-url.txt）。
5. 「全部下载」把清单里每个链接按当前引擎逐个入队，返回入队成功/失败数量；「清空」需二次确认（弹窗提示只清链接、不删已下载文件）。
6. 接口层同样可用：`GET/POST /api/download/urls`、`GET /api/download/urls/export`、`POST /api/download/urls/sync`、`POST /api/download/urls/queue`。

### 4.2 最近下载（分页 / 多选清除）

1. 「最近下载」列表与链接清单共用同一套分页条：超过 10 条自动分页，可选 10 / 20 / 50 / 100 条一页，
   支持上一页、下一页、页码与跳页，页大小分别记忆在 `localStorage['tiklocal.download.historyPageSize']`。
2. 点列表下方的「清除」进入多选：每行左侧出现勾选框，点击整行也可切换选中，底部浮出操作条
   （`全选` / `已选 N 项` / `取消` / `删除`）。
   - `全选` 只选当前页；当前页已全选且还有其它页时按钮变成「全选全部 N 条」，再点选中所有记录，全选后变为「取消全选」；
   - 选中状态跨页保留，按 `Esc` 或「取消」退出多选。
3. 点「删除」弹出选择面板，两种删除方式：
   - 「仅删除记录」：保留已下载的本地文件；
   - 「删除记录及文件」：记录删除的同时把文件移入该媒体源的回收站（`.tiklocal-trash`，30 天内可恢复）；
     选中的记录都没有本地文件时该选项置灰，并提示「没有可删除的本地文件。」。
4. 删除完成后给出结果提示（例如「已删除 1 条记录，1 个文件已移入回收站」）；
   正在下载的记录会被跳过并在提示里说明；删掉当前页最后一条后分页自动回到有效页。
5. 接口：`POST /api/download/jobs/delete`，请求体 `{"ids": [...], "delete_files": true|false}`，
   返回 `{deleted, skipped, files: {moved, missing, failed}}`；单个记录的删除仍走 `DELETE /api/download/jobs/<id>`。

## 5. 功能三：合集筛选与批量整理

1. `/library?view=explore` 顶部出现合集 chip 行：包含“全部”与已有合集；无合集时显示创建入口。
2. `/favorite` 顶部同样有 chip 行，点击某个合集后 URL 变为 `/favorite?collection=<id>`，只显示该合集的媒体，当前 chip 高亮。
3. 点击“整理”进入多选：选中若干图块后出现操作条（已选 N 项 / 加入合集 / 移动到合集 / 移出本合集 / 全选 / 完成）。
4. 选择“加入合集”，在弹层里勾选目标合集并确认，操作条消失；用 `GET /api/collections/by-media?uri=…` 可确认该媒体同时属于两个合集。
5. “移出本合集”只解除当前合集关系，点赞状态与其它合集关系保持不变；删除合集也不会删除媒体或点赞。

## 6. 功能四：中英文界面

1. `/settings/` → 外观 → 语言，点“English”，页面立即切换为英文；点“中文”切回。
2. 刷新后语言保持不变：选择写入 `lang` cookie（一年）与 `localStorage['tiklocal:lang']`。
3. 首次访问（无 cookie）时按 `Accept-Language` 决定：`zh*` 用中文，`en*` 用英文；浏览器首选其它语言时回落到默认中文。
4. 逐页确认两种语言下文案完整、无未翻译残留：`/`、`/flow`、`/library`、`/library?view=explore`、`/favorite`、`/collections`、`/download`、`/radio`、`/settings/`、`/login`。
   - 允许出现的中文：用户自己的合集名等数据、语言选项里的“中文”本身。
   - 英文界面下不应出现成段中文；`<html lang>` 应与当前语言一致。
5. 日期与月份：`/library` 时间线在中文下显示“10月”，英文下显示“October”；月详情页标题同理。
6. 服务端消息也随语言切换，可直接调用接口核对：

```bash
# 中文
curl -s -X POST http://127.0.0.1:8000/api/download/jobs -H 'Content-Type: application/json' -H 'Accept-Language: zh-CN' -d '{"url":"file:///tmp/a.mp4"}'
# 英文
curl -s -X POST http://127.0.0.1:8000/api/download/jobs -H 'Content-Type: application/json' -H 'Accept-Language: en-US' -d '{"url":"file:///tmp/a.mp4"}'
```

预期分别为“仅支持 HTTP 和 HTTPS 链接。”与“Only HTTP and HTTPS URLs are supported.”。提示词配置接口 `/api/ai/prompt-config` 的校验错误同样双语。

## 7. 已知限制

- 后台 worker 线程里产生的 cookie 相关报错没有请求上下文，固定输出中文；它们会写进下载历史，英文用户可能看到中英混排的一行。
- 下载历史的 `error_message` 是持久化字段，历史记录不会随语言切换重译。
- 少数内部状态文案（如“The download manager is closed.”）保留英文，因为已有测试按字面匹配。
- 6 个既有测试失败见第 2 节，属既有问题。其中 `test_thumbnails` 两个用例现在的失败点是
  `cache_count == 2`（`delete_thumbnail` 按规范化 key 删除，与 `generate_thumbnail` 实际写入的文件名不一致），
  与阶段三功能无关。
- 作者资料同步的主页链接路径依赖 sidecar 的 `/douyin/account`，实测该接口可能 20 秒以上甚至超时；
  首次绑定建议用作品链接（`/video/<id>`）走快路径，绑定之后「更新视频」才会用到作者时间线。
- `docs/` 下除本文件与 `docs/tiktok-downloader.md` 外仍为英文，留待统一翻译。

## 8. 一键回归

```bash
poetry run pytest -q                     # 期望 6 failed / 354 passed / 6 skipped
poetry run pytest tests/test_i18n.py -q  # 期望全绿
```

```powershell
Get-ChildItem tiklocal\static\*.js | ForEach-Object { node --check $_.FullName }
```

浏览器侧可用 Chrome DevTools 逐个打开第 6 节列出的页面，或在控制台批量 fetch 后统计中文字符数：英文界面除用户数据外应接近 0。
