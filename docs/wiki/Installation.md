# Installation

There are two ways to run AuralArchive: in Docker (the easiest, and what most people should pick) or directly with Python on Linux. Both end up with the same layout and the same features, so choose whichever fits your setup. On Windows, use Docker (see [On Windows](#on-windows)). macOS isn't supported.

## How AuralArchive stores your data

It helps to know this up front, because it decides what you back up and what you mount.

- **App data** is AuralArchive's own private stuff: settings, the database, saved sign-ins, the cover cache, logs and backups. It's small, and it's the one folder you really want to back up.
- **Media folders** are where the books live: `library/`, `downloads/`, `import/` and `work/`. These are shared with Audiobookshelf and your download client.

Without Docker, every media folder has a built-in location that works out of the box, and you can point any of them at your own storage later in **Settings › Storage**. One tip worth remembering: keep `library/`, `downloads/` and `work/` on the same disk. Finished books then move into your library instantly instead of being copied.

In Docker, the folders have fixed paths inside the container, and you pick where they live on your server with volumes.

| | App data | Built-in media folders |
|---|---|---|
| Docker | `/config` | `/library`, `/downloads`, `/import`, `/work` |
| Without Docker | `app/config` | `app/media/library`, `app/media/downloads`, `app/media/import`, `app/media/work` |

## Docker (recommended)

### What you need
- Docker 20.10 or newer
- Docker Compose 2.0 or newer

### Getting it running

1. **Make a folder for AuralArchive:**
```bash
mkdir -p ~/auralarchive
cd ~/auralarchive
```

2. **Create a `docker-compose.yml`:**
```yaml
services:
  auralarchive:
    image: ghcr.io/auralarchivist/auralarchive:latest
    container_name: auralarchive
    environment:
      - PUID=1000
      - PGID=1000
      - UMASK=002
      - TZ=America/New_York
    ports:
      - "8765:8765"
    volumes:
      - ./config:/config
      - /path/to/audiobooks:/library
      - /path/to/downloads:/downloads
      - /path/to/import:/import
      - /path/to/work:/work
    security_opt:
      - no-new-privileges:true
    restart: unless-stopped
```

Change the left side of each volume to the folder on your server. Use the same downloads folder your download client uses (more on that below).

3. **Set PUID and PGID** to the user that owns your media folders:
```bash
id -u  # your PUID
id -g  # your PGID
```

4. **Start it:**
```bash
docker compose up -d
```

5. **Open AuralArchive** at `http://localhost:8765` and follow [First Time Setup](First-Time-Setup).

The image comes with everything it needs: the free-threaded build of Python, so AuralArchive can use more than one CPU core at a time, and an audio-only FFmpeg for conversions.

### Volumes

| Path | What's in it |
|------|-------------|
| `/config` | App data: settings, database, sign-ins, cache, logs, backups |
| `/library` | Your organised audiobook library |
| `/downloads` | Where your download client saves downloads |
| `/import` | Drop folder for books you add by hand |
| `/work` | Scratch space for Audible downloads and conversions |

These paths are fixed inside the container, and the volumes decide where they really live. **Any folder you don't mount lives inside the container and disappears when the container is recreated**, which happens on every update. AuralArchive shows a red warning banner if your library, downloads or `/config` aren't mounted, so you'll know.

**Your download client.** When AuralArchive grabs a book, it tells your download client where to save it. That only works if both containers see the downloads folder at the same path. Most qBittorrent, SABnzbd and NZBGet images already use `/downloads`, so mount the same host folder at `/downloads` in both and you're done. If your client uses something else, add a path mapping in **Settings › Download Clients**.

Because each folder is a separate mount, finished books are copied into `/library` rather than moved, even if both folders are on the same disk.

**More than one library?** Mount each extra folder too, for example `- /path/to/kids-audiobooks:/library-kids`, then add it in **Settings › Storage › Libraries**. [Multiple Libraries](Multiple-Libraries) has the details.

### Environment variables

| Variable | Default | What it does |
|----------|---------|-------------|
| `PUID` | `1000` | The user AuralArchive runs as. It never runs as root unless you set `PUID=0`. |
| `PGID` | `1000` | The group it runs as |
| `UMASK` | `002` | Permissions for new files; `002` makes them group-writable |
| `TZ` | `UTC` | Your timezone, for example `America/New_York` |
| `AURALARCHIVE_PORT` | `8765` | The port inside the container |
| `HTTPS_ENABLED` | `false` | Set to `true` when AuralArchive is served over HTTPS, so session cookies are marked secure |
| `TRUSTED_PROXY_COUNT` | `0` | How many proxies in front of AuralArchive are trusted to set `X-Forwarded-*` headers |
| `CORS_ALLOWED_ORIGINS` | same origin | Extra origins, comma separated, allowed to open live-update connections |

### Accounts

Every account has full access, including settings and saved sign-ins. Only create accounts for people you'd trust with your server.

### Permissions

The container takes ownership of `/config` and of any media folders you haven't mounted. It **never** changes the owner of your mounted media, because other apps share those folders. If a mounted folder isn't writable by `PUID:PGID`, the container log tells you, and you fix the ownership on the host. If you'd rather skip the root setup step completely, run the container with `user: "1000:1000"`.

## Running without Docker

### What you need
- Linux
- Python 3.14. The free-threaded build (`python3.14t`) is recommended, because it lets AuralArchive do several things at once on more than one CPU core. A regular 3.14 build works too.
- FFmpeg, with `ffmpeg` and `ffprobe` on your `PATH`
- Git

The simplest way to get the free-threaded Python is [uv](https://docs.astral.sh/uv/): `uv python install 3.14t`.

### Step by step

1. **Get the code:**
```bash
git clone https://github.com/AuralArchivist/AuralArchive.git
cd AuralArchive/app
```

2. **Create a virtual environment** with Python 3.14:
```bash
python3.14t -m venv ../venv
source ../venv/bin/activate
```

3. **Install the dependencies:**
```bash
pip install -r requirements.txt
```

4. **Start it** as your normal user. AuralArchive won't run as root, on purpose.
```bash
python app.py
```

5. **Open AuralArchive** at `http://localhost:8765`.

There's nothing to create by hand. AuralArchive makes its folders the first time it starts.

### Where your data goes

Your settings, database and sign-ins live in `app/config`, right next to the program, and the built-in media folders in `app/media`. Both are created on the first start, and git ignores them, so `git pull` never touches them. Because they sit inside the program folder, update with `git pull` rather than deleting the folder and cloning it again, and keep a backup of `app/config`.

The folder needs to be writable by the user that runs AuralArchive, so clone it somewhere that user owns, such as your home folder. To see every location:
```bash
python app.py --show-paths
```

### Running it as a background service

AuralArchive doesn't come with a systemd service file. You can certainly run it that way, but the setup is up to you and isn't officially supported. If you do, use a dedicated non-root user that owns the data folder and can write to your media folders.

## On Windows

AuralArchive runs on Linux, so on Windows you run the Docker image with [Docker Desktop](https://www.docker.com/products/docker-desktop/), which uses WSL 2 under the hood. Install Docker Desktop, then follow the [Docker](#docker-recommended) steps above. Folder paths in the compose file use Windows paths on the left side, for example `C:/Audiobooks:/library`.

## On a NAS (Synology, TrueNAS, Unraid)

The main thing on a NAS is getting PUID and PGID right: they should match the user that owns your audiobook share.

**Synology / TrueNAS:**
```bash
id your_username
```

**Unraid:** PUID `99` (nobody) and PGID `100` (users).

### Example docker-compose.yml for a NAS

```yaml
services:
  auralarchive:
    image: ghcr.io/auralarchivist/auralarchive:latest
    container_name: auralarchive
    environment:
      - PUID=1026  # your NAS user ID
      - PGID=100   # your NAS group ID
      - UMASK=002
      - TZ=America/New_York
    ports:
      - "8765:8765"
    volumes:
      - /volume1/docker/auralarchive/config:/config
      - /volume1/audiobooks/library:/library
      - /volume1/downloads:/downloads
      - /volume1/audiobooks/import:/import
      - /volume1/audiobooks/work:/work
    restart: unless-stopped
```

## Updating

### Docker
```bash
cd ~/auralarchive
docker compose pull
docker compose up -d
```

### Without Docker
```bash
cd AuralArchive
git pull
source venv/bin/activate
pip install -r app/requirements.txt --upgrade
```

## Next steps

Once it's running, head over to **[First Time Setup](First-Time-Setup)**.
