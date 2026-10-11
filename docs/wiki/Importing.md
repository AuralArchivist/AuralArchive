# Importing

Already have audiobooks? There are two ways to bring them in, depending on whether they're loose files or an organised library you want to keep as it is.

- **Manual import** takes files from the import folder and copies or moves them into your library, renamed with your naming template.
- **Library Import** adopts an already organised library right where it is, without moving or renaming a thing.

## Manual import

1. Put audiobook files in the import folder (you'll find it in **Settings › Storage**; it's `/import` in Docker), or use **Upload from this computer**.
2. On the **Import** page, browse the **Staging folder** and pick the files or folders you want.
3. Click **Stage Files**. Each book becomes a card showing the match AuralArchive found.
4. Look over the matches. If one's wrong, **Remap Metadata** lets you search for the right book by title, author or ASIN.
5. Select the cards that look right and import them.

AuralArchive matches files by the ASIN in their tags or file name first, then by title and author. **Import Results** shows you what went in and anything that didn't.

With more than one library, a **Library** menu above the cards decides where the books go, using that library's naming template. Changing it re-stages the files so the destinations update.

Supported formats: M4B, M4A, MP3, MP4, FLAC, OGG, Opus and AAC.

## Library Import

This is for when your books are already organised in a folder you want to keep, for example a library Audiobookshelf is already serving. Nothing gets moved.

1. Set that folder as your **Library** in **Settings › Storage**, or add it as another library under **Libraries** on the same page (see [Multiple Libraries](Multiple-Libraries)).
2. On the **Import** page, click **Library Import**, choose the library if you have more than one, then **Scan Library**.
3. AuralArchive groups the files into one book per folder and matches each one as it goes. Folders get marked **Matched**, **Needs Review** or **Unmatched** while the scan runs.
4. For anything that needs review, use **Find the right match**.
5. Select the matched books and import them. The files stay exactly where they are.

## Folders that need review

The scheduled library check (see [Library Management](Library-Management#library-check)) adds new folders it finds in your library on its own, as long as it's confident about the match. When it isn't, the Import page shows **Library folders need review**. Click **Review in Library Import** to match those folders yourself, or tell AuralArchive to stop flagging a folder.

## Tips

- One book per folder gives the best matches.
- An ASIN in the folder or file name, like `Title [B0XXXXXXXX]`, makes the match exact.
- Without Docker, keep the import, downloads and library folders on the same disk, and imports become instant moves instead of copies.
