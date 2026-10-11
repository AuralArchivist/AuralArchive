"""
Module Name: likes.py
Author: AuralArchivist
Created: July 2, 2026
Last Modified: October 9, 2026
Description:
    Like or unlike Hardcover entities (UserBook entries today).

Location:
    /integrations/hardcover/likes.py
"""

from typing import Any, Dict

from core.logging import get_module_logger
from .connection import HardcoverClient

_LOGGER = get_module_logger("Integration.Hardcover.Likes")

_UPSERT_LIKE_MUTATION = """
mutation UpsertLike($likeable_id: Int!, $likeable_type: String!) {
  upsert_like(likeable_id: $likeable_id, likeable_type: $likeable_type) {
    id
  }
}
"""

_DELETE_LIKE_MUTATION = """
mutation DeleteLike($likeable_id: Int!, $likeable_type: String!) {
  delete_like(likeable_id: $likeable_id, likeable_type: $likeable_type) {
    __typename
  }
}
"""

_HAS_LIKED_QUERY = """
query HasLiked($likeable_id: Int!, $likeable_type: String!, $user_id: Int!) {
  likes(where: { likeable_id: { _eq: $likeable_id }, likeable_type: { _eq: $likeable_type }, user_id: { _eq: $user_id } }, limit: 1) {
    id
  }
}
"""


def has_liked(client: HardcoverClient, likeable_id: int, likeable_type: str, hardcover_user_id: int) -> bool:
    data = client.query(
        _HAS_LIKED_QUERY,
        {"likeable_id": likeable_id, "likeable_type": likeable_type, "user_id": hardcover_user_id},
    )
    return bool(data.get("likes"))


def like_entity(client: HardcoverClient, likeable_id: int, likeable_type: str = "UserBook") -> Dict[str, Any]:
    data = client.query(_UPSERT_LIKE_MUTATION, {"likeable_id": likeable_id, "likeable_type": likeable_type})
    return data.get("upsert_like") or {}


def unlike_entity(client: HardcoverClient, likeable_id: int, likeable_type: str = "UserBook") -> bool:
    data = client.query(_DELETE_LIKE_MUTATION, {"likeable_id": likeable_id, "likeable_type": likeable_type})
    return data.get("delete_like") is not None
