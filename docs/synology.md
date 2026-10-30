# Synology NAS

TikLocal runs in a container on a Synology NAS. This page is the Synology half
of [docker.md](docker.md): it exists because the generic quick start fails on
DSM for two reasons that have nothing to do with TikLocal — the image's built-in
account id does not exist there, and none of the paths in the example exist
either.

Everything below assumes DSM 7 with **Container Manager** (the package was
called **Docker** on DSM 7.0 and 7.1) and SSH switched on under **Control Panel
→ Terminal & SNMP**.

## What the container needs

| Path | Purpose | Access |
| --- | --- | --- |
| `/media` | Your library | Read-write: downloads land here and deleted files go to `.tiklocal-trash` inside their folder |
| `/data` | Database, password, thumbnails, cookies, optional `config.yaml` | Read-write |
| port 8000 | The web UI | Reachable from your LAN |

The image is built locally — this fork is not published to a registry. The
`Dockerfile` pulls `python:3.12-slim-trixie` and `denoland/deno:bin-2.9.7`, then
installs `ffmpeg` from Debian and `yt-dlp` / `gallery-dl` from PyPI, so the build
itself needs working access to Docker Hub, Debian and PyPI. Both base images are
published for `arm64` and Debian ships `ffmpeg` for it, so an ARM model works
too — it is just slower.

## Step 1 — Find the account id

DSM assigns its own uid/gid numbers. `admin` is 1024, `guest` is 1025, the first
user you create is usually 1026, and the built-in group `users` is 100. The image
runs as uid 1000 by default, and **there is no uid 1000 on DSM**, which is the
first thing that breaks.

SSH in as the account that should own the media and read the numbers off:

```bash
id -u    # 1026
id -g    # 100
```

Use that pair everywhere below as `<uid>:<gid>`. If you log in as `admin` to
run the commands, still use the id of the account you plan to serve media as —
`admin`'s 1024 is fine only if `admin` itself owns the folders.

## Step 2 — Get the code

```bash
sudo mkdir -p /volume1/docker/tiklocal
sudo chown <uid>:<gid> /volume1/docker/tiklocal
cd /volume1/docker/tiklocal
git clone -b dev https://github.com/beici/TikLocal.git .
```

Cloning as your own account (not with `sudo`) keeps the checkout writable for
`git pull` later. Without the Git package, upload a zip of the checkout with
File Station and unpack it here instead.

## Step 3 — Create the data folder first

```bash
mkdir -p /volume1/docker/tiklocal/tiklocal-data
```

Do this yourself. If Docker creates a missing bind-mount source it belongs to
`root`, and the container cannot write its database into it — the first start
then dies with a `PermissionError` on `/data/tiklocal.lock`.

## Step 4 — Edit the compose file

`compose.synology.yaml` in the checkout is already the Synology version; three
lines need your numbers:

| Line | Default | Change to |
| --- | --- | --- |
| `user:` | `1026:100` | your `<uid>:<gid>` from step 1 |
| `- /volume1/video:/media` | `/volume1/video` | the shared folder holding your media |
| `TZ:` | `Asia/Shanghai` | your time zone |

The media path must exist already. A typo there makes Docker create an empty
root-owned folder and TikLocal will faithfully index nothing.

If 8000 is already taken on the NAS, change only the host half — `"8080:8000"`
— and visit port 8080. Nothing inside the container has to change: the server,
the health check and `EXPOSE` all still use 8000. Moving the container half
(`"8000:9000"`) also needs `TIKLOCAL_PORT: "9000"` in `environment:`, because
both the server and the health check read that variable.

## Step 5 — Build and start

```bash
cd /volume1/docker/tiklocal
sudo docker compose -f compose.synology.yaml up -d --build
```

On a DSM whose Container Manager only ships the older binary, use
`docker-compose` instead of `docker compose`.

The first build compiles nothing but does download the base images, ffmpeg and
the Python dependencies; expect anywhere from a few minutes on a `+` model to
well over half an hour on a low-end one. Later builds reuse the layers.

## Step 6 — Sign in

```bash
sudo docker logs tiklocal 2>&1 | grep -A3 'Access password'
```

That prints the random password generated on the first start, once. Open
`http://<nas-address>:8000`, sign in, and change it:

```bash
sudo docker exec -it tiklocal tiklocal auth set-password
```

The web form (**Settings → Change Access Password → Change**) asks for the
current password first, so it cannot rescue a password you never wrote down — the
command above can, and it needs no old password. To skip the generated password
entirely, set `TIKLOCAL_AUTH_PASSWORD` in the compose file before the very first
start (it is ignored once `/data/auth.json` exists).

The first start indexes the whole library before it serves pages. Until that
finishes, `docker ps` reports the container as **unhealthy** — that is the image
health check probing `/login` and timing out, not a crash. The `restart:
unless-stopped` policy does not restart a container for being unhealthy, so give
it time and watch `docker logs`.

## Permissions

Both mounts are writable, and DSM's ACLs are not POSIX ownership alone: the
container id must also be granted access in **Control Panel → Shared Folder →
(the folder) → Edit → Permissions**.

- `/media` read-write is what makes deleting and URL downloads work. A
  read-only media mount is fine if you never do either.
- `/data` read-write is mandatory.
- The trash lives inside each source folder as `.tiklocal-trash`, and a download
  is staged in `.tiklocal-download-tmp` there before it is moved into place —
  both need write access to the folder itself, not just to the files.

## More than one folder

Mount the extra folder and declare it in `/data/config.yaml`, which takes
priority over every other configuration file:

```yaml
# /volume1/docker/tiklocal/tiklocal-data/config.yaml
name: Home NAS
media_sources:
  - id: default
    name: Videos
    path: /media
  - id: photos
    name: Photos
    path: /photos
```

```yaml
# compose.synology.yaml, under the tiklocal service
volumes:
  - /volume1/video:/media
  - /volume1/photo:/photos
```

The container always starts `tiklocal serve /media`, and that argument overrides
the path of the `default` source — so keep `default` on `/media` and give extra
folders their own ids. Downloads are saved into the `default` source, so that
one has to be the writable one.

## Behind the DSM reverse proxy

**Control Panel → Login Portal → Advanced → Reverse Proxy**: source HTTPS, your
hostname, destination HTTP `127.0.0.1:8000`. Then add to the compose file so the
session cookie is marked `Secure`:

```yaml
# compose.synology.yaml, under environment:
FLASK_AUTH_COOKIE_SECURE: "true"
```

The health check talks to `127.0.0.1:8000` inside the container and is
unaffected by the proxy.

## Updating

```bash
cd /volume1/docker/tiklocal
git pull
sudo docker compose -f compose.synology.yaml build
sudo docker compose -f compose.synology.yaml up -d
```

`/data` and `/media` are outside the container, so nothing is lost. Video sites
change faster than images are rebuilt; to patch the downloader in a running
container (lost on the next recreate):

```bash
sudo docker exec -u 0 tiklocal pip install -U "yt-dlp[default]"
```

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| Container exits at once, `docker logs` ends in `PermissionError` on `/data/tiklocal.lock` | the data folder is not writable by the compose `user:` id — compare `ls -ln tiklocal-data` with step 1 |
| `Cannot create the data directory: ...` | `/data` is missing and cannot be created; the bind mount source is wrong |
| `Cannot open the instance data under /data: ...` | writable, but the database will not open — an older `tiklocal.db` owned by another uid, or a full volume |
| `... is already in use by another TikLocal process` | two containers share one `/data`; only one may run per data folder |
| `Media directory does not exist: /media` | the host path in `volumes:` is wrong or not mounted |
| `unhealthy` for a long time | still indexing, or the port inside the container is not 8000 |
| Build fails fetching `python` / `deno` | Docker Hub unreachable — set a registry mirror in Container Manager → Registry → Settings, or configure a proxy for the daemon |
| Pages load but delete or download fails | no write permission on the media folder |
| Nothing loads from another machine | DSM firewall (**Control Panel → Security → Firewall**) allowing 8000 |

## Without SSH

Build on a machine with Docker, copy the image over, and import it in Container
Manager → **Image → Add → From file**:

```bash
docker build -t tiklocal:latest .
docker save tiklocal:latest -o tiklocal.tar
```

Then create the container from the GUI with port 8000, the two volume mappings,
`TZ`, and — under **Advanced Settings** — the user set to your uid, for the same
reason as step 1. Architectures have to match, so for an ARM NAS build the image
with `docker buildx build --platform linux/arm64 -t tiklocal:latest --load .`

## Backup

Everything TikLocal owns is in `tiklocal-data`; the media is yours and needs no
export. Stop the container before copying so the SQLite database is not captured
mid-write:

```bash
sudo docker stop tiklocal
sudo tar czf /volume1/backup/tiklocal-data-$(date +%F).tgz \
  -C /volume1/docker/tiklocal tiklocal-data
sudo docker start tiklocal
```

The embedding sidecar in [`deployment/embedding/`](../deployment/embedding/README.md)
is only needed for semantic search, which is off by default — skip it on a NAS.
