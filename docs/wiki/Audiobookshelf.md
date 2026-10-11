# Audiobookshelf

AuralArchive and [Audiobookshelf](https://www.audiobookshelf.org) get along well. AuralArchive looks after the files, and Audiobookshelf plays them. They already work together without any setup, since Audiobookshelf can simply read AuralArchive's library folder. Connecting the two adds some nice extras:

- **Listening progress** on the Library page and in book details
- **Next in series** notifications when someone finishes a book
- **New books show up in Audiobookshelf** right after they're imported
- **Metadata matching**: Audiobookshelf looks up every linked book by its exact ASIN
- **Match hints** for library folders AuralArchive can't identify on its own
- A **Play in Audiobookshelf** button on each linked book

You can connect without worry: AuralArchive never deletes anything in Audiobookshelf and never changes its settings.

## Connecting

1. In Audiobookshelf, open **Settings › API Keys**, create a key and copy it. Choose which user it acts as:
   - a **normal user** is enough for progress, next in series and play links
   - an **admin** also lets AuralArchive announce new books, read other users' progress and match lots of books at once
2. In AuralArchive, open **Settings › Audiobookshelf**.
3. Enter the server address (for example `http://192.168.1.10:13378`) and the key, then click **Test**. The result lists what the key is allowed to do.
4. Turn **Connection on** and click **Save**.

In Docker, use an address the AuralArchive container can actually reach: the host's IP, or the Audiobookshelf container name if both are on the same Docker network.

If you paste an old user token rather than an API key, the test warns you. Old tokens never expire, so switching to an API key is a good idea.

## Library mapping

Each AuralArchive library is paired with the Audiobookshelf library that holds the same folder. After a successful test, AuralArchive suggests the pairing that shares the most books. Check it looks right and click **Save Mapping**.

Books are linked to Audiobookshelf items by **exact ASIN**, or by the **same folder** inside the library. There's no guessing by title, so a book is never linked to the wrong item. The two apps don't even need to see the folder at the same path.

**Status**, at the bottom of the page, lists books that couldn't be linked (with a menu to link each one by hand) and books that are in Audiobookshelf but not in AuralArchive.

## The features in detail

**Listening progress.** Library cards show a tick for finished books and a ring for books you're partway through, and the Library page gets a **Listening** filter. Book details list each person's progress. You choose whose progress counts: the key's user, everyone (with an admin key), or specific users. A book counts as finished if any of them finished it.

**Next in series.** When someone finishes a book in a series, you get a notification about the next one: whether it's already in your library, downloading, on your Wanted list, already out, or still to come. Nothing is added to Wanted on its own; that's your call.

**New books in Audiobookshelf.** After an import, AuralArchive tells Audiobookshelf about the new folder so the book appears within a minute, then links the new item. If the Audiobookshelf library's folder watcher is off, it asks for a library scan instead, and a burst of imports turns into a single scan. This needs an admin key.

**Match hints.** When the library check or Library Import can't work out what a folder is, AuralArchive asks Audiobookshelf whether it has an ASIN for that folder. The book is only imported automatically if the title or author agrees with the folder; otherwise the suggestion goes to the review list for you to check.

**Metadata.** AuralArchive asks Audiobookshelf to match each linked book by its exact ASIN in your Audible marketplace, so Audiobookshelf fills in the description, cover, genres, tags, narrators and series numbers itself.

| Setting | What happens |
|---|---|
| Off | Nothing is matched |
| Match and fill gaps | Only empty fields and missing covers are filled, so edits you made in Audiobookshelf are kept |
| Match and replace | Details and covers are replaced (you'll be asked to confirm) |

**Also use AuralArchive's authors and series** sends your author names and series from AuralArchive after the match, which is useful if you corrected them here.

New books are matched as soon as they're linked. For books already in Audiobookshelf, click **Preview** to see what's missing, then **Match all linked books**.

One thing to watch for: if an Audiobookshelf library reads folder names last in its metadata order of precedence, matched titles revert to the folder name on its next scan. The mapping card warns you when that's the case.

## Syncing

Links and progress sync every 6 hours by default (**Sync every** on the Features card; set it to 0 to turn it off). **Sync now** on the Status card runs one straight away. Announcing new books happens separately, within seconds of each import.

If Audiobookshelf can't be reached, AuralArchive carries on as normal and tries again later. If the key stops working, you get a notification and syncing waits until you save a new key.
