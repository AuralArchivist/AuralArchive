# Troubleshooting

Something not working? Most problems leave a clue somewhere. Here's where to look, and fixes for the things people run into most.

## Where to look first

- **Settings › Maintenance › Application Logs** shows the live log. Set **Log level** to Debug for more detail while you reproduce the problem.
- The **notification bell** reports failed downloads, storage problems and an expired Audible sign-in.
- **Settings › Storage** shows whether every folder exists, is writable and has space.

Log files live in the `logs/` folder of your app data (`app/config/logs`, or `/config/logs` in Docker). The current one is `auralarchive_web.log`.

```bash
docker compose logs -f auralarchive     # Docker
python app.py --show-paths              # without Docker: prints every folder location
```

## Installation and startup

**The container exits straight away**
- Run `docker compose logs auralarchive` and read the last few lines.
- If the port is already in use, change the host side of the port mapping, for example `"8766:8765"`.

**"Permission denied" when writing to media folders**
- Set `PUID`/`PGID` to the user that owns your media folders (`id your_username`).
- The container log names any mounted folder that `PUID:PGID` can't write to. Fix the ownership on the host:
  ```bash
  sudo chown -R 1000:1000 /path/to/audiobooks
  sudo chmod -R u+rwX,g+rwX /path/to/audiobooks
  ```
- On SELinux systems, add `:z` to the volume mounts.

**A red banner says a folder isn't mounted**
- In Docker, any folder you don't mount lives inside the container and is lost when it's recreated. Mount `/config`, `/library`, `/downloads`, `/import` and `/work` as shown in [Installation](Installation).

**Downloads fail, or qBittorrent can't save them**
- In Docker, AuralArchive and your download client need to see the downloads folder at the same path. Mount the same host folder at `/downloads` in both containers, or add a path mapping in **Settings › Download Clients**.

**It won't start without Docker**
- AuralArchive refuses to run as root. Start it as your normal user.
- Check that `ffmpeg` and `ffprobe` are on your `PATH`.
- Check you're using Python 3.14 (see [Installation](Installation)).

## Audible

See [Audible Integration](Audible-Integration#when-things-go-wrong).

## Downloads and imports

See [Downloads and Queue](Downloads-and-Queue#when-things-go-wrong). The usual suspects are:
- AuralArchive can't reach the download client (check the host, port and sign-in; in Docker, don't use `localhost`).
- The client's download path isn't visible to AuralArchive (add a path mapping).
- The library or downloads folder isn't writable (check **Settings › Storage**).

## Library

**Books show as Missing, but the files are there**
- The library folder may have moved. Check **Settings › Storage**, then run the **Library** job in **Settings › Metadata**.

**The library check stopped and asked you to confirm**
- It found a large part of the library missing at once, which almost always means a drive isn't mounted. Check the drive first, and only confirm if those books really are gone.

**Books you already have aren't in the Library**
- Use **Import › Library Import** to adopt them where they are (see [Importing](Importing)).

**Duplicate entries**
- Delete the extra copy from the Library. Books with the same ASIN are only ever added once.

## Database

The database is `database/auralarchive.db` in your app data: `app/config/database/` without Docker, `/config/database/` in Docker.

**"Database is locked"**
- This usually means a second copy of AuralArchive is using the same app data. Stop all of them and start just one.

**Errors or corruption**
1. Try **Settings › Maintenance › Repair Database** first.
2. If that doesn't help, stop AuralArchive, move the database aside, and start it again to create a fresh one:
   ```bash
   mv config/database/auralarchive.db config/database/auralarchive.db.broken
   ```
   Then sync from Audible and use **Library Import** to adopt your existing files.

## Backups

Back up the whole app data folder (`app/config`, or the folder you mounted at `/config` in Docker). It holds your settings, database, sign-ins, cache and logs. Stop AuralArchive first so the database is consistent:

```bash
docker compose stop auralarchive
tar -czf auralarchive-backup-$(date +%Y%m%d).tar.gz config/
docker compose start auralarchive
```

To restore, stop AuralArchive, put the folder back, and start it again.

## Reporting a bug

If you're stuck, open an issue at [github.com/AuralArchivist/AuralArchive/issues](https://github.com/AuralArchivist/AuralArchive/issues) and include:
- what you did, what you expected, and what actually happened
- the relevant lines from the log
- whether you're on Docker, your OS, and your AuralArchive version (**Settings › General**)

Please remove API keys, passwords and usernames from anything you paste.
