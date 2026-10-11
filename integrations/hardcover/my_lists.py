"""
Module Name: my_lists.py
Author: AuralArchivist
Created: July 2, 2026
Last Modified: October 9, 2026
Description:
    Manage the connected account's own Hardcover lists.

Location:
    /integrations/hardcover/my_lists.py
"""

from typing import Any, Dict, List, Optional

from core.logging import get_module_logger
from .connection import HardcoverClient

_LOGGER = get_module_logger("Integration.Hardcover.MyLists")

_MY_LISTS_QUERY = """
query MyLists($user_id: Int!) {
  lists(where: { user_id: { _eq: $user_id } }, order_by: { updated_at: desc }) {
    id
    name
    description
    books_count
    privacy_setting_id
  }
}
"""

_INSERT_LIST_MUTATION = """
mutation InsertList($object: ListInput!) {
  insert_list(object: $object) {
    id
    list {
      id
      name
      description
      privacy_setting_id
    }
  }
}
"""

_INSERT_LIST_BOOK_MUTATION = """
mutation InsertListBook($object: ListBookInput!) {
  insert_list_book(object: $object) {
    id
    list_book {
      id
      list_id
      book_id
      position
    }
  }
}
"""

_DELETE_LIST_BOOK_MUTATION = """
mutation DeleteListBook($id: Int!) {
  delete_list_book(id: $id) {
    id
  }
}
"""


def fetch_my_lists(client: HardcoverClient, hardcover_user_id: int) -> List[Dict[str, Any]]:
    data = client.query(_MY_LISTS_QUERY, {"user_id": hardcover_user_id})
    return data.get("lists") or []


def create_list(
    client: HardcoverClient,
    name: str,
    *,
    description: Optional[str] = None,
    privacy_setting_id: int = 1,
) -> Dict[str, Any]:
    """Create a new list owned by the connected account. privacy_setting_id:
    1=Public, 2=Followers, 3=Private."""
    object_fields: Dict[str, Any] = {"name": name, "privacy_setting_id": privacy_setting_id}
    if description:
        object_fields["description"] = description
    data = client.query(_INSERT_LIST_MUTATION, {"object": object_fields})
    return data.get("insert_list") or {}


def add_book_to_list(client: HardcoverClient, list_id: int, hardcover_book_id: int) -> Dict[str, Any]:
    """Add a book to a list owned by the connected account. Hardcover only
    allows adding books to lists you own."""
    data = client.query(
        _INSERT_LIST_BOOK_MUTATION,
        {"object": {"list_id": list_id, "book_id": hardcover_book_id}},
    )
    return data.get("insert_list_book") or {}


def remove_book_from_list(client: HardcoverClient, list_book_id: int) -> bool:
    data = client.query(_DELETE_LIST_BOOK_MUTATION, {"id": list_book_id})
    return data.get("delete_list_book") is not None
