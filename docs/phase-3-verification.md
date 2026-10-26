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

## 2. 自动化测试

```bash
poetry run pytest -q
```

第三阶段结束时的基线：**6 failed / 236 passed / 6 skipped**。这 6 个失败在本阶段之前就存在，与本阶段四个功能无关：

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
   - 右侧竖排：头像（圆形渐变，无缩略图时显示作者名首字）、心形（点赞）、星形（收藏）、信息（详情）。
   - 左下角：`@作者名`（点击跳到 `/flow?blog=<source_id>`）与文案；文案里的 `#话题` 高亮显示。
   - 底部导航：首页（高亮）/ 下载 / 设置。
   - 图集里带实况的条目显示“实况”角标。
3. 交互确认：
   - 点击右侧心形 = 点赞，旧点赞按钮状态同步，再点取消。
   - 双击画面点赞，右侧心形同步。
   - 点击星形打开合集选择弹窗，勾选后星形变为已收藏，取消后恢复。
   - 点击信息按钮进入详情页。
   - 长文案出现“展开”，点开后为全屏文案层，可用“收起”、Esc 或点击空白处关闭。
4. 关闭开关后刷新 `/flow`，旧布局恢复，抖音层消失。

数据来源校验：`GET /api/feed/mix` 的条目应带 `author_name`、`author_url`、`caption_text`；作者名来自媒体源目录名，文案来自文件名。

## 4. 功能二：TikTokDownloader 博主批量下载

1. 打开 `/download`，顶部三态开关为 Video（yt-dlp）/ Images（gallery-dl）/ 博主（TikTokDownloader）。
2. 选“博主”，出现“作品 / 喜欢 / 收藏”三个标签与数量下拉（18/36/60/100），输入框提示可粘贴博主主页、作品或合集链接。
3. 未配置服务时直接提交，页面应给出内联提示“TikTokDownloader 未配置，请先在设置中填写服务地址”，并提供跳到设置的入口，不抛异常。
4. 在 `/settings/` → 下载 → TikTokDownloader 中填写服务地址与可选 token，保存后 `/download` 状态变为可用。
5. 粘贴博主主页链接提交，任务进入下载列表；完成后文件按 `日期-类型-作者-标题` 命名，类型取 `视频` / `图集` / `实况`，多文件作品追加 `_序号`，使实况视频与图片能重新配对。
6. 失败路径：服务不可达、链接不合法、数量越界都应给出可读错误，且不影响其它任务。
7. 该功能的真实设备联调（局域网 sidecar 真机）本阶段未完成，留待统一测试。

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
- 6 个既有测试失败见第 2 节，属既有问题。
- `docs/` 下除本文件与 `docs/tiktok-downloader.md` 外仍为英文，留待统一翻译。

## 8. 一键回归

```bash
poetry run pytest -q                     # 期望 6 failed / 236 passed / 6 skipped
poetry run pytest tests/test_i18n.py -q  # 期望全绿
```

```powershell
Get-ChildItem tiklocal\static\*.js | ForEach-Object { node --check $_.FullName }
```

浏览器侧可用 Chrome DevTools 逐个打开第 6 节列出的页面，或在控制台批量 fetch 后统计中文字符数：英文界面除用户数据外应接近 0。
