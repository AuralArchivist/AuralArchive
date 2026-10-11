"""
Module Name: scoring.py
Author: AuralArchivist
Created: October 9, 2026
Last Modified: October 9, 2026
Description:
    Picks the best search result for a download, applying the match and
    quality thresholds and the ratio, freeleech and grab-limit rules.

Location:
    /services/acquisition/scoring.py
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text

from .ratio_provider import estimate_ratio_after_grab, get_ratio_snapshot_for_indexer
from services.container import container


class ScoringMixin:
    """Result ranking and the automatic search rules. Mixed into DownloadManagementService."""

    def _select_best_candidate(
        self,
        results: List[Dict[str, Any]],
        min_match_score: float,
        min_quality_score: float,
        queued_at: Optional[str],
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """Rank and filter search results, applying the match/quality
        thresholds plus the optional ratio/freeleech/duplicate-grab automatic
        search rules. Falls through to the next-best candidate when one is
        disqualified by a rule below (duplicate grab, ratio floor, freeleech
        hold) instead of failing the whole search outright - only match
        confidence, which is monotonic with the sort order, short-circuits
        the scan early.

        Returns (winning_result, None) or (None, failure_reason).
        """
        def _unwrap(entry: Dict[str, Any]) -> Dict[str, Any]:
            if not isinstance(entry, dict):
                return {}
            nested = entry.get('selected_result')
            if isinstance(nested, dict) and nested:
                combined = nested.copy()
                if 'confidence_score' not in combined and 'confidence_score' in entry:
                    combined['confidence_score'] = entry.get('confidence_score')
                if 'indexer' not in combined and 'indexer' in entry:
                    combined['indexer'] = entry.get('indexer')
                return combined
            return entry

        def _confidence(entry: Dict[str, Any]) -> float:
            payload = _unwrap(entry)
            score = payload.get('confidence_score')
            if score is None:
                score = entry.get('confidence_score', 0)
            return score or 0

        def _release_quality(entry: Dict[str, Any]) -> float:
            payload = _unwrap(entry)
            score = payload.get('release_quality_score')
            if score is None:
                score = entry.get('release_quality_score', 0)
            return score or 0

        def _is_freeleech(payload: Dict[str, Any]) -> bool:
            if payload.get('mam_fl_vip') or payload.get('mam_free') or payload.get('mam_vip'):
                return True
            freeleech_value = str(payload.get('freeleech') or '').strip().lower()
            return freeleech_value in ('fl', 'vip', 'true', '1')

        def _is_duplicate_grab(payload: Dict[str, Any]) -> bool:
            if payload.get('already_snatched'):
                return True
            return bool(payload.get('mam_my_snatched'))

        def _sort_key(entry: Dict[str, Any]):
            payload = _unwrap(entry)
            confidence = _confidence(entry)
            if self.prefer_freeleech:
                return (1 if _is_freeleech(payload) else 0, confidence)
            return (confidence,)

        ranked = sorted(results, key=_sort_key, reverse=True)
        hold_expired = self._freeleech_hold_expired(queued_at)
        held_count = 0

        for entry in ranked:
            payload = _unwrap(entry)
            confidence = _confidence(entry)
            quality = _release_quality(entry)

            if confidence < min_match_score:
                continue

            if quality < min_quality_score:
                continue

            if not payload.get('download_url'):
                continue

            if self.duplicate_grab_protection and _is_duplicate_grab(payload):
                continue

            indexer_name = payload.get('indexer') or entry.get('indexer')
            if self.ratio_protection_enabled and indexer_name:
                if not self._passes_ratio_floor(indexer_name, payload):
                    continue

            if indexer_name and not self._passes_tracker_grab_limits(indexer_name):
                continue

            if self.hold_non_freeleech and not _is_freeleech(payload) and not hold_expired:
                held_count += 1
                continue

            return payload, None

        if held_count:
            return None, (
                f"{held_count} eligible non-freeleech result(s) held pending freeleech "
                f"(retries until {self.freeleech_hold_days}-day hold expires)"
            )

        return None, "No eligible search results after applying match/quality/ratio/duplicate-grab rules"

    def _freeleech_hold_expired(self, queued_at: Optional[str]) -> bool:
        """True once freeleech_hold_days have elapsed since the book was
        first queued (or immediately if the hold is effectively disabled)."""
        if self.freeleech_hold_days <= 0 or not queued_at:
            return True
        try:
            queued_dt = datetime.fromisoformat(queued_at)
        except (TypeError, ValueError):
            return True
        elapsed_days = (datetime.now() - queued_dt).days
        return elapsed_days >= self.freeleech_hold_days

    def _passes_ratio_floor(self, indexer_name: str, payload: Dict[str, Any]) -> bool:
        """True if grabbing this candidate wouldn't drop the indexer's
        account ratio below the configured floor. Fails open (allows the
        candidate) for indexers with no ratio-tracking provider registered,
        or if the account snapshot can't be fetched right now."""
        try:

            indexer_config = container.get_indexer_manager().indexer_configs.get(indexer_name)
            if not indexer_config:
                return True

            snapshot = get_ratio_snapshot_for_indexer(indexer_config)
            if snapshot is None:
                return True

            size_bytes = payload.get('size_bytes') or 0
            projected_ratio = estimate_ratio_after_grab(snapshot, size_bytes)

            if projected_ratio < self.ratio_floor:
                self.logger.info(
                    "Rejecting candidate for ratio protection",
                    extra={
                        "indexer": indexer_name,
                        "projected_ratio": round(projected_ratio, 3),
                        "ratio_floor": self.ratio_floor,
                        "size_bytes": size_bytes,
                    },
                )
                return False
            return True

        except Exception as exc:
            self.logger.warning("Ratio floor check failed; allowing candidate: %s", exc, exc_info=True)
            return True

    def _passes_tracker_grab_limits(self, indexer_name: str) -> bool:
        """True if grabbing from this indexer now wouldn't exceed its own
        configured max_grabs_per_hour/max_grabs_per_day (set per-indexer
        alongside rate_limit; 0 = unlimited). Fails open if unconfigured or
        the count query fails."""
        try:

            indexer_config = container.get_indexer_manager().indexer_configs.get(indexer_name)
            if not indexer_config:
                return True

            rate_limit = indexer_config.get('rate_limit') or {}
            max_per_hour = int(rate_limit.get('max_grabs_per_hour') or 0)
            max_per_day = int(rate_limit.get('max_grabs_per_day') or 0)

            if max_per_hour <= 0 and max_per_day <= 0:
                return True

            now = datetime.now()

            if max_per_hour > 0:
                count = self._count_recent_grabs(indexer_name, now - timedelta(hours=1))
                if count >= max_per_hour:
                    self.logger.info(
                        "Rejecting candidate: hourly grab limit reached",
                        extra={"indexer": indexer_name, "count": count, "limit": max_per_hour},
                    )
                    return False

            if max_per_day > 0:
                count = self._count_recent_grabs(indexer_name, now - timedelta(days=1))
                if count >= max_per_day:
                    self.logger.info(
                        "Rejecting candidate: daily grab limit reached",
                        extra={"indexer": indexer_name, "count": count, "limit": max_per_day},
                    )
                    return False

            return True

        except Exception as exc:
            self.logger.warning("Tracker grab-limit check failed; allowing candidate: %s", exc, exc_info=True)
            return True

    def _count_recent_grabs(self, indexer_name: str, window_start: datetime) -> int:
        """Live count of download_queue rows assigned to this indexer since
        window_start - no maintained counter to drift or need resetting."""
        try:
            engine = container.get_database_service().connection_manager.get_engine()
            with engine.connect() as conn:
                row = conn.execute(
                    text("""
                        SELECT COUNT(*) FROM download_queue
                        WHERE indexer = :indexer AND queued_at >= :window_start
                    """),
                    {"indexer": indexer_name, "window_start": window_start.isoformat()},
                ).fetchone()
            return int(row[0]) if row else 0
        except Exception as exc:
            self.logger.warning("Unable to count recent grabs for %s; grab limit not enforced: %s", indexer_name, exc, exc_info=True)
            return 0
