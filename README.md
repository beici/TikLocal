<h1 align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/ChanMo/TikLocal/main/brand/tiklocal-lockup-dark.svg">
    <img src="https://raw.githubusercontent.com/ChanMo/TikLocal/main/brand/tiklocal-lockup.svg" alt="TikLocal" width="260">
  </picture>
</h1>

<p align="center">
  <strong>Your own TikTok for the videos and photos you already have.</strong><br>
  Self-hosted, offline, no account, no cloud.
</p>

<p align="center">
  <a href="https://pypi.org/project/tiklocal/"><img src="https://img.shields.io/pypi/v/tiklocal" alt="PyPI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/pypi/l/tiklocal" alt="License"></a>
</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/ChanMo/TikLocal/main/docs/screenshots/hero.jpg" alt="TikLocal Library grid on desktop with the Flow swipe feed on a phone">
</p>

TikLocal turns a folder of media into a private feed. Run one command on your
computer or NAS, open it on your phone, and swipe through your own videos and
photos full screen — or browse them as a Pinterest-style grid and a year/month
timeline. Nothing leaves your machine.

## Quick start

```bash
pip install tiklocal
tiklocal ~/Videos
```

Open `http://<your-computer>:8000` on any phone, tablet, or browser on the same
network and sign in with the default access password `admin666`. Change it
before you expose the server to anyone else.

## What it does

- **Flow** — a full-screen vertical feed that mixes videos and images. Swipe to
  the next item, double-tap to favorite, hold a video to play it at 2×, and drag
  sideways to seek.
- **Library** — a year/month timeline for multi-year archives and an Explore grid
  with random images, latest videos, and large files.
- **Home** — pick up where you left off, see what happened *On This Day*, and
  open your collections.
- **Favorites and collections** — save anything from any view; long videos resume
  where you stopped, across devices.
- **Downloads** — paste a link and TikLocal fetches it into your library with
  `yt-dlp` or `gallery-dl`.
- **Radio** — an ambient audio player for the music in the same folders.
- **Many folders, one library** — merge several media directories; deletions go
  to a trash you can undo.
- **相关媒体与描述搜索（实验性，默认关闭）** — 视频详情页的相关推荐、图库
  详情页的相似照片，以及 Library 的"按描述搜索"；详见下文
  [从 spooly 移植的功能](#从-spooly-移植的功能)。

<p align="center">
  <img src="https://raw.githubusercontent.com/ChanMo/TikLocal/main/docs/screenshots/flow-mobile.jpg" alt="Flow: a full-screen waterfall video with like, save and info actions" width="260">
  <img src="https://raw.githubusercontent.com/ChanMo/TikLocal/main/docs/screenshots/home-mobile.jpg" alt="Home: a collage of recent photos with entry points to Flow and Radio" width="260">
  <img src="https://raw.githubusercontent.com/ChanMo/TikLocal/main/docs/screenshots/library-mobile.jpg" alt="Library: a two-column grid of landscape and city photos" width="260">
</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/ChanMo/TikLocal/main/docs/screenshots/home-desktop.jpg" alt="Home on desktop with the On This Day row" width="820">
</p>

<p align="center"><sub>Screenshots use CC0 and public-domain photos and clips from Wikimedia Commons.</sub></p>

## Flow controls

| Gesture | Video | Image |
| --- | --- | --- |
| Swipe up / down, wheel, ↑ ↓ | Next / previous | Next / previous |
| Tap | Play / pause | Show / hide the interface |
| Double-tap | Favorite | Favorite |
| Press and hold | 2× while held | Show / hide the interface |
| Drag sideways | Seek | Next image in a gallery |
| ← → | Back / forward 5 s | Gallery image, otherwise next / previous |

Files the browser cannot play (for example some HEVC or AV1 videos) are skipped
with a short note instead of stopping the feed.

## Configuration

Settings come from command-line options, then environment variables
(`MEDIA_ROOT`, `TIKLOCAL_HOST`, `TIKLOCAL_PORT`), then
`~/.config/tiklocal/config.yaml`:

```yaml
media_sources:
  - id: default
    name: Main Library
    path: ~/Videos/TikLocal
  - id: photos
    name: Photos
    path: ~/Pictures
download_source: default   # where URL downloads are saved
name: Studio Mac           # shown in the browser tab
port: 8000
```

A single `media_root: ~/Videos` still works. Sources are merged into one library;
media URIs look like `@source_id/path`, and old bare-path links and favorites keep
working through the default source.

```bash
tiklocal /path/to/media                        # start with one folder
tiklocal --port 9000                           # custom port
tiklocal --media-source photos=~/Pictures/AI   # add a source (repeatable)
tiklocal --help
```

### Access password

Sign-in is on by default. Every page, API and media file requires it.

A new instance starts with the access password `admin666`, so the first sign-in
needs no lookup. It is public knowledge: set your own before anyone else can
reach the server.

```bash
tiklocal auth status          # status and storage path
tiklocal auth set-password    # new password; signs out existing sessions
TIKLOCAL_AUTH_PASSWORD='a-long-private-password' tiklocal auth set-password
```

To start a *new* instance with your own password instead of the default, set
`TIKLOCAL_AUTH_PASSWORD` before its first start; it is ignored once
`auth.json` exists.

The password is stored as a scrypt hash in `~/.tiklocal/auth.json`. Keep TikLocal
on a trusted network.

### HTTPS

TikLocal serves plain HTTP. For HTTPS, put it behind a reverse proxy that keeps
the original Host header and media Range requests, and mark the session cookie
secure:

```bash
FLASK_AUTH_COOKIE_SECURE=true tiklocal ~/Videos --host 127.0.0.1 --port 8000
```

### URL downloads

The `/download` page queues background downloads. Install the tools it uses:

```bash
brew install yt-dlp gallery-dl ffmpeg        # macOS
sudo apt install yt-dlp gallery-dl ffmpeg    # Ubuntu / Debian
```

- `yt-dlp` (required) handles video sites; TikLocal asks it for H.264/AAC so
  downloads play in every browser. `gallery-dl` handles image posts and albums.
  `ffmpeg` merges formats and makes video thumbnails.
- For sign-in-only content, put exported cookie files in `~/.tiklocal/cookies`
  with the domain in the name (`x.com.txt`, `youtube.com.cookies`), or upload
  them on the download page.

Stopping the server (Ctrl + C or SIGTERM) cancels unfinished downloads and keeps
finished files; interrupted jobs are not resumed.

<details>
<summary><strong>More commands</strong></summary>

**Thumbnails**

```bash
tiklocal thumbs /path/to/media              # generate video thumbnails
tiklocal thumbs /path --overwrite           # regenerate
```

**Duplicates**

```bash
tiklocal dedupe /path/to/media              # dry run
tiklocal dedupe /path --type video          # videos only
tiklocal dedupe /path --keep newest --execute
```

Options: `--type video|image|all`, `--algorithm sha256|md5`,
`--keep oldest|newest|shortest_path`, `--dry-run` (default), `--execute`,
`--auto-confirm`.

</details>

<details>
<summary><strong>AI titles and similar images (optional)</strong></summary>

Image titles and tags use a vision model; similar images use an embedding model.
Both are off unless configured:

```yaml
vision:
  enabled: true
  base_url: https://openrouter.ai/api/v1
  model_name: google/gemini-2.5-flash
  tags_limit: 5

experiments:
  similarity:
    enabled: true

embedding:
  enabled: true
  base_url: https://openrouter.ai/api/v1
  model_name: google/gemini-embedding-2
  dimensions: 768
  image_max_size: 512
  image_quality: 82
```

API keys come from `TIKLOCAL_VISION_API_KEY`, `TIKLOCAL_EMBEDDING_API_KEY`, then
`TIKLOCAL_AI_API_KEY`, `OPENAI_API_KEY` or `OPENROUTER_API_KEY`.

Vectors are built from the command line and stored in
`~/.tiklocal/tiklocal.sqlite3`; viewing results never calls a model.

```bash
tiklocal vectorize ~/Videos --dry-run                  # see what would be sent
tiklocal vectorize ~/Videos --limit 200 --order latest
tiklocal vectorize ~/Videos --source photos --cleanup
tiklocal analyze-similar ~/Videos --limit 500 --yes
```

Only missing or stale images are uploaded, resized and re-encoded without their
original metadata. Results appear at `/experiments/similarity`, opened from
Settings. `experiments.similarity.enabled: false` turns the feature off without
deleting data; restart after changing it. When omitted, a valid legacy
`embedding.enabled: true` enables it. Precedence is defaults < YAML < saved
embedding configuration < CLI options.

</details>

<details>
<summary><strong>Embedding in another WSGI server</strong></summary>

The CLI owns the download manager. When hosting `create_app()` yourself, call
`app.extensions['download_manager'].start()` inside the serving worker and
`.close()` in its shutdown hook — not in Flask's per-request teardown.

</details>

<details>
<summary><strong>Upgrading from versions with built-in HTTPS or PWA</strong></summary>

Built-in HTTPS, certificate management, PWA installation and offline caching were
removed. Remove `https`, `tls_cert`, `tls_key` and `hostnames` from the YAML
configuration; active old TLS settings stop startup rather than silently switching
to HTTP. The `--https`, `--tls-cert`, `--tls-key`, `--hostname` options, `tls`
commands and the `[https]` extra are gone. Files under `~/.tiklocal/tls/` are left
untouched. Revisit the same address once to retire the old service worker; old
home-screen shortcuts can be removed by hand.

</details>

## 从 spooly 移植的功能

本仓库在 TikLocal 0.8.45 基础上，从其换牌续作
[spooly](https://github.com/oddloafhq/spooly) 0.8.46–0.8.48 移植了以下功能
（品牌/设置页 UI 刷新、PWA/设备配对清理等上游差异未移植）：

1. **相关视频与相似照片（spooly v0.8.48 "related videos"）**——视频按
   时长 20% / 50% / 80% 抽取三帧（短于 1 秒只抽一帧）单独向量化；
   匹配时先用均值向量选出 48 个候选，再做双向逐帧最佳匹配平均，避免单个
   共同镜头主导排序。视频/图片详情页共用 `/api/recommend/similar`。
2. **按文字描述搜索 + 相关照片发现卡片（spooly v0.8.47 "optional media
   discovery"）**——Library 探索页新增"Search by description"面板（中文
   可用）与"Related photos"发现卡片；数据层把 `image_vectors` 统一为
   `media_vectors`（数据库迁移 011/012，含迁移事务安全修复）。Flow 偶数页
   也会插入相关照片组卡片。
3. **Docker / NAS 部署链路**——多阶段 `Dockerfile`（含 ffmpeg、yt-dlp、
   gallery-dl、Deno）、`compose.yaml`、尊重配置端口的健康检查，以及无 AVX
   NAS CPU 可用的 llama.cpp EmbeddingGemma-2 侧车方案
   （[`deployment/embedding/`](deployment/embedding/README.md)）。本仓库不
   发布镜像，部署时本地构建：见 [`docs/docker.md`](docs/docker.md)。
4. **兼容性小修复**——废弃 `datetime.utcnow()` 清理（Python 3.12+）、
   pytest 移入 dev 依赖组、`TIKLOCAL_INSTANCE` 实例目录（其 `config.yaml`
   优先读取，Docker 数据卷即用它隔离）。

### 默认关闭与启用方式

embedding 功能默认**关闭**（`experiments.similarity.enabled` 缺省为
`false`），关闭时不注册任何向量路由、页面不显示相关入口，核心浏览功能零
依赖。启用需要自备一个 768 维 embedding 服务（OpenAI 兼容 `/embeddings`
接口；本地推荐 llama.cpp 方案）：

```yaml
# ~/.config/tiklocal/config.yaml
experiments:
  similarity:
    enabled: true
embedding:
  base_url: http://127.0.0.1:9379/v1   # 或 OpenRouter 等云端服务
  model_name: embeddinggemma-2
  # api_key: sk-...                    # 云端服务需要；本地 llama.cpp 不用
```

启用后：设置页 → Similar Images（或 CLI `tiklocal vectorize --dry-run`
预览再执行）完成媒体准备；准备完成前旧向量仍可用于浏览。**性能参考**
（spooly 在无 AVX 的 N5105 NAS 实测）：单张图片约 26 秒、三帧视频约 3 倍、
容器内存约 1.1 GiB——建议在 PC 上建好索引再迁往 NAS；换模型会重建向量。
架构细节见 [`docs/media-index-and-recommendation.md`](docs/media-index-and-recommendation.md)。

> 移植方式为逐文件复制 + 包名重命名，未引入 spooly 的品牌与设置页 UI。

## 抖音式二开功能（实况 / 图集 / 按博主）

针对抖音归档命名（`YYYY-MM-DD HH.MM.SS-类型-博主名-标题_N.ext`，博主文件夹
`UID…_博主名_发布作品`）的三个核心功能，均在扫描/索引层完成：

1. **实况配对**——`-实况-` 帖子内 `_N` 跨文件连续编号，mp4 与相邻 jpeg 严格
   相邻配对成一条 `live` 条目（封面为 jpeg，`live_video_uri` 指向 mp4，mp4
   不再单独成条）。Flow 中显示静图封面 + "实况"徽章，**按住播放动态 mp4**
   （松开停止；有声播放被浏览器拒绝时自动降级为静音）。
2. **图集接入**——`-图集-` 同时间戳前缀的 jpeg 归为一组（`album_key`），
   Flow 中作为一张可**左右滑动**的组卡呈现；实况多图帖同样整帖成组，
   组内可混合 image/live/video 成员。抖音模式下左上角常驻"图集 1/5"角标
   （纯文字样式，同"实况"标识），滑动换图时实时更新当前浏览进度。
3. **按博主浏览**——每个博主文件夹注册为一个 media source（`--media-source
   id=路径` 或 config.yaml `media_sources`），Library 探索页出现博主 chips
   （显示从文件夹名提取的博主名与条数），点击筛选；选中后出现"刷一刷"入口
   直达该博主的 Flow（`/flow?blog=<source_id>`）。推荐流按博主维度过滤，
   图集/实况组在过滤流中保持完整。
4. **抖音式搜索**——抖音模式顶栏右上角放大镜进入独立搜索页 `/search`：
   一次搜索同时命中**博主昵称 / 抖音号**与**作品文案**，结果分「综合 / 视频 /
   用户」三栏（综合 = 用户快捷区 + 双列瀑布流，视频栏滚动加载更多，用户栏为
   博主列表）；API 为 `GET /api/search?q=&tab=general|video|user&limit=&offset=`。

媒体库维表：`media_items` 新增 `live_video_uri` 与 `album_key/album_index/
album_size`（数据库迁移 13）；推荐候选只取各组第 0 位封面，避免组员重复
进入信息流。

```bash
tiklocal serve --port 8000 \
  --media-source b1=/media/UID100000000000001_甲博主_发布作品 \
  --media-source b2=/media/UID100000000000002_乙博主_发布作品
```

## 按博主批量下载（TikTokDownloader 接入）

`/download` 页第三个开关“博主”接入
[TikTokDownloader](https://github.com/JoeanAmier/TikTokDownloader) Web API：
粘贴抖音**博主主页 / 喜欢 / 收藏**链接，或单条**作品 / 合集**链接
（支持短链与分享文本），TikLocal 通过局域网 sidecar 解析出无水印直链后
自行下载入库。文件名对齐抖音归档命名，下载完成即自动完成实况/图集配对。
sidecar 安装、配置键（`tiktok_downloader.base_url/token`）、回退顺序与
故障排查见
[`docs/tiktok-downloader.md`](docs/tiktok-downloader.md)。

## LumaFold for iPhone and Android

`apps/radio` contains **LumaFold**, a local-first native client. It imports photos
and videos you pick into a private offline Flow with no account, network, or
server, and can optionally connect to your TikLocal server for Radio. See
[`docs/radio-native-client-architecture.md`](docs/radio-native-client-architecture.md).

## Documentation

- [Docs index](docs/README.md)
- [Flow interaction](docs/flow-interaction-unification.md)
- [Media index and local recommendation](docs/media-index-and-recommendation.md)
- [Release notes](docs/release_notes.md)

## Contributing

Bug reports, ideas and pull requests are welcome on
[GitHub](https://github.com/ChanMo/TikLocal/). To refresh the screenshots, serve a
demo folder without sign-in and run the capture script (it uses your installed
Chrome):

```bash
npm install
FLASK_AUTH_ENABLED=false tiklocal demo-media --port 8793
node scripts/screenshots.mjs
```

Contact: [chan.mo@outlook.com](mailto:chan.mo@outlook.com)

## License

[MIT](LICENSE)
