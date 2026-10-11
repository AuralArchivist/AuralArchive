"""
Module Name: hardcover_account.py
Author: AuralArchivist
Created: July 1, 2026
Last Modified: October 9, 2026
Description:
    Hardcover.app account connect/disconnect + lightweight stats, mirroring
    services/sync/accounts/mam_account.py's shape for the Connected Services
    panel in Settings. Uses an API token (from
    https://hardcover.app/account/api) instead of a session cookie.

Location:
    /services/sync/accounts/hardcover_account.py
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional, Tuple

from core.logging import get_module_logger
from integrations.hardcover.connection import HardcoverClient, HardcoverAPIError, HardcoverAuthError

_LOGGER = get_module_logger("Services.UserProfile.HardcoverAccount")

_STATS_CACHE_TTL = 300
_stats_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}

_STATS_QUERY = """
query MyStats($user_id: Int!) {
  user_books_aggregate(where: { user_id: { _eq: $user_id } }) {
    aggregate {
      count
      avg { rating }
    }
  }
  want: user_books_aggregate(where: { user_id: { _eq: $user_id }, status_id: { _eq: 1 } }) { aggregate { count } }
  reading: user_books_aggregate(where: { user_id: { _eq: $user_id }, status_id: { _eq: 2 } }) { aggregate { count } }
  read: user_books_aggregate(where: { user_id: { _eq: $user_id }, status_id: { _eq: 3 } }) { aggregate { count } }
  owned: user_books_aggregate(where: { user_id: { _eq: $user_id }, owned: { _eq: true } }) { aggregate { count } }
}
"""


def validate_token(token: str) -> Tuple[bool, Dict[str, Any]]:
    """Validate a Hardcover API token via `me { id username }`.

    Returns (True, {"id": int, "username": str}) on success,
    (False, {"error": str}) on failure.
    """
    token = (token or "").strip()
    if not token:
        return False, {"error": "Token is empty"}

    try:
        client = HardcoverClient(token)
        account = client.whoami()
    except HardcoverAuthError:
        return False, {"error": "Hardcover rejected this token - please check it and try again"}
    except HardcoverAPIError as exc:
        _LOGGER.warning("Hardcover token validation failed: %s", exc)
        return False, {"error": str(exc)}

    return True, {"id": account.get("id"), "username": account.get("username")}


def connect_account(token: str, config_service) -> Tuple[bool, Dict[str, Any]]:
    """Validate a token and persist it (+ user id/username) via ConfigService."""
    ok, result = validate_token(token)
    if not ok:
        return False, result

    config_service.update_hardcover_config({
        "token": token.strip(),
        "hardcover_user_id": result["id"],
        "hardcover_username": result["username"],
    })
    _LOGGER.info("Hardcover account connected for user '%s'", result["username"])
    return True, result


def disconnect_account(config_service) -> bool:
    """Remove the stored Hardcover token and settings."""
    success = config_service.disconnect_hardcover()
    if success:
        _LOGGER.info("Hardcover account disconnected")
    return success


def get_linked_account(config_service) -> Optional[Dict[str, Any]]:
    """Return {'user_id', 'username'} if a token is stored, else None."""
    hc_config = config_service.get_hardcover_config()
    if not hc_config or not hc_config.get("token"):
        return None
    return {
        "user_id": hc_config.get("hardcover_user_id"),
        "username": hc_config.get("hardcover_username"),
    }


def get_cached_stats(config_service) -> Optional[Dict[str, Any]]:
    """Return cached account stats (shelf counts, avg rating) if still fresh, else fetch live."""
    account = get_linked_account(config_service)
    if not account or not account.get("user_id"):
        return None

    cache_key = str(account["user_id"])
    cached = _stats_cache.get(cache_key)
    if cached:
        ts, data = cached
        if time.monotonic() - ts < _STATS_CACHE_TTL:
            return data

    hc_config = config_service.get_hardcover_config()
    token = (hc_config or {}).get("token", "")
    if not token:
        return None

    try:
        client = HardcoverClient(token)
        data = client.query(_STATS_QUERY, {"user_id": int(account["user_id"])})
    except HardcoverAPIError as exc:
        _LOGGER.warning("Failed to fetch Hardcover stats: %s", exc)
        return None

    def _count(key: str) -> int:
        return int(((data.get(key) or {}).get("aggregate") or {}).get("count") or 0)

    total_agg = (data.get("user_books_aggregate") or {}).get("aggregate") or {}
    stats = {
        "total_books": int(total_agg.get("count") or 0),
        "avg_rating": (total_agg.get("avg") or {}).get("rating"),
        "want_to_read": _count("want"),
        "currently_reading": _count("reading"),
        "read": _count("read"),
        "owned": _count("owned"),
    }
    _stats_cache[cache_key] = (time.monotonic(), stats)
    return stats


def invalidate_cache(user_id: Any) -> None:
    _stats_cache.pop(str(user_id), None)
