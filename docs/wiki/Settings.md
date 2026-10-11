# Settings

Open **Settings** in the sidebar and the sections are listed underneath. Anything you save takes effect straight away, so there's no need to restart AuralArchive after changing something.

## General

- **Theme**: Dark, Light, Nord, Enterprise, Midnight Navy or Graphite Azure. Try a few.
- **Application**: the version you're running, and **Check for Updates**. AuralArchive also checks once a day on its own and lets you know when a new version is out.

## Storage

This is where your library, downloads, import and work folders live. Leave a field blank to use the built-in folder, or enter your own; it has to exist already and be writable. **Reset** goes back to the built-in folder, and a folder pinned by an environment variable shows as **Locked**. In Docker all four are locked at `/library`, `/downloads`, `/import` and `/work`; you choose where they live with volumes instead.

Each folder shows whether it's ready, how much space is free, and in Docker whether it's mounted from the host. You'll get a warning for missing folders, low disk space, Docker folders that aren't mounted, and a library and downloads folder on different disks (which turns imports into copies instead of instant moves).

**Libraries** lists your library folders: Main (which follows the Library folder above) and any you've added, each with its book count, free space and health. You can add, edit, disable or delete libraries here, choose the default, and use **Repair assignments** after moving folders by hand. See [Multiple Libraries](Multiple-Libraries).

**App Data** shows where your settings, database, sign-ins, cache, logs and backups are kept.

## Download Clients

qBittorrent, SABnzbd and NZBGet, plus path mappings. See [Downloads and Queue](Downloads-and-Queue#download-clients).

## Download Management

Automatic downloads, the schedule window, the download workflow, the disk space floor, tracker ratio and freeleech rules, and retries. See [Downloads and Queue](Downloads-and-Queue#automatic-downloads).

## Indexers

Prowlarr and NZBHydra2 sync, and your own indexers. See [Downloads and Queue](Downloads-and-Queue#indexers).

## Match & Quality

**Minimum match confidence to auto-download** (0-100, default 80) is how sure AuralArchive has to be that a result is the right book and edition before an automatic search grabs it. Interactive Download shows every result's match score, so you can always pick a lower one by hand.

Underneath: preferred format, format priority, minimum and preferred bitrate, and **Minimum release quality to auto-download** (0-100, default 70). Release quality only judges the file itself (format, bitrate, seeders), never whether it's the right book.

## Media Management

- **Naming Template**: how books are laid out in your library folder, with a live preview so you can see the result before saving. **Simple** gives `Author/Series/Title/Title`; **Simple ASIN** adds the ASIN to the file name.
- **Conversion Quality**: whether MP3 downloads get merged into a single M4B, plus the codec, bitrate and channels under **Advanced**. The **Copy** codec repackages the audio without re-encoding it.
- **Library Routing** (once you have more than one library): **Keep series together**, routing rules (language, author, series, narrator) checked from the top, and a **Test** box. See [Multiple Libraries](Multiple-Libraries#where-new-books-go).

## Metadata

**Scheduled Refresh** covers the library check and the metadata jobs (Library, Series, Books, Authors, Hardcover, Release calendar): how often each runs, a **Run now** for each, and the pace between Audible requests. See [Search and Metadata](Search-and-Metadata#keeping-details-fresh).

**Background Tasks** lists everything AuralArchive does on its own: the download monitor, automatic downloads, the Audible wishlist sync, Hardcover, Audiobookshelf, the metadata refresh, the update check and the Audible category sync. Each one shows what it does, how often it runs, when it last ran and when it runs next. The switch beside it turns its schedule on or off, and **Run now** runs it immediately. A task that's waiting for a service you haven't connected says so.

## Discovery

How often the Discover page's genre list refreshes, with **Sync Now** if you don't want to wait.

**Release Calendar:** choose what the Release Calendar page and the dashboard follow. Add a **Tag** (an Audible genre such as Space Opera) or a **Keyword** (such as LitRPG, optionally within a tag; Audible has no LitRPG genre). **Look ahead** and **Look back** set how far forward and back titles are kept. Your unreleased Wanted books always show on the calendar. Nothing is added to your library automatically: use **Add** on the calendar for anything you want. The refresh schedule is under **Settings › Metadata**.

**Marketplace & Language:** your Audible marketplace (set by your Audible sign-in) and your preferred languages. The language setting is used by Discover, the author and series pages, and search.

## Audiobookshelf

Server address and API key with **Test**, library mapping, and the features: listening progress, next in series, telling Audiobookshelf about new books, match hints, metadata matching and the sync interval. The Status card shows linked books, books that couldn't be linked, and **Sync now**. See [Audiobookshelf](Audiobookshelf).

## Connected Services

Sign in to [Audible](Audible-Integration), [Hardcover](Hardcover) and MyAnonamouse. Each account has its own tab with its status, stats and options.

## Maintenance

- **Optimize Database**: runs VACUUM and ANALYZE to keep things snappy
- **Repair Database**: backs the database up first, then checks it, fixes integrity errors and rebuilds indexes
- **Clear Image Cache**: deletes cached covers; they're fetched again when needed
- **Hidden Titles**: review and unhide titles you've hidden
- **Log level**: Debug, Info, Warning or Error, applied immediately
- **Application Logs**: a live view of the log, with Clear, Download and Pause

## Profile

Click your name at the bottom of the sidebar to change your display name and password.

## Notifications

The bell in the header is where AuralArchive tells you what's going on: books found, imported or failed, new releases of books you're waiting for, new titles from your authors and series, finished scheduled refreshes, low disk space or storage problems, an expired Audible sign-in, and new AuralArchive versions. Mark them read or dismiss them one at a time or all at once.
