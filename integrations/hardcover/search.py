"""
Module Name: search.py
Author: AuralArchivist
Created: July 1, 2026
Last Modified: October 9, 2026
Description:
    Hardcover search for books, authors, series and lists, plus genre and mood
    browsing.

Location:
    /integrations/hardcover/search.py
"""

from typing import Any, Dict, List, Optional

from .connection import HardcoverClient

BROWSE_GENRES = [
    "Fantasy", "Science Fiction", "Mystery", "Thriller", "Romance", "Horror",
    "Historical Fiction", "Nonfiction", "Biography", "Young Adult",
    "Literary Fiction", "Fiction",
]
BROWSE_MOODS = [
    "Adventurous", "Dark", "Emotional", "Funny", "Hopeful", "Inspiring",
    "Mysterious", "Reflective", "Relaxing", "Tense",
]

_SEARCH_QUERY = """
query Search($query: String!, $query_type: String!, $per_page: Int!, $page: Int!, $fields: String, $weights: String, $typos: String) {
  search(query: $query, query_type: $query_type, per_page: $per_page, page: $page, fields: $fields, weights: $weights, typos: $typos) {
    query
    query_type
    page
    per_page
    results
  }
}
"""

_LISTS_BROWSE_QUERY = """
query BrowseLists($orderBy: [lists_order_by!]!, $limit: Int!, $offset: Int!) {
  lists(where: { public: { _eq: true } }, order_by: $orderBy, limit: $limit, offset: $offset) {
    id
    name
    description
    books_count
    likes_count
    updated_at
    user { username }
    list_books(order_by: { position: asc }, limit: 8) {
      book { image { url } }
    }
  }
}
"""

_LISTS_SORT_ORDER_BY = {
    "featured": [{"featured": "desc"}, {"likes_count": "desc"}],
    "popular": [{"likes_count": "desc"}],
    "new": [{"created_at": "desc"}],
    "updated": [{"updated_at": "desc"}],
}


def search(
    client: HardcoverClient,
    query: str,
    query_type: str = "book",
    *,
    per_page: int = 20,
    page: int = 1,
    fields: Optional[str] = None,
    weights: Optional[str] = None,
    typos: Optional[str] = None,
) -> Dict[str, Any]:
    """Run a Hardcover search. query_type: book / author / series / list.

    Per docs/HardcoverAPI/guides/searching.md, `fields`/`weights`/`typos` must
    be passed together - Hardcover's API silently returns an entirely null
    `search` object (no error) if `fields` is set without matching `weights`
    and `typos` values, so callers overriding `fields` must also supply both.
    """
    variables = {"query": query, "query_type": query_type, "per_page": per_page, "page": page}
    variables["fields"] = fields
    variables["weights"] = weights
    variables["typos"] = typos
    data = client.query(_SEARCH_QUERY, variables)
    return data.get("search") or {}


def browse_by_genre(client: HardcoverClient, genre: str, *, per_page: int = 20, page: int = 1) -> Dict[str, Any]:
    return search(client, genre, "book", per_page=per_page, page=page, fields="genres", weights="1", typos="0")


def browse_by_mood(client: HardcoverClient, mood: str, *, per_page: int = 20, page: int = 1) -> Dict[str, Any]:
    return search(client, mood, "book", per_page=per_page, page=page, fields="moods", weights="1", typos="0")


def browse_lists(
    client: HardcoverClient,
    sort: str = "featured",
    *,
    per_page: int = 20,
    page: int = 1,
) -> List[Dict[str, Any]]:
    """Browse public lists without a search query (Hardcover's Lists page
    supports Featured/Popular/New/Recently Updated browsing - see
    docs/HardcoverAPI/schemas/lists.md's `lists` query). sort: featured / popular / new / updated."""
    order_by = _LISTS_SORT_ORDER_BY.get(sort, _LISTS_SORT_ORDER_BY["featured"])
    offset = max(page - 1, 0) * per_page
    data = client.query(_LISTS_BROWSE_QUERY, {"orderBy": order_by, "limit": per_page, "offset": offset})
    lists = data.get("lists") or []
    for lst in lists:
        covers = []
        for lb in (lst.pop("list_books", None) or []):
            book = lb.get("book") or {}
            url = (book.get("image") or {}).get("url")
            if url:
                covers.append(url)
        lst["cover_urls"] = covers
    return lists
