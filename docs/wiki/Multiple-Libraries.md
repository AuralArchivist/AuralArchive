# Multiple Libraries

Not every book belongs in the same place. Maybe the kids' books should live in their own folder, or you listen in two languages, or your main disk is full and a second one just arrived. AuralArchive can keep books in more than one library folder, each with its own naming template if you want one. New books are sent to the right library automatically, and the library check looks after all of them.

If you only have one library, none of this gets in your way. The extra options stay hidden until you add a second one.

## Adding a library

1. If you run AuralArchive in Docker, add a volume for the new folder first (see [Installation](Installation)).
2. Open **Settings › Storage › Libraries** and click **Add**.
3. Give it a name and a folder, plus a naming template if it should differ from the global one.
4. Click **Save**.

Your existing library is called **Main**. It always uses the **Library** folder in **Media Folders** above the list, so if you want to change Main's folder, change it there (or with `AURALARCHIVE_LIBRARY_DIR`).

A library folder:
- must already exist (or be creatable) and be writable by AuralArchive
- can't sit inside another library, or contain one
- can't sit inside the downloads, import or work folders, or contain them

**Default library:** books that don't match any rule end up here. Tick **Default library** on another library to change it.

**Disabled libraries** are skipped by the library check and by routing, but their books stay in your library list.

**Deleting a library** never touches its folder or files. If it still has books, you'll choose a library to move them to first.

**Repair assignments** (on the Libraries card) re-reads every book's file location and works out its library from that. Use it if you've been moving folders around by hand.

## Where new books go

When a book is added, queued for download or imported, AuralArchive picks its library in this order:

1. **Your choice.** You can pick a library yourself in several places: when you **Add** a book from Search, on the Import page and in Library Import, and on the Downloads page for anything queued.
2. **The library you chose for a wanted book.** Open a wanted book and use the **Library** menu in its **Files** tab. It's saved straight away and used when the book is downloaded. Pick **Automatic** to let the rules below decide again.
3. **Its series.** If other books from the same series are already in a library, the new one joins them, so a series never gets split up. You can turn this off in **Settings › Media Management › Library Routing**.
4. **Rules.** The rules in **Library Routing** are checked from the top: language is, author contains, series contains, narrator contains. The first one that matches wins.
5. **The default library.**

The **Test** box under the rules shows where a book would go and why. Enter an ASIN from your library, or an author, series and language.

On the **Downloads** page, every queued book shows its target library. Click it to change it any time before the import starts. If the chosen library is disabled, or its folder can't be reached when the download finishes, the book goes to the default library instead and you get a notification.

## Moving a book to another library

Open the book, go to the **Files** tab and use **Move to library**. AuralArchive shows you the new location (named with the target library's template), then moves the book's folder and tidies up any empty folders left behind. Books that share a folder with another book can't be moved this way.

Moving folders yourself works too. The next library check finds the book in its new home.

## The library check with several libraries

The library check (**Settings › Metadata**, the **Library** job) looks at each library separately:

- **A library whose folder can't be reached** is skipped, and you get a notification. Its books are left exactly as they are and are never marked missing while the drive is offline.
- **A library folder that's empty** while books still point into it is treated as not mounted, and skipped the same way.
- **The safety limit** that stops a run from marking lots of books missing at once applies to each library on its own.
- **Books moved between libraries** are found again by ASIN or by folder, and their library is updated.
- **New folders** in any library are imported in place or listed for review on the Import page, labelled with their library.

## Docker

Every library needs its own volume, or its books disappear when the container is recreated. AuralArchive won't let you save a library on a folder that isn't mounted from the host, and **Settings › Storage** warns about any library that isn't.

Each library is its own mount, so books are copied into it and then the original is removed.
