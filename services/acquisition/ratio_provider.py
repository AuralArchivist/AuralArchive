"""
Module Name: ratio_provider.py
Author: AuralArchivist
Created: July 6, 2026
Last Modified: October 9, 2026
Description:
    Reads private tracker ratio stats and estimates the ratio impact of a grab.

Location:
    /services/acquisition/ratio_provider.py
"""

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from core.logging import get_module_logger

_LOGGER = get_module_logger("Service.Downloads.RatioProvider")


@dataclass
class RatioSnapshot:
    """A tracker account's ratio-relevant stats at a point in time."""
    ratio: float
    uploaded_bytes: int
    downloaded_bytes: int
    class_name: Optional[str] = None


def _mam_ratio_snapshot(indexer_config: Dict[str, Any]) -> Optional[RatioSnapshot]:
    """Reuse the Profile page's already-fetched MAM account stats."""
    try:
        from services.sync.accounts import mam_account
        from services.container import container

        config_service = container.get_config_service()
        cookie = mam_account.get_linked_cookie(config_service)
        if not cookie:
            return None

        stats = mam_account.get_cached_stats(cookie)
        if not stats:
            return None

        try:
            ratio = float(stats.get('ratio') or 0)
        except (TypeError, ValueError):
            ratio = 0.0

        uploaded_bytes = int(stats.get('uploaded_bytes') or 0)
        downloaded_bytes = int(stats.get('downloaded_bytes') or 0)

        return RatioSnapshot(
            ratio=ratio,
            uploaded_bytes=uploaded_bytes,
            downloaded_bytes=downloaded_bytes,
            class_name=stats.get('class_name'),
        )
    except Exception as exc:
        _LOGGER.debug("Unable to fetch MAM ratio snapshot: %s", exc)
        return None


_RATIO_PROVIDERS: Dict[str, Callable[[Dict[str, Any]], Optional[RatioSnapshot]]] = {
    'myanonamouse': _mam_ratio_snapshot,
}


def get_ratio_snapshot_for_indexer(indexer_config: Dict[str, Any]) -> Optional[RatioSnapshot]:
    """Return a ratio snapshot for this indexer, or None if it isn't a
    ratio-tracked tracker (or no snapshot is currently available)."""
    provider_key = (indexer_config.get('provider_key') or indexer_config.get('provider') or '').lower()
    provider_fn = _RATIO_PROVIDERS.get(provider_key)
    if not provider_fn:
        return None
    return provider_fn(indexer_config)


def estimate_ratio_after_grab(snapshot: RatioSnapshot, additional_download_bytes: int) -> float:
    """Estimate the account's ratio if this candidate is downloaded, assuming
    no seeding credit yet (the conservative/worst-case estimate)."""
    new_downloaded = snapshot.downloaded_bytes + max(0, additional_download_bytes or 0)
    if new_downloaded <= 0:
        return snapshot.ratio
    return snapshot.uploaded_bytes / new_downloaded
