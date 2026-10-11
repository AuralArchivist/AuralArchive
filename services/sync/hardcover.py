"""
Module Name: hardcover.py
Author: AuralArchivist
Created: July 1, 2026
Last Modified: October 9, 2026
Description:
    Hardcover sync: refreshes the discovery caches and pushes shelf changes
    (the Hardcover background task).

Location:
    /services/sync/hardcover.py
"""

import json
import time
from typing import Any, Dict, Optional

from sqlalchemy import text

from core.logging import get_module_logger
from core.database.repositories import hardcover_cache

from integrations.hardcover.connection import get_client, HardcoverAPIError, HardcoverAuthError
from integrations.hardcover.new_releases import fetch_new_releases
from integrations.hardcover.shelves import push_shelf_update, set_edition_owned, find_user_book
from integrations.hardcover.trending import WINDOWS, fetch_trending

TASK_NAME = 'hardcover'
TICK_SECONDS = 60


def map_local_status_to_hardcover(library_status: str, monitored: bool) -> Dict[str, Any]:
    """Map a local books row's status to the Hardcover fields to push.

    - monitored=false always pushes status_id=6 (Ignored), regardless of
      library_status, since that's the closest Hardcover-side equivalent.
    - 'owned' maps to owned=True. It intentionally does NOT force a reading
      status_id - owning a copy says nothing about whether it's been read.
    - 'wanted' maps to status_id=1 (Want to Read) and owned=False.
    - 'missing' clears owned, leaving status_id untouched (Hardcover doesn't
      have a "missing" concept; the book may still be legitimately shelved
      there as read/want-to-read even though the local file is gone).
    """
    if not monitored:
        return {"status_id": 6}
    if library_status == "owned":
        return {"owned": True}
    if library_status == "wanted":
        return {"status_id": 1, "owned": False}
    if library_status == "missing":
        return {"owned": False}
    return {}


class HardcoverService:
    """Background service for Hardcover cache refresh + push-queue draining."""

    def __init__(
        self,
        config_service,
        db_service,
        *,
        cache_refresh_minutes: Optional[int] = None,
        logger=None,
    ):
        self.logger = logger or get_module_logger("Integration.Hardcover.Service")
        self.config_service = config_service
        self.db_service = db_service
        self._cache_refresh_minutes_override = cache_refresh_minutes

        self._last_cache_refresh: float = 0.0

        self.logger.success("Hardcover service initialized")


    def is_connected(self) -> bool:
        return self.config_service.is_hardcover_connected()

    def _cache_refresh_seconds(self) -> int:
        minutes = self._cache_refresh_minutes_override or self.config_service.get_hardcover_cache_refresh_minutes()
        return max(minutes, 5) * 60


    def tick(self) -> None:
        """Push queued shelf changes, then refresh the discovery caches when due."""
        self.drain_push_queue()
        if time.monotonic() - self._last_cache_refresh >= self._cache_refresh_seconds():
            self.refresh_all_caches()


    def refresh_all_caches(self) -> None:
        try:
            client = get_client(self.config_service)
        except HardcoverAuthError:
            self.logger.warning("Skipping Hardcover cache refresh: not connected")
            return

        engine = self.db_service.connection_manager.get_engine()

        for window in WINDOWS:
            try:
                entries = fetch_trending(client, window)
                with engine.begin() as conn:
                    hardcover_cache.replace_trending_cache(conn, window, entries)
            except HardcoverAPIError as exc:
                self.logger.warning("Trending refresh failed for window '%s': %s", window, exc)

        try:
            releases = fetch_new_releases(client)
            with engine.begin() as conn:
                hardcover_cache.replace_new_releases_cache(conn, releases)
        except HardcoverAPIError as exc:
            self.logger.warning("New releases refresh failed: %s", exc)

        self._last_cache_refresh = time.monotonic()
        self.logger.info("Hardcover discovery caches refreshed")


    def enqueue_status_push(self, book_id: int) -> bool:
        """Queue a shelf-status/owned push for a local book, if it's linked and enabled.

        Runs from the BOOK_STATUS_CHANGED event, after the status change is
        committed (services/event_subscribers.py). No-ops quietly if the integration isn't
        connected, the book has no confirmed Hardcover match, or auto-push is
        disabled for the relevant field(s) - this is intentionally best-effort,
        never raising, since it must never block the local status change itself.
        """
        if not self.is_connected():
            return False

        engine = self.db_service.connection_manager.get_engine()
        with engine.connect() as conn:
            link = hardcover_cache.get_link_for_book(conn, book_id)
            if not link:
                return False

            row = conn.execute(
                text("SELECT library_status, monitored FROM books WHERE id = :id"),
                {"id": book_id},
            ).mappings().first()
        if not row:
            return False

        fields = map_local_status_to_hardcover(row["library_status"], bool(row["monitored"]))
        if not fields:
            return False

        if "status_id" in fields and not self.config_service.get_hardcover_auto_push_status():
            fields.pop("status_id", None)
        if "owned" in fields and not self.config_service.get_hardcover_auto_push_owned():
            fields.pop("owned", None)
        if not fields:
            return False

        with engine.begin() as conn:
            hardcover_cache.enqueue_push(conn, book_id, "shelf_sync", fields)
        return True

    def drain_push_queue(self, limit: int = 25) -> None:
        try:
            client = get_client(self.config_service)
        except HardcoverAuthError:
            return

        account = self.config_service.get_hardcover_config() or {}
        hardcover_user_id = account.get("hardcover_user_id")
        if not hardcover_user_id:
            return

        engine = self.db_service.connection_manager.get_engine()
        with engine.connect() as conn:
            pending = hardcover_cache.get_pending_pushes(conn, limit=limit)

        for push in pending:
            link = None
            with engine.connect() as conn:
                link = hardcover_cache.get_link_for_book(conn, push["book_id"])
            if not link:
                with engine.begin() as conn:
                    hardcover_cache.mark_push_failed(conn, push["id"], "No confirmed Hardcover match for this book")
                continue

            try:
                payload = json.loads(push["payload"] or "{}")
                status_id = payload.get("status_id")
                if status_id is not None:
                    push_shelf_update(
                        client,
                        int(hardcover_user_id),
                        link["hardcover_book_id"],
                        status_id=status_id,
                    )

                desired_owned = payload.get("owned")
                if desired_owned is not None and link.get("hardcover_edition_id"):
                    existing = find_user_book(client, int(hardcover_user_id), link["hardcover_book_id"])
                    current_owned = bool((existing or {}).get("owned"))
                    if current_owned != bool(desired_owned):
                        set_edition_owned(client, link["hardcover_edition_id"])
            except HardcoverAPIError as exc:
                self.logger.warning("Hardcover push failed for book %s: %s", push["book_id"], exc)
                with engine.begin() as conn:
                    hardcover_cache.mark_push_failed(conn, push["id"], str(exc))
                continue

            with engine.begin() as conn:
                hardcover_cache.mark_push_sent(conn, push["id"])
