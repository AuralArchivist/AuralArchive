# First Time Setup

You've got AuralArchive running. Here's how to get from a blank page to a working library in a few minutes.

## 1. Create your account

Open AuralArchive at `http://<your-server>:8765`. The first visit takes you straight to account setup. Pick a username and a password of at least 8 characters, and that's the account you'll sign in with from then on.

## 2. Choose your storage folders

Next, AuralArchive shows you where it plans to keep things:

| Folder | What it's for |
|---|---|
| Library | Your organised audiobooks. This is the folder to point Audiobookshelf at. |
| Downloads | Where your download client saves files |
| Import | A drop folder for books you add by hand |
| Work | Scratch space for Audible downloads and conversions |

Each folder already has a built-in location that works as it is. Leave a field blank to keep it, or type in your own folder. A custom folder has to exist already and be writable. In Docker these are paths inside the container, so mount your host folders first (see [Installation](Installation)).

Don't worry about getting it perfect. You can change any of these later in **Settings › Storage**, which also warns you about missing folders, low disk space and Docker folders that aren't mounted.

## 3. Connect your services

Nothing here is required, and all of it can wait until you're ready.

1. **Audible**: **Settings › Connected Services › Audible**. This is what brings in the books you own. See [Audible Integration](Audible-Integration).
2. **Download client**: **Settings › Download Clients**. See [Downloads and Queue](Downloads-and-Queue).
3. **Indexers**: **Settings › Indexers**. See [Downloads and Queue](Downloads-and-Queue#indexers).
4. **Hardcover**: **Settings › Connected Services › Hardcover**. See [Hardcover](Hardcover).

## 4. A few settings worth a look

- **Settings › Media Management**: how books are named in your library folder, and whether MP3 downloads get merged into a single M4B.
- **Settings › Download Management**: automatic downloads, seeding, retries and how much free space to keep.
- **Settings › Metadata**: how often the library check and metadata refresh run.
- **Settings › General**: the theme. Pick the one you like looking at.

Anything you save takes effect straight away; there's no restart needed. [Settings](Settings) walks through every section if you want the full tour.

## Where to go next

- [Audible Integration](Audible-Integration)
- [Library Management](Library-Management)
