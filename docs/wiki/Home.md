# AuralArchive

AuralArchive is a self-hosted web app for people who care about their audiobook collection. It keeps track of what you own and what you're still waiting for, goes and finds the books you're missing, and files everything into a tidy, consistently named library folder that players like Audiobookshelf can serve straight away.

Behind the scenes it reads book details from Audible, follows your favourite authors and series so new releases don't slip past you, searches your indexers, hands the best release to your download client, and imports the finished files for you.

> Found a bug or something that doesn't feel right? Please open an issue on the [issue tracker](https://github.com/AuralArchivist/AuralArchive/issues). Every report helps.

## Where to start

1. **[Installation](Installation)**: Docker is the easy route, but running it directly with Python works too.
2. **[First Time Setup](First-Time-Setup)**: create your account and tell AuralArchive where your books live.
3. **[Audible Integration](Audible-Integration)**: sign in so your Audible library shows up.
4. **[Downloads and Queue](Downloads-and-Queue)**: connect an indexer and a download client when you're ready to fill the gaps.

You don't have to do it all on day one. Each step is optional and you can come back to it later.

## What it can do

- **Your library**: every book you own or want, with covers, series, narrators, ratings and runtime.
- **Authors and series**: see exactly which books you're missing from a series or an author's catalogue, and add them in one click.
- **Search and Discover**: look up anything in the Audible catalogue, or let AuralArchive suggest books based on what you already like.
- **Release Calendar**: follow the genres and keywords you care about and see what's coming out, day by day.
- **Hardcover**: trending books, new releases, lists and your own Hardcover shelves.
- **Automatic downloads**: AuralArchive searches your indexers for wanted books and sends the best match to your download client, following the rules you set.
- **Audible downloads**: download the books you own on Audible and convert them to M4B.
- **Importing**: bring in books you already have, or adopt an organised library exactly where it sits.
- **Library check**: notices when books are moved, go missing or turn up, and keeps the database honest.
- **Multiple libraries**: keep children's books, another language or a second disk in their own folders, with new books sent to the right one.
- **Audiobookshelf**: an optional connection for listening progress, next-in-series alerts and metadata matching.
- **Notifications**: the bell in the header keeps you posted about finished downloads, failures, new releases and anything that needs your attention.

## The pages in this wiki

| Page | What you'll find |
|---|---|
| [Installation](Installation) | Docker, running without Docker, NAS setups and updating |
| [First Time Setup](First-Time-Setup) | Your account, storage folders and the first settings worth a look |
| [Audible Integration](Audible-Integration) | Signing in, syncing your library and Audible downloads |
| [Library Management](Library-Management) | The library, authors, series, the library check and hidden titles |
| [Search and Metadata](Search-and-Metadata) | Search, Discover, the Release Calendar and keeping details fresh |
| [Downloads and Queue](Downloads-and-Queue) | Download clients, indexers, automatic downloads and the queue |
| [Importing](Importing) | Manual import and Library Import |
| [Multiple Libraries](Multiple-Libraries) | Several library folders and how new books find their home |
| [Hardcover](Hardcover) | Connecting Hardcover and browsing it |
| [Audiobookshelf](Audiobookshelf) | Listening progress, next in series and metadata matching |
| [Settings](Settings) | A tour of every settings section |
| [Troubleshooting](Troubleshooting) | Common problems, logs and backups |

## What you need

- Docker (recommended), or Python 3.14 on Linux. The free-threaded build of Python is recommended; see [Installation](Installation).
- FFmpeg, if you run without Docker. The Docker image already has it.
- On Windows, use Docker Desktop. macOS isn't supported.
