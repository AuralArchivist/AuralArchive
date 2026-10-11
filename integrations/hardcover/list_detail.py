"""
Module Name: list_detail.py
Author: AuralArchivist
Created: July 1, 2026
Last Modified: October 9, 2026
Description:
    Fetches a Hardcover list and a page of its books.

Location:
    /integrations/hardcover/list_detail.py
"""

from typing import Any, Dict, Optional

from core.logging import get_module_logger
from .connection import HardcoverClient
from .formatting import pick_narrator

_LOGGER = get_module_logger("Integration.Hardcover.ListDetail")

_LIST_DETAIL_QUERY = """
query ListDetail($id: Int!) {
  lists_by_pk(id: $id) {
    id
    name
    description
    books_count
    likes_count
    user { username }
  }
}
"""

_LIST_BOOKS_QUERY = """
query ListBooks($id: Int!, $limit: Int!, $offset: Int!) {
  lists(where: { id: { _eq: $id } }) {
    list_books(order_by: { position: asc }, limit: $limit, offset: $offset) {
      position
      book {
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
  }
}
"""


def _book_to_entry(book: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    editions = book.get("editions") or []
    first_audio = next((e for e in editions if e.get("asin")), None)
    if not first_audio:
        return None

    authors = ", ".join(
        c["author"]["name"] for c in (book.get("contributions") or []) if c.get("author")
    ) or None
    cover_url = (first_audio.get("image") or {}).get("url") or (book.get("image") or {}).get("url")
    return {
        "hardcover_book_id": book["id"],
        "title": book.get("title"),
        "author": authors,
        "cover_url": cover_url,
        "rating": book.get("rating"),
        "ratings_count": book.get("ratings_count"),
        "asin": first_audio.get("asin"),
        "narrator": pick_narrator(first_audio),
        "audio_seconds": first_audio.get("audio_seconds"),
    }


def fetch_list_detail(client: HardcoverClient, list_id: int) -> Optional[Dict[str, Any]]:
    data = client.query(_LIST_DETAIL_QUERY, {"id": list_id})
    lst = data.get("lists_by_pk")
    if not lst:
        return None
    return {
        "id": lst["id"],
        "name": lst.get("name"),
        "description": lst.get("description"),
        "books_count": lst.get("books_count"),
        "likes_count": lst.get("likes_count"),
        "username": (lst.get("user") or {}).get("username"),
    }


def fetch_list_books(client: HardcoverClient, list_id: int, *, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
    """Return a page of audio-only, ASIN-bearing entries for a list, in the
    list's own display order. Books with no audio edition are dropped - same
    rule as Trending/New Releases/Search.

    Since filtering happens after the raw page is fetched, the number of
    entries returned can be less than `limit` even when more list_books
    remain - the caller should keep paginating (via `next_offset`) while
    `has_more` is true rather than assuming a short page means the end."""
    data = client.query(_LIST_BOOKS_QUERY, {"id": list_id, "limit": limit, "offset": offset})
    lists = data.get("lists") or []
    list_books = lists[0].get("list_books") if lists else []
    list_books = list_books or []

    entries = []
    for lb in list_books:
        book = lb.get("book")
        if not book:
            continue
        entry = _book_to_entry(book)
        if entry:
            entries.append(entry)

    raw_count = len(list_books)
    return {
        "entries": entries,
        "next_offset": offset + raw_count,
        "has_more": raw_count == limit,
    }
