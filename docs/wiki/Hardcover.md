# Hardcover

[Hardcover](https://hardcover.app) is a friendly book tracking community, a bit like a modern Goodreads. Connecting it gives AuralArchive a **Hardcover** page for finding new books, and it can keep your Hardcover shelves in step with your library.

## Connecting

1. Sign in at hardcover.app, open your account settings and copy your **API token**.
2. In AuralArchive, open **Settings › Connected Services › Hardcover**.
3. Paste the token into **API Token** and click **Connect Account**.

The card then shows your shelf counts (Want to Read, Reading, Read, Owned) and how syncing is going.

The same card has two **sync options**:
- automatically push shelf status changes to Hardcover
- how often, in minutes, to refresh the discovery lists

## The Hardcover page

| Tab | What it shows |
|---|---|
| Trending | Popular audiobooks over the last month, 3 months, year or all time |
| New Releases | Recently released audiobooks |
| My Shelves | Your Hardcover library |
| Search | Books, authors, series and lists |
| Lists | Public lists: Featured, Popular, New, Recently Updated |
| Genres & Moods | Browse by genre or by mood |

Only books with an audiobook edition are shown, since that's what you're here for. **Add to Library** checks the audiobook's ASIN against Audible, adds the book as Wanted, and fills in its series. Open a list to add several books at once.

Trending and new releases are cached and refreshed in the background. **Refresh Cache** on the page refreshes them right now.

## Linking your library

A book needs to be linked to its Hardcover book before its details window shows a **Hardcover** tab (with shelf status, rating, owned, like and lists).

- Books you add from the Hardcover page are linked automatically.
- For anything else, open the book and choose **Match to Hardcover** from the ⋮ menu. If AuralArchive finds the same audiobook ASIN, or a near-identical title by the same author, it links the book straight away. Otherwise it shows you the closest Hardcover books so you can pick.
- The **Hardcover** job in **Settings › Metadata** quietly links the rest of your library in the background. Books it can't match confidently are tried again after 30 days, or you can match them by hand.
- **Unlink from Hardcover** in the same menu removes a link that's wrong.

## Good to know

- Your library in AuralArchive is always the source of truth. Changes go from AuralArchive to Hardcover, never the other way around.
- If your token expires or is revoked, the Hardcover card tells you. Paste a new one to reconnect.
