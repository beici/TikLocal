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

- 不接直播、评论、搜索接口；不支持 TikTok 国际版链接（路由已预留
  `/tiktok/*`，需要时再扩展）。
- 博主昵称不在 API 返回数据里，文件名"博主"段用 UID 派生，不影响配对；
  后续如需真实昵称可在 sidecar 设置里调整返回字段再映射。
- sidecar 解析出的直链由 TikLocal 直接下载（带浏览器 UA 与抖音 Referer）。
  Retry 复用已解析的直链；若直链已过期（报 HTTP 403/无法访问媒体主机），
  请清空输入后重新提交原链接，TikLocal 会重新解析。
