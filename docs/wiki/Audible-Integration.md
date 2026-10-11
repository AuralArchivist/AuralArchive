# Audible Integration

Signing in to Audible is what turns AuralArchive from a catalogue into *your* library. Once you're connected, it can:

- bring the books you own on Audible into your library
- download those books and convert them to M4B
- keep your Audible wishlist and your Wanted list in step

You don't need to sign in to search the catalogue, look at book details or follow series. That all works without an account.

## Signing in

1. Open **Settings › Connected Services** and choose **Audible**.
2. Enter your Audible **Email** and **Password**, and pick your **Marketplace** (US, UK, CA, AU, DE, FR, IT or ES).
3. Click **Connect Account**.
4. If Amazon asks for a one-time passcode, enter the code from your authenticator app, SMS or email, then click **Verify**.

Your password is only used for that sign-in and is never stored. What AuralArchive keeps is the device sign-in Audible hands out, encrypted, in the `secrets/` folder of your app data.

If the sign-in ever expires or gets revoked, the Audible card says so and the notification bell lets you know. Just sign in again from the same place.

## Syncing your library

The Audible card has a handful of tools:

| Button | What it does |
|---|---|
| **Full Sync** | Reads your whole Audible library and updates every title |
| **Quick Sync** | Only picks up recent purchases and changes, so it's much faster |
| **Refresh Cache** | Refreshes the cached copy of your Audible library |
| **Validate** | Checks your library records against Audible |
| **Download All** | Queues every book you own for download |
| **Export** | Exports your Audible library as a list |
| **Load Stats** | Shows some statistics about your library |

Quick Sync is the one you'll use most. Save Full Sync for when something looks off.

On the Library page, the **Source** filter can show only the books **In my Audible library** or **Not in my Audible library**, which is handy for spotting what came from elsewhere.

## Downloading from Audible

**Direct Download Settings** on the same card control how Audible downloads behave:

- **Preferred Format**: AAXC (Enhanced) or AAX (Legacy)
- **Audio Quality**: Best available, High or Standard
- **Fallback to AAX if AAXC fails**
- **Concurrent Audible downloads**
- Whether to download chapter metadata, cover artwork, and the supplemental PDF when a book has one
- Whether to keep voucher files for AAXC downloads

Downloaded books are decrypted, converted to M4B and imported into your library like any other download. You can watch their progress on the **Downloads** page.

## Your wishlist

While you're signed in, anything on your Audible wishlist shows up in AuralArchive as **Wanted**. Add a book to your wishlist on your phone, and it's waiting for you here.

## When things go wrong

**The sign-in fails**
- Make sure the marketplace matches the country of your Audible account.
- Give it a few minutes and try again. Amazon limits repeated sign-in attempts.
- Look in **Settings › Maintenance › Application Logs** for the actual error.

**New purchases don't appear**
- Run **Quick Sync**, and if that doesn't do it, **Full Sync**.
- Check that the Audible card still says **Connected**.

**A download fails**
- Make sure the book is owned by the signed-in account.
- If AAXC keeps failing, turn on **Fallback to AAX if AAXC fails**.
- Check there's enough free space in **Settings › Storage**.

**Some books are missing or look wrong**
- Titles differ between marketplaces. Sign in with the marketplace you bought your books in.
