"""
Module Name: mam_account.py
Author: AuralArchivist
Created: June 8, 2026
Last Modified: October 9, 2026
Description:
    MAM (MyAnonamouse) account management service.
    Validates session cookies, fetches account stats, and provisions
    the MAM direct indexer entry automatically on connect.

Location:
    /services/sync/accounts/mam_account.py
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional, Tuple

import requests

from core.logging import get_module_logger
from core.convert import to_int
from core.http import HttpSession

_LOGGER = get_module_logger("Services.UserProfile.MAMAccount")

_MAM_BASE = "https://www.myanonamouse.net"
_JSONLOAD_PATH = "/jsonLoad.php"
_REQUEST_TIMEOUT = 10
_STATS_CACHE_TTL = 300

_stats_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}


def _make_session(cookie_value: str) -> HttpSession:
    """Session carrying the MAM session cookie."""
    session = HttpSession(headers={
        "User-Agent": "AuralArchive/1.0 (+https://github.com/auralarchive)",
        "Accept": "application/json",
    })
    session.cookies.set("mam_id", cookie_value, domain="www.myanonamouse.net")
    return session


def validate_cookie(cookie_value: str) -> Tuple[bool, Dict[str, Any]]:
    """
    Validate a MAM session cookie by calling /jsonLoad.php.

    Returns (True, account_dict) on success, (False, {error: str}) on failure.
    account_dict keys: uid, username, class_name, downloaded, uploaded,
                       ratio, bonus_points, wedge_count
    """
    cookie_value = (cookie_value or "").strip()
    if not cookie_value:
        return False, {"error": "Cookie value is empty"}

    try:
        session = _make_session(cookie_value)
        url = f"{_MAM_BASE}{_JSONLOAD_PATH}"
        resp = session.get(
            url + "?snatch_summary&clientStats",
            timeout=_REQUEST_TIMEOUT,
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        _LOGGER.warning("MAM /jsonLoad.php request failed: %s", exc)
        return False, {"error": f"Network error: {exc}"}

    if resp.status_code in (301, 302, 303):
        _LOGGER.warning("MAM cookie invalid - received redirect (likely login page)")
        return False, {"error": "Session cookie rejected - please try again"}

    if resp.status_code != 200:
        _LOGGER.warning("MAM /jsonLoad.php returned HTTP %s", resp.status_code)
        return False, {"error": f"MAM returned HTTP {resp.status_code}"}

    try:
        data = resp.json()
    except ValueError:
        _LOGGER.warning("MAM /jsonLoad.php returned non-JSON body")
        return False, {"error": "Invalid response from MAM - not JSON"}

    uid = data.get("uid")
    username = data.get("username")
    if not uid or not username:
        _LOGGER.warning("MAM /jsonLoad.php missing uid/username in response: %s", list(data.keys()))
        return False, {"error": "Session cookie rejected - please try again"}

    _LOGGER.debug("MAM /jsonLoad.php raw keys: %s", list(data.keys()))
    _LOGGER.debug("MAM /jsonLoad.php raw data: %s", {k: v for k, v in data.items() if k not in ("csrfToken",)})

    account = _parse_account_data(data)
    _LOGGER.info("MAM cookie validated for user '%s' (uid=%s)", username, uid)
    return True, account


def _parse_account_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """Parse raw /jsonLoad.php JSON into a clean account dict.

    snatch_summary and clientStats arrive as nested objects. Account totals
    are read from the root because the clientStats copy is cached 30 minutes.
    """
    downloaded: str = str(data.get("downloaded") or "")
    uploaded: str   = str(data.get("uploaded") or "")

    ratio_raw = data.get("ratio")
    if isinstance(ratio_raw, (int, float)):
        ratio: Optional[str] = "{:.2f}".format(float(ratio_raw))
    elif ratio_raw is not None:
        ratio = str(ratio_raw)
    else:
        ratio = None

    bonus = (
        data.get("seedbonus")
        or data.get("bonusPoints")
        or data.get("bonus_points")
        or data.get("fl_tokens")
        or 0
    )
    wedges = (
        data.get("wedges")
        or data.get("fl_silver")
        or data.get("wedge_count")
        or 0
    )

    summary = data.get("snatch_summary")
    if not isinstance(summary, dict):
        summary = {}

    def _cat(key: str) -> int:
        entry = summary.get(key)
        return to_int(entry.get("count")) if isinstance(entry, dict) else 0

    _SNATCH_CATS = (
        "inactSat", "sSat", "seedUnsat", "leeching",
        "inactHnr", "inactUnsat", "seedHnr", "upAct", "upInact",
    )
    snatch_count: Optional[int] = sum(_cat(c) for c in _SNATCH_CATS) if summary else None
    seeding_count: Optional[int] = (_cat("sSat") + _cat("seedUnsat") + _cat("seedHnr")) if summary else None
    leeching_count: Optional[int] = _cat("leeching") if summary else None
    hnr_count: int = _cat("inactHnr")

    unsat_obj = summary.get("unsat")
    unsat_count: Optional[int] = None
    unsat_limit: Optional[int] = None
    if isinstance(unsat_obj, dict):
        unsat_count = to_int(unsat_obj.get("count"))
        unsat_limit = to_int(unsat_obj.get("limit")) if unsat_obj.get("limit") is not None else None

    clients = _client_entries(data.get("clientStats"))

    # Live client checks first; the snatch summary flag is the fallback
    connectable: Optional[bool] = None
    if clients:
        connectable = any(_is_yes(c.get("connectable")) for c in clients)
    elif summary.get("connectable") is not None:
        connectable = _is_yes(summary.get("connectable"))

    active_torrent_count: Optional[int] = None
    if clients:
        active_torrent_count = sum(to_int(c.get("count")) for c in clients)
    elif summary:
        active_torrent_count = (seeding_count or 0) + (leeching_count or 0)

    member_since: Optional[str] = None
    created_ts = summary.get("created") or data.get("created")
    if created_ts:
        try:
            import datetime as _dt
            member_since = _dt.datetime.fromtimestamp(
                int(created_ts), tz=_dt.timezone.utc
            ).strftime("%B %d, %Y")
        except (TypeError, ValueError, OverflowError, OSError):
            member_since = None

    return {
        "uid": str(data.get("uid", "")),
        "username": data.get("username", ""),
        "class_name": data.get("classname") or data.get("class") or "",
        "downloaded": downloaded,
        "uploaded": uploaded,
        "downloaded_bytes": data.get("downloaded_bytes"),
        "uploaded_bytes": data.get("uploaded_bytes"),
        "ratio": ratio,
        "bonus_points": to_int(bonus),
        "wedge_count": to_int(wedges),
        "snatch_count": snatch_count,
        "seeding_count": seeding_count,
        "leeching_count": leeching_count,
        "hnr_count": hnr_count,
        "unsat_count": unsat_count,
        "unsat_limit": unsat_limit,
        "connectable": connectable,
        "active_torrent_count": active_torrent_count,
        "member_since": member_since,
        "country_code": data.get("country_code"),
        "country_name": data.get("country_name"),
    }


def _client_entries(block: Any) -> list:
    """Return the client list from the clientStats block (nested one level)."""
    if isinstance(block, dict):
        block = block.get("clientStats")
    if not isinstance(block, list):
        return []
    return [c for c in block if isinstance(c, dict)]


def _is_yes(value: Any) -> bool:
    return str(value).strip().lower() in ("yes", "true", "1")


def get_cached_stats(cookie_value: str) -> Optional[Dict[str, Any]]:
    """
    Return cached MAM stats if still fresh, else fetch live.
    Returns None on fetch failure.
    """
    if not cookie_value:
        return None

    cache_key = cookie_value[:16]
    cached = _stats_cache.get(cache_key)
    if cached:
        ts, data = cached
        if time.monotonic() - ts < _STATS_CACHE_TTL:
            return data

    ok, result = validate_cookie(cookie_value)
    if not ok:
        return None
    _stats_cache[cache_key] = (time.monotonic(), result)
    return result


def invalidate_cache(cookie_value: str) -> None:
    """Evict cached stats for the given cookie."""
    if cookie_value:
        _stats_cache.pop(cookie_value[:16], None)


def buy_wedges(cookie_value: str) -> Tuple[bool, Dict[str, Any]]:
    """
    Spend bonus points on a freeleech wedge via POST /json/bonusBuy.php.

    Only call this in response to explicit user action, never automatically.
    MAM can answer HTTP 200 with {"success": false, "error": ...}, so the body
    is checked too.

    Returns (True, {}) on success, (False, {"error": str}) on failure.
    """
    cookie_value = (cookie_value or "").strip()
    if not cookie_value:
        return False, {"error": "Cookie value is empty"}

    try:
        session = _make_session(cookie_value)
        resp = session.post(
            f"{_MAM_BASE}/json/bonusBuy.php",
            data={"spendtype": "wedges"},
            timeout=_REQUEST_TIMEOUT,
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        _LOGGER.warning("MAM bonusBuy.php request failed: %s", exc)
        return False, {"error": f"Network error: {exc}"}

    if resp.status_code != 200:
        _LOGGER.warning("MAM bonusBuy.php returned HTTP %s", resp.status_code)
        return False, {"error": f"MAM returned HTTP {resp.status_code}"}

    try:
        body = resp.json()
    except ValueError:
        body = None
    if isinstance(body, dict) and (body.get("success") is False or body.get("error")):
        message = str(body.get("error") or "MAM refused the purchase")
        _LOGGER.warning("MAM refused the wedge purchase: %s", message)
        return False, {"error": message}

    _LOGGER.info("Spent bonus points on a MAM freeleech wedge")
    invalidate_cache(cookie_value)
    return True, {}


def provision_indexer(cookie_value: str, account: Dict[str, Any], config_service) -> bool:
    """
    Create or update the MAM direct indexer entry in config using the validated
    cookie. Uses sensible defaults: searchType=active, categories=[3030].

    Returns True on success.
    """
    indexer_key = "myanonamouse"
    existing = config_service.get_indexer_config(indexer_key) or {}

    indexer_data = {
        "name": existing.get("name") or "MyAnonamouse",
        "type": "direct",
        "protocol": "direct",
        "base_url": _MAM_BASE,
        "session_id": cookie_value,
        "categories": ["3030"],
        "private": True,
        "enabled": True,
        "priority": existing.get("priority") or 25,
        "search_type": existing.get("search_type") or "active",
        "min_seeders": existing.get("min_seeders") or 0,
        "preferred_formats": existing.get("preferred_formats") or [],
        "languages": existing.get("languages") or [],
        "provider_key": "myanonamouse",
        "_provisioned_by": "profile",
        "_mam_username": account.get("username", ""),
    }

    success = config_service.set_indexer_config(indexer_key, indexer_data)
    if success:
        _LOGGER.info("MAM indexer provisioned/updated for user '%s'", account.get("username"))
    else:
        _LOGGER.error("Failed to provision MAM indexer for user '%s'", account.get("username"))
    return success


def deprovision_indexer(config_service) -> bool:
    """Remove the MAM indexer entry from config."""
    success = config_service.delete_indexer_config("myanonamouse")
    if success:
        _LOGGER.info("MAM indexer entry removed")
    return success


def get_linked_cookie(config_service) -> Optional[str]:
    """Return the stored MAM session cookie from the indexer config, or None."""
    indexer = config_service.get_indexer_config("myanonamouse")
    if not indexer:
        return None
    return indexer.get("session_id") or None
