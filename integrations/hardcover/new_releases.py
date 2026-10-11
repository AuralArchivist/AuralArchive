"""
Module Name: new_releases.py
Author: AuralArchivist
Created: July 1, 2026
Last Modified: October 9, 2026
Description:
    Populates hardcover_new_releases_cache with upcoming audiobook releases
    (books with a release_date today or later, that have at least one audio
    edition), ordered soonest-first.

Location:
    /integrations/hardcover/new_releases.py
"""

from datetime import date
from typing import Any, Dict, List

from core.logging import get_module_logger
from .connection import HardcoverClient
from .formatting import pick_narrator

_LOGGER = get_module_logger("Integration.Hardcover.NewReleases")

_RESULT_LIMIT = 50

_UPCOMING_QUERY = """
query UpcomingReleases($today: date!, $limit: Int!) {
  books(
    where: {
      release_date: { _gte: $today }
      editions: { reading_format_id: { _in: [2, 3] } }
    }
    order_by: { release_date: asc }
    limit: $limit
  ) {
    id
    title
    release_date
    image { url }
    contributions { author { name } }
    editions(where: { reading_format_id: { _in: [2, 3] } }) {
      id
      asin
      audio_seconds
      image { url }
      contributions { contribution author { name } }
    }
  }
}
"""


def fetch_new_releases(client: HardcoverClient) -> List[Dict[str, Any]]:
    today = date.today().isoformat()
    data = client.query(_UPCOMING_QUERY, {"today": today, "limit": _RESULT_LIMIT})
    books = data.get("books") or []

    entries = []
    for book in books:
        editions = book.get("editions") or []
        first_audio = next((e for e in editions if e.get("asin")), None)
        if not first_audio:
            continue

        authors = ", ".join(
            c["author"]["name"] for c in (book.get("contributions") or []) if c.get("author")
        ) or None
        cover_url = (first_audio.get("image") or {}).get("url") or (book.get("image") or {}).get("url")
        entries.append({
            "hardcover_book_id": book["id"],
            "title": book.get("title"),
            "author": authors,
            "cover_url": cover_url,
            "release_date": book.get("release_date"),
            "asin": first_audio.get("asin"),
            "narrator": pick_narrator(first_audio),
            "audio_seconds": first_audio.get("audio_seconds"),
        })
    return entries
