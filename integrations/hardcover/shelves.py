"""
Module Name: shelves.py
Author: AuralArchivist
Created: July 1, 2026
Last Modified: October 9, 2026
Description:
    Pull the Hardcover library and push local status changes back.

Location:
    /integrations/hardcover/shelves.py
"""

from typing import Any, Dict, List, Optional

from core.logging import get_module_logger
from .connection import HardcoverClient

_LOGGER = get_module_logger("Integration.Hardcover.Shelves")

_MY_SHELVES_QUERY = """
query MyShelves($user_id: Int!, $limit: Int!, $offset: Int!) {
  user_books(
    where: { user_id: { _eq: $user_id } }
    order_by: { updated_at: desc }
    limit: $limit
    offset: $offset
  ) {
    id
    status_id
    owned
    rating
    date_added
    book {
      id
      title
      image { url }
      contributions { author { name } }
      default_audio_edition { id asin }
    }
  }
}
"""

_FIND_USER_BOOK_QUERY = """
query FindUserBook($user_id: Int!, $book_id: Int!) {
  user_books(where: { user_id: { _eq: $user_id }, book_id: { _eq: $book_id } }, limit: 1) {
    id
    status_id
    owned
    rating
    privacy_setting_id
  }
}
"""

_INSERT_USER_BOOK_MUTATION = """
mutation InsertUserBook($object: UserBookCreateInput!) {
  insert_user_book(object: $object) {
    id
    user_book {
      id
      status_id
      rating
      privacy_setting_id
    }
  }
}
"""

_UPDATE_USER_BOOK_MUTATION = """
mutation UpdateUserBook($id: Int!, $object: UserBookUpdateInput!) {
  update_user_book(id: $id, object: $object) {
    id
    user_book {
      id
      status_id
      rating
      privacy_setting_id
    }
  }
}
"""

_EDITION_OWNED_MUTATION = """
mutation EditionOwned($id: Int!) {
  edition_owned(id: $id) {
    id
  }
}
"""


def fetch_my_shelves(client: HardcoverClient, hardcover_user_id: int, *, limit: int = 200) -> List[Dict[str, Any]]:
    """Return the full 'my shelves' list (all statuses) for the linked account."""
    results: List[Dict[str, Any]] = []
    offset = 0
    page_size = 100
    while True:
        batch_limit = min(page_size, limit - len(results))
        if batch_limit <= 0:
            break
        data = client.query(_MY_SHELVES_QUERY, {"user_id": hardcover_user_id, "limit": batch_limit, "offset": offset})
        rows = data.get("user_books") or []
        if not rows:
            break
        results.extend(rows)
        offset += len(rows)
        if len(rows) < batch_limit:
            break
    return results


def find_user_book(client: HardcoverClient, hardcover_user_id: int, hardcover_book_id: int) -> Optional[Dict[str, Any]]:
    data = client.query(_FIND_USER_BOOK_QUERY, {"user_id": hardcover_user_id, "book_id": hardcover_book_id})
    rows = data.get("user_books") or []
    return rows[0] if rows else None


def push_shelf_update(
    client: HardcoverClient,
    hardcover_user_id: int,
    hardcover_book_id: int,
    *,
    status_id: Optional[int] = None,
    rating: Optional[float] = None,
    privacy_setting_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Create or update the user_book entry for a Hardcover book with the given fields.

    Only the fields explicitly provided (not None) are sent, so e.g. a
    status-only push doesn't clobber the rating and vice versa.

    Note: "owned" is intentionally NOT a parameter here - it isn't a field on
    UserBookCreateInput/UserBookUpdateInput (confirmed via schema
    introspection). Use set_edition_owned() below instead.
    """
    object_fields: Dict[str, Any] = {}
    if status_id is not None:
        object_fields["status_id"] = status_id
    if rating is not None:
        object_fields["rating"] = rating
    if privacy_setting_id is not None:
        object_fields["privacy_setting_id"] = privacy_setting_id

    if not object_fields:
        raise ValueError("push_shelf_update requires at least one of status_id/rating/privacy_setting_id")

    existing = find_user_book(client, hardcover_user_id, hardcover_book_id)
    if existing:
        data = client.query(
            _UPDATE_USER_BOOK_MUTATION,
            {"id": existing["id"], "object": object_fields},
        )
        return data.get("update_user_book") or {}

    insert_fields = {"book_id": hardcover_book_id, **object_fields}
    data = client.query(_INSERT_USER_BOOK_MUTATION, {"object": insert_fields})
    return data.get("insert_user_book") or {}


def set_edition_owned(client: HardcoverClient, hardcover_edition_id: int) -> bool:
    """Toggle the given edition's owned state for the current user via the
    `edition_owned` mutation. Returns True if the call succeeded (the
    mutation itself is a toggle with no boolean "desired state" input, so
    callers should check current state - e.g. via fetch_my_shelves()'s
    `owned` field on the user_book - before calling this, to avoid
    accidentally toggling the wrong direction)."""
    data = client.query(_EDITION_OWNED_MUTATION, {"id": hardcover_edition_id})
    return data.get("edition_owned") is not None
