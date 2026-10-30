# Docker

This fork is not published to a registry. Build the image locally from the
repository checkout; the build includes `ffmpeg`, `yt-dlp`, `gallery-dl` and
Deno, the JavaScript runtime yt-dlp uses for YouTube.

On macOS and Windows, `pip install -e .` is faster: Docker Desktop reads
mounted folders slowly, and TikLocal scans the whole library at startup.

On a Synology NAS the account ids and folder paths are not the ones below; see
[synology.md](synology.md) instead.

## Start

```bash
git clone <your-fork-url> tiklocal && cd tiklocal   # or use your existing checkout
docker build -t tiklocal:latest .
mkdir tiklocal-data                       # create it yourself so your user owns it
docker run -d --name tiklocal --restart unless-stopped --init \
  --user "$(id -u):$(id -g)" -p 8000:8000 -e TZ=Asia/Shanghai \
  -v ~/Videos:/media -v "$PWD/tiklocal-data:/data" \
  tiklocal:latest
docker logs tiklocal                      # first start prints the generated password
```

Or use [`compose.yaml`](../compose.yaml) from the checkout: edit the media path
and time zone, then run `docker compose up -d --build`.

Open `http://<server-address>:8000`.

## Volumes

| Path | Contents | Access |
| --- | --- | --- |
| `/media` | Your library | Read-write for URL downloads and the deletion trash (`.tiklocal-trash`). Read-only works if you never delete or download. |
| `/data` | Database, password, thumbnails, cookies, `config.yaml` | Read-write; back it up |

Run the container as the user that owns your media (`--user` or `user:` in
Compose). On Synology this is often `1026:100`; [synology.md](synology.md) walks
through how to find the right pair. If Docker creates a missing host folder, it
belongs to root and TikLocal cannot write to it.

## Password

A fresh `/data` gets a random password on its first start. It is printed once in
the container log, under `Access password (generated, save it)`:

```bash
docker logs tiklocal 2>&1 | grep -A3 'Access password'
```

Copy it, sign in, then change it. To skip the generated step entirely, set
`TIKLOCAL_AUTH_PASSWORD` before the first start; it is ignored once
`/data/auth.json` exists. Change the password later with:

```bash
docker exec -it tiklocal tiklocal auth set-password
```

Or use **Settings → Change Access Password → Change** in the web UI; that signs
out the other sessions but keeps the browser you are using.

## Configuration

Place `config.yaml` in `/data`; it takes priority over other configuration
files. Several folders become one library when you mount them under separate
paths:

```yaml
# /data/config.yaml
name: Home NAS
media_sources:
  - id: default
    name: Videos
    path: /media
  - id: photos
    name: Photos
    path: /photos
```

```bash
docker run ... -v /volume1/video:/media -v /volume1/photo:/photos ...
```

Environment variables also work: `TZ`, `TIKLOCAL_NAME`, `TIKLOCAL_PORT` (also
change the published port), and `FLASK_AUTH_COOKIE_SECURE=true` behind an HTTPS
reverse proxy.

## Updating

```bash
git pull                                  # or apply your own changes
docker build -t tiklocal:latest .
docker rm -f tiklocal    # then run the same docker run command again
# or: docker compose build && docker compose up -d
```

Video sites change often. If downloads fail before a new image is published,
update yt-dlp inside the running container (lost when the container is
recreated):

```bash
docker exec -u 0 tiklocal pip install -U "yt-dlp[default]"
```

## Notes

- Files added to `/media` from another computer appear after a library sync in
  Settings or a container restart.
- Thumbnails, downloads and AI features work as in a regular install. AI API
  keys are passed as environment variables, for example
  `-e TIKLOCAL_AI_API_KEY=...`.
