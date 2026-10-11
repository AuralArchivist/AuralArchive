# Downloads and Queue

This is where AuralArchive goes looking for the books on your Wanted list. It searches your indexers, hands the best release to your download client, and imports the finished files into your library. Books you own on Audible can also be downloaded straight from Audible; see [Audible Integration](Audible-Integration).

## Download clients

**Settings › Download Clients**

| Client | Type | Notes |
|---|---|---|
| qBittorrent | Torrent | API key (qBittorrent 5.2.0 and later). Username and password still work, but are removed in AuralArchive 1.1 |
| SABnzbd | Usenet | API key. Experimental. |
| NZBGet | Usenet | Username and password. Experimental. |

To add one:
1. Click **Add** and choose the **Client Type**.
2. Fill in **Host**, **Port** and the sign-in details. Turn on **Use SSL** or set a **URL Base** if your client needs them.
3. Set a **Category** (for example `audiobooks`) so AuralArchive's downloads are easy to spot in the client.
4. Click **Test**, then **Save Client**.

**Signing in to qBittorrent.** Choose one **Authentication Method**. Only that one is used, and the other's details are cleared when you save. There's no quiet fallback: if the method you picked fails, you'll see why.
- **API Key** (recommended): in qBittorrent 5.2.0 or newer, open **Preferences › WebUI**, create an API key and paste it here. Older qBittorrent versions don't have API keys and will reject this.
- **Username & Password**: deprecated, and gone in AuralArchive 1.1. Switch to an API key before you upgrade to 1.1, which means updating qBittorrent to 5.2.0 or newer if you're on something older.

**Test** uses exactly the same connection as real downloads, so if the test passes, downloads will connect too.

**Path mappings.** If your download client runs in another container or on another machine, it may report download paths AuralArchive can't see, for example `/data/torrents/...` when AuralArchive sees the same files at `/downloads/...`. Turn on **Enable remote path mappings** and add a mapping from the client's path to AuralArchive's path.

## Indexers

**Settings › Indexers**

**Aggregators.** If you use **Prowlarr** or **NZBHydra2**, enter its base URL and API key, click **Test**, then **Sync** to bring its indexers in.

**Indexers.** You can also add Torznab/Newznab indexers, Jackett, or these direct providers:
- **MyAnonamouse**: uses your session cookie (Session ID). The ratio, freeleech and VIP options apply to it, and the MyAnonamouse tab under **Settings › Connected Services** shows your ratio and stats.
- **AudioBookBay**

For each indexer you can set its priority, categories, minimum seeders and a search rate limit.

## Match & Quality

**Settings › Match & Quality** decides which release gets picked when AuralArchive is choosing on its own.

- **Minimum match confidence to auto-download** (default 80): how sure AuralArchive has to be that a release is the right book and edition
- **Preferred format** and **Format priority** (M4B, M4A, MP3, FLAC)
- **Minimum bitrate** and **Preferred bitrate**
- **Minimum release quality to auto-download** (default 70): how good the release itself has to be

These two scores are kept separate on purpose. Match confidence asks "is this the right book?", and release quality asks "is this a good file?". Interactive Download shows both for every result, so you can still pick something below the bar by hand.

## Automatic downloads

**Settings › Download Management**

Turn on **Enable automatic downloads**, and AuralArchive regularly searches your indexers for Wanted books and sends the best match to your download client. **Restrict to schedule window** limits searching to certain hours; a start hour later than the end hour wraps past midnight, so 22 to 6 means overnight.

The same page covers:
- **Download Workflow**: seeding, deleting the source after import, overwriting existing library files, how many downloads run at once, queue priority and how often downloads are checked
- **Disk Space**: stop starting new downloads when free space drops below a floor
- **Tracker Ratio & Freeleech Rules**: a minimum ratio for ratio-tracked private trackers, preferring freeleech or VIP releases, holding out for freeleech for a number of days, and a guard against grabbing the same book twice
- **Retry Policy**: how many times to retry the search, download, conversion and import, and how long to wait between tries

## The Downloads page

The cards at the top show the active queue, downloads in progress, Audible conversions and seeding torrents. **Download Activity** lists every item with live progress, speed and time remaining. The **Seeding** tab shows torrents that are still seeding, and **Recently Imported** shows the books that just landed in your library.

Every item works its way through these stages:

| Stage | Statuses |
|---|---|
| Finding a release | Queued, Searching, Found |
| Downloading | Downloading, Audible Downloading, Complete, Paused |
| Processing | Converting, Converted, Importing, Imported |
| Seeding | Seeding, Seeding Complete |
| Problems | Search Failed, Download Failed, Audible Download Failed, Conversion Failed, Import Failed, Cancelled |

When something fails you can retry it, and the retry picks up at the step that failed. A failed import, for example, just imports the file that's already on disk again. Each queued book also shows the library it's heading for; click it to change it any time before the import starts. The notification bell tells you when a book is found, imported or fails.

## When things go wrong

**AuralArchive can't reach the download client**
- Check the host, port and sign-in details, then use **Test**.
- In Docker, use the client's container name or the host's IP address, not `localhost`.

**Downloads finish but never import**
- The client's download path probably isn't visible to AuralArchive. Add a path mapping in **Settings › Download Clients**.
- Check that the downloads and library folders are writable (**Settings › Storage**).

**Nothing ever gets picked automatically**
- Lower **Minimum match confidence** or **Minimum release quality** a little.
- Check that at least one indexer is turned on and passes its test.
- Make sure the disk space floor or the ratio rules aren't blocking grabs.

**The queue seems stuck**
- Look for failed items on the Downloads page and retry them.
- Check that **Max concurrent downloads** isn't set too low.
