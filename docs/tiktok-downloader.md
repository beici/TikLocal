# TikTokDownloader 接入（按博主批量下载）

`/download` 页第三个开关"博主"接入 [TikTokDownloader](https://github.com/JoeanAmier/TikTokDownloader)
的 Web API，把抖音的**博主主页 / 喜欢 / 收藏**、单条**作品**、**合集**链接
解析为无水印直链，由 TikLocal 自行下载入库并登记媒体索引。

## 架构与设计决策

**纯 HTTP 客户端，不做 subprocess 拉起。** 理由：

1. TikTokDownloader 是独立 GPL-3.0 Python 项目，进程边界清晰可避免许可与
   依赖耦合（不 pip-install 进 TikLocal 环境）。
2. 用户实际部署形态是局域网内独立服务（源码 `python main.py` 选 Web API
   模式，或 Docker 部署）。TikLocal 无法也不应替远端主机拉起进程。
3. API 模式只返回 JSON 不落盘；TikLocal 拿到直链后用自己的下载管线入库，
   命名可控（见下），两件事互不干扰。

**无自动回退。** 模式是显式的：博主批量只有 TikTokDownloader 能做
（yt-dlp 不支持按主页批量拉取）；单作品链接若 sidecar 不可用，用户可手动
切回 Video 开关用 yt-dlp 下载。不做静默回退，避免同一链接走两条管线
重复下载、文案混淆。

## 安装并启动 sidecar

在一台局域网主机上（也可以就是本机）：

```bash
git clone https://github.com/JoeanAmier/TikTokDownloader
cd TikTokDownloader
python main.py        # 选 3）Web API 模式，默认监听 0.0.0.0:5555
```

首次运行会在项目目录生成配置；接口文档见 `http://<host>:5555/docs`。
默认未启用鉴权；如启用了 token 校验，在 TikLocal 配置里填同一 token。
**喜欢 / 收藏页**通常需要在 sidecar 端配置抖音 cookie（其自身
`/settings`），TikLocal 不代传 cookie。

TikLocal 不负责编排 sidecar 的安装与部署；Docker 方式见其项目 README，
TikLocal 只需要最终能得到一个可达的 `base_url`。

## TikLocal 配置

三个来源任选（优先级：环境变量 > config.yaml > 设置页手填 > 默认值；
设置页保存写入本地 download_config.json）：

**config.yaml**（`~/.config/tiklocal/config.yaml` 或 `~/.tiklocal/config.yaml`）：

```yaml
tiktok_downloader:
  base_url: 'http://192.168.x.x:5555'   # 占位示例，勿提交真实地址
  token: ''                             # sidecar 启用鉴权时填写
```

**环境变量**（Flask prefixed env，支持 JSON）：

```bash
TIKLOCAL_TIKTOK_DOWNLOADER='{"base_url": "http://192.168.x.x:5555", "token": ""}'
```

**设置页**：`/settings` → Downloads → “TikTokDownloader 服务”，显示连接
状态（`GET /token` 探活）并可编辑 base_url / token。

配置键语义：

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `base_url` | 空 | sidecar 根地址，含端口；为空即未启用 |
| `token` | 空 | 请求头 `token`，sidecar 未启用鉴权时留空 |
| `timeout_sec` | 15 | 调用 sidecar API 的超时（3–60） |
| `account_max_items` | 100 | 单次博主批量解析的作品数上限（1–500） |

## 使用

`/download` 页：开关切到 **博主**，粘贴链接，按需选 作品 / 喜欢 / 收藏
与数量（18/36/60/100），提交。支持：

- 博主主页 `https://www.douyin.com/user/<sec_uid>`（默认拉最新作品；
  数量即 `count`，最多解析 `account_max_items` 条）
- 单条作品 `…/video/<id>`、`…/note/<id>`、`…/slides/<id>`（含
  `iesdouyin.com/share/...` 与 `modal_id=` 变体）
- 合集 `…/collection/<id>`
- 短链 `https://v.douyin.com/xxxx/`（自动展开后再识别；分享文本可直接粘贴）

解析成功后每个作品生成一个下载任务（引擎 `tiktok-downloader`），进度、
取消、重试与历史和现有下载一致；下载并发沿用设置页 Concurrent Downloads
（默认 2）。

## 文件命名与实况/图集配对

下载文件名对齐 TikTokDownloader 默认归档格式
`YYYY-MM-DD HH.MM.SS-类型-博主-标题[_N].ext`（类型为 视频/图集/实况），
其中"博主"段：单作品来源为 `douyin`/`tiktok`，博主来源为 `UID<sec_uid前12位>`，
合集为 `MIX<id前12位>`。该格式与索引层的实况（mp4+jpeg 相邻配对）和
图集（同时间戳前缀分组）规则天然兼容，下载完成后自动完成配对编组。

## 故障排查

- 页面提示"未配置"：base_url 为空，先在设置页或 config.yaml 填写。
- 提示"无法连接"：`GET <base_url>/token` 探活失败，检查 sidecar 是否
  启动、地址端口是否正确、防火墙是否放行。
- 403 / token 拒绝：sidecar 启用了鉴权而 token 不一致。
- 喜欢页空白或报错：sidecar 端未配置抖音 cookie。
- 单条链接失败时，可切回 Video 开关用 yt-dlp 下载单个作品（手动回退）。

## 已知边界

- 不接直播、评论接口；搜索接口只用于「同步博主资料」（见下），不对外暴露
  搜索页面；不支持 TikTok 国际版链接（路由已预留 `/tiktok/*`，需要时再扩展）。
- 默认返回（`source: false`）的账号数据只有 uid/unique_id/昵称/签名，没有头像和
  粉丝数；需要头像与统计时按作品或账号调用时带 `source: true` 取原始字段。
  文件名"博主"段仍用 UID 派生，不影响配对。
- sidecar 解析出的直链由 TikLocal 直接下载（带浏览器 UA 与抖音 Referer）。
  Retry 复用已解析的直链；若直链已过期（报 HTTP 403/无法访问媒体主机），
  请清空输入后重新提交原链接，TikLocal 会重新解析。

## 三种运行模式的区别（后台监听 vs Web API）

TikTokDownloader / DouK-Downloader 有三种跑法，选择决定"谁来下载、文件落在哪"：

| 模式 | 入口 | 行为 | 与 TikLocal 的关系 |
| --- | --- | --- | --- |
| 终端交互模式 | `python main.py` 命令行 | 交互式输入链接，自己下载到 `root` | 互不干扰；TikLocal 只读同一目录时可用 |
| Web UI 交互模式 | 浏览器 UI | 上游 README 标注"代码尚未更新，未开放" | 不用 |
| Web API 模式 | `python main.py` → FastAPI，`/docs` | 只返回 JSON（元数据 + 直链），**不落盘** | TikLocal 调它拿直链，自己下载入库 |

用户常说的"后台监听"指终端/后台常驻那一类跑法：它按自己的
`name_format`（如 `create_time type nickname desc`）和 `root`/`folder_name`
自动把作品写进磁盘，TikLocal 只是被动看到文件。Web API 模式则相反：
sidecar 不写盘，TikLocal 拿到直链后用自己的下载管线落盘，因此能保持
`YYYY-MM-DD HH.MM.SS-类型-博主-标题[_N].ext` 命名与实况/图集配对规则，
也能在下载历史里追踪进度、重试、取消。

## 作者资料同步（头像 / 抖音号 / 粉丝数）

抖音的账号昵称不唯一（同名账号可以有很多个），而本地媒体目录只有
`UID<数字>_<昵称>_发布作品`，这个数字 UID 既不是 `sec_user_id`，也不在
`/douyin/search/user` 的结果里，所以**不能**用"搜到第一个同名账号"来认领。

落地方式（`tiklocal/services/author_profiles.py` + `tiklocal/web/author.py`）：

1. `/author/<source_id>` 默认用目录名派生：昵称取 `_发布作品` 之前的部分，
   `抖音号` 取 `UID` 后面的数字，头像用内置 `static/default-avatar.jpg`。
2. 页面上的「同步资料」按钮打开账号选择弹窗：先 `POST /api/author/<sid>/search`
   （`/douyin/search/user`，`channel: 2`，`count` 会被夹到 5–20，因为 sidecar
   要求 `count >= 5`），把候选账号（头像 / 昵称 / 抖音号 / 粉丝 / 获赞）列出来。
3. 用户点中正确的那一个，前端把 `unique_id` / `uid` 一起回传
   `POST /api/author/<sid>/refresh`；服务端只在**完全相等**的
   unique_id / short_id / 昵称上匹配，多个精确命中时返回 409 让用户再选，
   绝不猜。命中后把资料写进 `instance/authors/<source_id>.json`，头像字节
   缓存为 `instance/authors/<source_id>.jpg`（抖音头像 URL 是带签名的临时地址，
   只能缓存图片本体，不能缓存 URL）。
4. 头像通过 `GET /author/<source_id>/avatar` 输出（有缓存发缓存，无缓存发内置
   默认头像，`max-age=300`）。抖音模式下 Flow 右栏头像与作者名都指向
   `/author/<source_id>`，点进去就是本地作者主页。

sidecar 未配置 `base_url` 时按钮不出现，页面保持目录名派生的纯本地形态。

### 精确同步：按链接 + UID 校验（推荐）

实测结论（DouK-Downloader 5.8.stable）：

- `/douyin/search/user` 用**抖音号**当关键字并不精确，返回的仍是同名的模糊结果，
  所以不能用「抖音号搜索」认领账号；
- `/douyin/account`（`source: true`）返回的 author 块被**脱敏**：只有
  `uid` / `nickname` / `avatar_thumb` / `sec_uid`，`unique_id`、`short_id`、
  `follower_count`、`total_favorited` 全是 `None`，而且经常要 20 秒以上甚至超时；
- `/douyin/detail`（`source: true`）返回的 `data.author` 才是完整资料：
  `unique_id`（真实抖音号）、`short_id`、`uid`（等于目录名里的数字 UID）、
  `follower_count`、`total_favorited`、`signature`、`avatar_larger/medium/thumb`。

因此同步按「链接 → 作品 → 作者块」走，并强制 UID 校验：

1. **快路径（推荐）**：直接给作品链接 `/video/<id>`、`/note/<id>`、`/slides/<id>`
   或裸作品 ID。`parse_aweme_id()` 取出 ID 后只调一次 `/douyin/detail`，
   实测约 5~8 秒。
2. **慢路径**：给作者主页链接 `/user/<sec_user_id>`。`parse_profile_sec_uid()`
   取出 sec_uid，先 `/douyin/account`（`pages: 1`）拿第一条作品的 `aweme_id`，
   再走 `/douyin/detail`。该路径超时上限 `AUTHOR_ACCOUNT_TIMEOUT_SEC = 150`，
   失败时提示改用作品链接。
3. 拿到 author 块后，`store_author_block()` 会校验 `author.uid` 与目录名里的
   数字 UID 完全一致；不一致直接报错并且**不写任何文件**，避免把别人的头像
   和抖音号记到本地作者上。
4. 批量入口：`POST /api/author/sync`，body 支持
   `{items: [{source_id?, profile_url?, aweme_id?}]}` 或
   `{text: "source_id|<链接>"}`（`#` 开头为注释）。不带 `source_id` 时用返回的
   UID 反查媒体源。设置页「作者资料」卡片就是它的界面：
   点「刷新」列出媒体源，粘贴若干链接后点「同步」逐条显示结果。

### 自动绑定主页 + 一键更新视频

每个媒体源现在自己记住主页地址，同步不再依赖「每次粘贴一批链接」：

- `POST /api/author/<sid>/bind` `{profile_url}` 只做校验与保存：主页链接存
  `https://www.douyin.com/user/<sec_user_id>`，作品链接则改存该作品的
  `sec_user_id` 主页地址；写入 `instance/authors/<sid>.json` 的 `profile_url`。
- `POST /api/author/<sid>/sync` `{profile_url?}` 用已绑定（或本次传入）的链接
  走上面的快/慢路径，仍然强制 UID 校验。
- `POST /api/author/<sid>/update-videos` `{profile_url?, count?}` 一键更新最新视频：
  `TikTokDownloadService.enqueue_latest_videos()` 用 `sec_user_id` 调
  `/douyin/account`（默认 `count: 18`、`pages: 1`）拿作者时间线，把作品的
  `_title_key()`（去掉 emoji / 标点 / 空白的标题指纹）与本地 `post_title()`
  逐个比对，只为本地没有的作品 `enqueue_from_url('https://www.douyin.com/video/<aweme_id>')`，
  已存在的不重复下载。实测 42 个作品取回 13.9 秒、整轮 16.2 秒，全部已在本地时
  返回 `已是最新`。
- `GET /api/author/sources` 的每一项都带 `profile_url` 与 `local_count`，
  作者主页的「同步」按钮打开两段式菜单（同步博主资料 / 一键更新最新视频），
  设置页「作者资料」卡片每行是一个媒体源：已绑定的主页链接直接预填在输入框里，
  右侧是「同步资料」「更新视频」，顶部还有「全部同步资料」「全部更新视频」。
  已经同步过资料的老媒体源会从缓存资料里的 `sec_uid` 自动推导出主页地址
  （`guess_profile_url()`），不需要重新粘贴。

## 下载链接清单（与 TikTokDownloader 共用 user-url.txt）

`tiklocal/services/url_list.py` 维护一份本地清单 `instance/download-urls.txt`
（每行一个链接，`#` 开头为注释），并记住一个可选的「服务器文件路径」
（`instance/download-urls.json`），用于和 sidecar 所在的机器共用同一个清单：

- `GET /api/download/urls` 取快照；`POST /api/download/urls` 支持
  `replace` / `add` / `remove` / `clear` / `source`（设置同步文件路径）；
- `GET /api/download/urls/export` 导出 `download-urls.txt` 附件；
- `POST /api/download/urls/sync` 的 `direction` 为 `pull`（从文件读回）
  或 `push`（原子写回文件）；
- `POST /api/download/urls/queue` 把清单里每个链接按指定引擎逐个入队。

约束：只接受 http/https、长度 ≤ 2048、最多 5000 行、去重保序；同步文件必须是
绝对路径且不是目录，读取上限 2 MB。写入使用临时文件 + `os.replace` 的原子替换。
