"""
Module Name: trending.py
Author: AuralArchivist
Created: July 1, 2026
Last Modified: October 9, 2026
Description:
    Fills hardcover_trending_cache for the 1m, 3m, 1y and all-time windows.

Location:
    /integrations/hardcover/trending.py
"""

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from core.logging import get_module_logger
from .connection import HardcoverClient
from .formatting import pick_narrator

_LOGGER = get_module_logger("Integration.Hardcover.Trending")

WINDOWS = ("1m", "3m", "1y", "all")
_WINDOW_DAYS = {"1m": 30, "3m": 90, "1y": 365}
_ACTIVITY_SAMPLE_SIZE = 500
_RESULT_LIMIT = 25

_ACTIVITY_QUERY = """
query RecentActivity($since: timestamptz!, $limit: Int!, $offset: Int!) {
  activities(
    where: { event: { _eq: "UserBookActivity" }, created_at: { _gte: $since }, book_id: { _is_null: false } }
    order_by: { created_at: desc }
    limit: $limit
    offset: $offset
  ) {
    book_id
  }
}
"""

_BOOK_DETAILS_QUERY = """
query TrendingBookDetails($ids: [Int!]!) {
  books(where: { id: { _in: $ids } }) {
    id
    title
    rating
    ratings_count
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

_POPULAR_BOOKS_QUERY = """
query MostPopularBooks($limit: Int!) {
  books(order_by: { users_count: desc }, limit: $limit) {
    id
    title
    rating
    ratings_count
    users_count
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


def _book_to_cache_entry(book: Dict[str, Any], rank: int, stat_label: str) -> Optional[Dict[str, Any]]:
    editions = book.get("editions") or []
    first_audio = next((e for e in editions if e.get("asin")), None)
    if not first_audio:
        return None

    authors = ", ".join(
        c["author"]["name"] for c in (book.get("contributions") or []) if c.get("author")
    ) or None
    cover_url = (first_audio.get("image") or {}).get("url") or (book.get("image") or {}).get("url")
    return {
        "rank": rank,
        "hardcover_book_id": book["id"],
        "title": book.get("title"),
        "author": authors,
        "cover_url": cover_url,
        "stat_label": stat_label,
        "rating": book.get("rating"),
        "ratings_count": book.get("ratings_count"),
        "asin": first_audio.get("asin"),
        "narrator": pick_narrator(first_audio),
        "audio_seconds": first_audio.get("audio_seconds"),
    }


def fetch_trending_by_activity(client: HardcoverClient, window: str) -> List[Dict[str, Any]]:
    """Approximate trending via recent activity volume for the 1m/3m/1y windows."""
    days = _WINDOW_DAYS[window]
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S+00:00")

    counter: Counter = Counter()
    fetched = 0
    page_size = 100
    offset = 0
    while fetched < _ACTIVITY_SAMPLE_SIZE:
        limit = min(page_size, _ACTIVITY_SAMPLE_SIZE - fetched)
        data = client.query(_ACTIVITY_QUERY, {"since": since, "limit": limit, "offset": offset})
        rows = data.get("activities") or []
        if not rows:
            break
        for row in rows:
            book_id = row.get("book_id")
            if book_id:
                counter[book_id] += 1
        fetched += len(rows)
        offset += len(rows)
        if len(rows) < limit:
            break

    if not counter:
        return []

    candidate_ids = [book_id for book_id, _ in counter.most_common(_RESULT_LIMIT * 4)]
    data = client.query(_BOOK_DETAILS_QUERY, {"ids": candidate_ids})
    books_by_id = {b["id"]: b for b in (data.get("books") or [])}

    entries: List[Dict[str, Any]] = []
    for book_id in candidate_ids:
        if len(entries) >= _RESULT_LIMIT:
            break
        book = books_by_id.get(book_id)
        if not book:
            continue
        activity_count = counter[book_id]
        stat_label = f"{activity_count} activity event{'s' if activity_count != 1 else ''}"
        entry = _book_to_cache_entry(book, len(entries) + 1, stat_label)
        if entry:
            entries.append(entry)
    return entries


def fetch_trending_all_time(client: HardcoverClient) -> List[Dict[str, Any]]:
    """All-time trending: sort by overall popularity (users_count)."""
    data = client.query(_POPULAR_BOOKS_QUERY, {"limit": _RESULT_LIMIT * 4})
    books = data.get("books") or []
    entries: List[Dict[str, Any]] = []
    for book in books:
        if len(entries) >= _RESULT_LIMIT:
            break
        users_count = book.get("users_count")
        stat_label = f"{users_count:,} readers" if users_count is not None else "Popular"
        entry = _book_to_cache_entry(book, len(entries) + 1, stat_label)
        if entry:
            entries.append(entry)
    return entries


def fetch_trending(client: HardcoverClient, window: str) -> List[Dict[str, Any]]:
    if window not in WINDOWS:
        raise ValueError(f"Unknown trending window: {window!r} (expected one of {WINDOWS})")
    if window == "all":
        return fetch_trending_all_time(client)
    return fetch_trending_by_activity(client, window)
