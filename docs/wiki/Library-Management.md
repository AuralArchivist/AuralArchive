# Library Management

## The Library page

The **Library** page is home base: every book AuralArchive knows about, whether it's on your disk or still on your wish list.

**The toolbar**
- **Search Library**: filters by title, author or series as you type
- **Status**: Owned, Wanted or Missing
- **Source**: In my Audible library / Not in my Audible library
- **Genre**
- **Sort by**: Title, Author, Rating or Date Added
- **Card Density**: how big the cards are in grid view
- Grid, list and table views
- **Clear filters**, and **Select Books** for bulk actions

**What the statuses mean**
| Status | Meaning |
|---|---|
| Owned | The book's files are in your library (or you own it on Audible) |
| Wanted | You'd like it, so it can be searched for and downloaded |
| Missing | It should be in your library, but its files can't be found on disk |

**Book details.** Click a cover to open the book. From there you can refresh its details, run an **Interactive Download** (where you pick a release yourself from your indexers), start an automatic download, or delete it. For a wanted book, the **Files** tab also lets you choose which library it goes to once it's downloaded; see [Multiple Libraries](Multiple-Libraries).

**Bulk actions.** Click **Select Books**, tick the ones you want, and you can refresh their details, change their status or delete them all at once.

## Authors

**Authors** lists everyone in your library, with how many of their books and series you have, the total hours, and how much of their catalogue you've collected.

Open an author to see:
- **Highlights**: your most recent addition, their main publisher and the narrator you hear most
- **Catalogue Coverage**: how many titles are tracked, how much you own, and what's missing
- **Series Overview**: each of their series with your owned count
- **Standalone Titles**
- Buttons to add their whole catalogue as Wanted, or open their Audible page

## Series

**Series** lists every series with owned, missing and total counts and a progress bar, so you can see at a glance which ones you're close to finishing.

Open a series to see every book in order, with narrator, runtime, rating and whether you own it. From here you can refresh the series from Audible, add the missing books to your Wanted list, or search your indexers for them.

## Hiding titles

Every series has its clutter: other editions, box sets, books you've already read somewhere else. You can hide them from series and author pages so they stop getting in the way. Hidden books you own stay in your library but are left out of automatic searches. Hidden books you don't own simply stop being tracked.

Changed your mind? Tick **Show hidden titles** on the series or author page, or go to **Settings › Maintenance › Hidden Titles** to bring them back.

## Library check

The library check makes sure the database matches what's actually on your disk. It runs on the schedule you set in **Settings › Metadata** (the **Library** job), and each time it:

- finds books whose folders were moved or renamed, and relinks them
- marks owned books whose files have gone as **Missing**
- marks Missing books whose files have come back as **Owned** again
- finds book folders the database doesn't know about yet and adds them. If it isn't sure what a folder is, it lists it for you to review on the **Import** page rather than guessing.

It's careful by design. It never deletes files and never changes a book back to Wanted. If a library folder can't be reached, or looks empty while books point into it, it skips that library and leaves its books alone, so an unplugged drive doesn't turn your whole library Missing. And if a run would mark a big part of a library missing at once, it stops and asks you first. With several libraries, each one is checked on its own; see [Multiple Libraries](Multiple-Libraries#the-library-check-with-several-libraries).

## Tips

- Keep one book per folder. Putting the ASIN in folder or file names (the **Simple ASIN** naming template does this for you) makes matching exact.
- Point Audiobookshelf or another player at the same library folder. AuralArchive looks after the files and your player reads them. Connect [Audiobookshelf](Audiobookshelf) to see your listening progress here too.
