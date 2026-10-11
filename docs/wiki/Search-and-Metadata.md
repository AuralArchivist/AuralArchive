# Search and Metadata

## Search

**Search** looks things up in the Audible catalogue. Type a title, an author or a series and see what comes back.

- Grid, list and table views
- **Filter**: All Results, In Library, Not in Library, Wanted, Downloadable
- **Sort**: Relevance, Title, Author, Rating or Release Date
- The summary bar shows how many results you already have or want

Each result shows its cover, series and book number, narrator, runtime and rating. **Details** opens the full record, **Similar** finds related books, and the status button adds the book to your library as Wanted, or tells you it's already there.

If you have more than one library, clicking **Add** asks which one the book should go to. Leave it on **Automatic** and your routing rules decide, or pick a library yourself. You can change it later in the book's details. See [Multiple Libraries](Multiple-Libraries).

Looking for a *download* rather than a book? Open the book in your Library and use **Interactive Download**, which searches your indexers. See [Downloads and Queue](Downloads-and-Queue).

## Discover

**Discover** is for when you don't know what you want next. It suggests books based on what you already own, so it needs an Audible sign-in (**Settings › Connected Services**).

- **Recommended For You**: books from your favourite authors and series, plus best sellers in the Audible categories you own the most of
- **Browse Genres**: pick a category and wander through it
- **New Releases** and **Popular Books** from your favourite categories. Popular Books is a single ranking across all of them, based on best-seller position, how many people rated a book and its average rating.

The genre list refreshes on the schedule in **Settings › Discovery**, or straight away with **Sync Now**.

Want to know what other readers are into? The Hardcover community's trending books and lists are on the [Hardcover](Hardcover) page.

## Release Calendar

The **Release Calendar** shows what's coming out for the things you follow, so a new book in your favourite genre doesn't sneak past you.

You choose what to follow in **Settings › Discovery › Release Calendar**. A watch can be a **Tag** (an Audible genre such as Space Opera) or a **Keyword** (such as LitRPG, optionally limited to a tag; Audible has no LitRPG genre, so a keyword is the way to follow it). Your own Wanted books that aren't out yet always appear too.

The page gives you:
- tiles for what's releasing today, in the next 7 and 30 days, and your unreleased Wanted books
- a small month view to jump to a date, with dots on busy days and the ones that have a Wanted book
- filters for each watch, a search box, and **Show recent releases** to look back as well
- a day-by-day timeline of covers. Click one to open its details in a side panel.

Nothing is added to your library on its own. When something catches your eye, use **Add**. The dashboard also has a **Releasing This Week** row, and the bell tells you about books releasing today and this week. The calendar refreshes daily by default; the schedule lives in **Settings › Metadata**, or press **Refresh** on the page.

## Where the details come from

Book details (title, authors, narrators, series and book number, runtime, rating, release date, publisher, summary and cover) come from Audible, looked up by ASIN. Books without an ASIN are matched by title and author first.

Covers are cached locally in the app data `cache/` folder, so pages load quickly. **Settings › Maintenance › Clear Image Cache** empties it, and covers are fetched again as they're needed.

## Keeping details fresh

**Settings › Metadata › Scheduled Refresh** runs these jobs in the background:

| Job | What it does |
|---|---|
| Library | Checks your library folder for moved, missing and new books (see [Library Management](Library-Management#library-check)) |
| Series | Finds new titles and series details, so new books show up as missing |
| Books | Refreshes book details, ratings and covers |
| Authors | Fetches author bios and photos |
| Hardcover | Links books to Hardcover (see [Hardcover](Hardcover#linking-your-library)) |
| Release calendar | Looks for upcoming titles for your watches |

Each job can run daily, every 3 days, weekly, every 2 weeks, monthly, or not at all, and each has its own **Run now** button. **Pace** sets the pause between Audible requests, from 1 to 10 seconds; slower is gentler on Audible. Authors with no details yet are fetched shortly after startup. Series work the same way: when you add books, their series are looked up in the background within a few minutes, and that doesn't need an Audible sign-in.

You can also refresh a single book from its details window, a series from its series page, or a bunch of books at once with **Select Books** in the Library.

## When things go wrong

**Search finds nothing**
- Try fewer words, part of the title, or the series name.
- Check the marketplace on your Audible sign-in. Catalogues differ from country to country.

**A book has wrong or missing details**
- Refresh it from its details window.
- Check its ASIN is right.

**Covers won't load**
- Refresh the book, or clear the image cache in **Settings › Maintenance**.

**A series is in the wrong order**
- Refresh the series from its page. The order comes from Audible's series data.
