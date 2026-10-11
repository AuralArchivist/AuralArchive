I'm not a professional developer. I taught myself to code while building AuralArchive, one feature, one bug and one "why won't this work?" at a time. Many months later, this is version 1.0 of my very first application. It's grown into something I'm proud of, but it's still young: now and then something will break, and when it does, it'll get fixed. Thanks for stopping by, for your patience, and for giving it a try. Happy listening!

# AuralArchive

**Your audiobook library, organised, complete and up to date.**

AuralArchive keeps track of every audiobook you own and every one you're still waiting for. It follows the authors and series you love, so a new release never slips past you. When a book is missing, it can find it through your indexers, send it to your download client and file the finished book neatly into your library, ready for Audiobookshelf or any other player. If you listen on Audible, it brings in the books you own and keeps an eye on your wishlist too.

## What it does

**Keeps your library in order**
- One place for everything you own and everything you want, with covers, series, narrators, runtimes and ratings.
- Every book is filed into a tidy `Author/Series/Title` folder, with its ASIN written into the audio file so players recognise it straight away.
- A scheduled check notices books that were moved or deleted, and picks up new folders on its own. It never deletes your files.
- Room for more than one library, for example a children's library or a second disk, with rules that decide where each new book goes.

**Helps you find what's next**
- Author and series pages show what you have, what you're missing and what's coming, and add a whole series in one click.
- Discover suggests books based on what you already own.
- The Release Calendar follows the genres and keywords you choose and lets you know when something you're waiting for comes out.
- Search covers the whole Audible catalogue, and connecting Hardcover adds trending lists, curated lists and your shelves.

**Gets the books for you**
- Searches all of your Torznab and Newznab indexers at once, including Prowlarr, NZBHydra2 and Jackett.
- Sends the best match to qBittorrent, or to SABnzbd or NZBGet for Usenet (Usenet support is still experimental).
- Automatic downloads go looking for your Wanted books on a schedule and follow your rules for match quality, formats, bitrate, free disk space and tracker ratio.
- Downloads the Audible books you own as M4B.

**Brings in the books you already have**
- Drop files in the import folder or upload them from your browser, check the matches, and they're renamed and filed for you.
- Already organised? Library Import adopts your existing library exactly where it is, without moving a single file.
- Handles M4B, M4A, MP3, FLAC, OGG, Opus and AAC, and can merge MP3 audiobooks into a single chaptered M4B.

**Plays nicely with Audiobookshelf**
- Point Audiobookshelf at the same library folder and it simply works.
- Connect the two to see listening progress in AuralArchive, get a nudge about the next book in a series, and have new books show up in Audiobookshelf right after they're imported.

## Getting started with Docker

Docker is the easiest way to run AuralArchive. The image includes everything it needs, including an audio-only FFmpeg.

```yaml
services:
  auralarchive:
    image: ghcr.io/auralarchivist/auralarchive:latest
    container_name: auralarchive
    environment:
      - PUID=1000        # the user that owns your media folders (run `id`)
      - PGID=1000
      - UMASK=002
      - TZ=Etc/UTC
    ports:
      - "8765:8765"
    volumes:
      - /path/to/auralarchive/config:/config
      - /path/to/audiobooks:/library
      - /path/to/downloads:/downloads
      - /path/to/import:/import
      - /path/to/work:/work
    security_opt:
      - no-new-privileges:true
    restart: unless-stopped
```

```bash
docker compose up -d
```

Then open `http://localhost:8765` and create your account.

Inside the container AuralArchive always uses the same folders. You decide where each one lives on your server with the volumes:

| Folder | What's in it |
|---|---|
| `/config` | Settings, database, sign-ins, cache and logs. This is the folder to back up. |
| `/library` | Your organised audiobook library. Point Audiobookshelf here too. |
| `/downloads` | Where your download client saves downloads |
| `/import` | A drop folder for books you add by hand |
| `/work` | Scratch space for Audible downloads and conversions |

A few things worth knowing:
- **Use the same downloads folder as your download client.** AuralArchive tells the client where to save each book, so mount the same host folder at `/downloads` in both containers. Most qBittorrent, SABnzbd and NZBGet images already use `/downloads`.
- **Mount every folder you care about.** Anything you don't mount lives inside the container and is lost when it's recreated, for example on an update. AuralArchive shows a warning if your library, downloads or config aren't mounted.
- **Permissions.** AuralArchive runs as `PUID:PGID` and never changes the owner of your media folders. If it can't write to one, the container log tells you which.

## Running without Docker

You'll need Linux, Python 3.14 and FFmpeg. The free-threaded build of Python (`python3.14t`) is recommended, since it lets AuralArchive use more than one CPU core at a time. The easiest way to get it is `uv python install 3.14t`.

```bash
git clone https://github.com/AuralArchivist/AuralArchive.git
cd AuralArchive/app
python3.14t -m venv ../venv && source ../venv/bin/activate
pip install -r requirements.txt
python app.py
```

Then open `http://localhost:8765`. Start it as your normal user; AuralArchive won't run as root. Your settings and database are kept in `app/config`, next to the program, and the built-in media folders in `app/media`. `python app.py --show-paths` shows where everything is.

**On Windows?** Run the Docker image with Docker Desktop, using the compose file above. macOS isn't supported.

## Documentation

The [wiki](https://github.com/AuralArchivist/AuralArchive/wiki) covers everything in more detail:

- [Installation](https://github.com/AuralArchivist/AuralArchive/wiki/Installation) and [First Time Setup](https://github.com/AuralArchivist/AuralArchive/wiki/First-Time-Setup)
- [Audible Integration](https://github.com/AuralArchivist/AuralArchive/wiki/Audible-Integration), [Hardcover](https://github.com/AuralArchivist/AuralArchive/wiki/Hardcover) and [Audiobookshelf](https://github.com/AuralArchivist/AuralArchive/wiki/Audiobookshelf)
- [Downloads and Queue](https://github.com/AuralArchivist/AuralArchive/wiki/Downloads-and-Queue), [Importing](https://github.com/AuralArchivist/AuralArchive/wiki/Importing) and [Multiple Libraries](https://github.com/AuralArchivist/AuralArchive/wiki/Multiple-Libraries)
- [Settings](https://github.com/AuralArchivist/AuralArchive/wiki/Settings) and [Troubleshooting](https://github.com/AuralArchivist/AuralArchive/wiki/Troubleshooting)

Found a bug or have an idea? [Open an issue](https://github.com/AuralArchivist/AuralArchive/issues). Please remove any API keys, passwords and usernames from logs before you paste them.

## Credits and thanks

AuralArchive stands on the shoulders of some great open-source projects:
- **Audible by mkb79**: the foundation for Audible sign-in and downloads. Thank you for making this possible.
- **LazyLibrarian**: inspiration for working with indexers and download clients, and for the overall workflow.
- **Readarr**: an influence on library organisation, queue handling and how metadata flows.

## Disclaimer

**This software is intended solely for downloading your own legally owned titles and copyright-free or public domain audio content.**

This software is provided "AS IS," without warranty of any kind, either express or implied, including but not limited to the implied warranties of merchantability, fitness for a particular purpose, or non-infringement.

The developer(s) make no representations or warranties regarding the accuracy, functionality, reliability, or suitability of this software for any purpose.

You acknowledge and agree that you assume full responsibility for how you choose to use this software. The developer(s) are not responsible for any misuse of this software and do not endorse or condone copyright infringement or any illegal activity.

You are solely responsible for ensuring compliance with all applicable local, state, national, and international laws, including copyright laws and intellectual property rights, and for obtaining proper authorization before downloading any content.

The developer(s) shall not be held liable for any legal consequences, damages, losses, or claims arising from your use or misuse of this software.

By using this software, you acknowledge that you have read, understood, and agree to these terms, and you accept all risks associated with its use.

**USE AT YOUR OWN RISK.**
