"""
Module Name: automatic_download_service.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Coordinate automatic download scheduling for books marked as Wanted, respecting configuration and queue limits.

Location:
    /services/acquisition/automatic_download_service.py
"""

from __future__ import annotations

import re
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

from core.logging import get_module_logger
from core.convert import to_bool, to_int
from core.scheduler import get_scheduler
from services.container import container



TASK_NAME = 'automatic_downloads'


class AutomaticDownloadService:
    """Singleton service that orchestrates automatic download scheduling."""

    def __init__(self, *, logger=None):
        self.logger = logger or get_module_logger("Service.Automation.AutomaticDownload")

        self._state_lock = threading.Lock()

        self.paused = False
        self.last_run: Optional[datetime] = None
        self.last_error: Optional[str] = None

        self._queue_snapshot: List[Dict[str, Any]] = []
        self._skip_book_ids: List[int] = []
        self._round_robin_cursor: int = 0

        self.config: Dict[str, Any] = {
            "auto_download_enabled": False,
            "scan_interval_seconds": 60,
            "max_batch_size": 2,
            "schedule_window_enabled": False,
            "schedule_start_hour": 0,
            "schedule_end_hour": 24,
        }

        self.metrics: Dict[str, Any] = {
            "total_books_queued": 0,
            "last_book_queued": None,
            "last_queue_time": None,
        }

        self.reload_configuration()

    # --- Lazy-loaded service dependencies ---

    # --- Lifecycle management ---

    @property
    def running(self) -> bool:
        """Whether scheduled automatic searches are active."""
        return get_scheduler().is_active(TASK_NAME)

    def start(self) -> bool:
        with self._state_lock:
            self.paused = False
        get_scheduler().resume(TASK_NAME)
        self.logger.success(
            "Automatic download service started successfully",
            extra={
                "scan_interval_seconds": self.config.get("scan_interval_seconds"),
                "max_batch_size": self.config.get("max_batch_size"),
            },
        )
        return True

    def stop(self) -> bool:
        get_scheduler().pause(TASK_NAME)
        self.logger.info("Automatic download service stopped")
        return True

    def pause(self) -> bool:
        with self._state_lock:
            if self.paused:
                return True
            self.paused = True
            self.logger.info(
                "Automatic download service paused",
                extra={"paused": True},
            )
        return True

    def resume(self) -> bool:
        with self._state_lock:
            if not self.paused:
                return True
            self.paused = False
            self.logger.info(
                "Automatic download service resumed",
                extra={"paused": False},
            )
        return True

    # --- Configuration helpers ---

    def reload_configuration(self):
        """Read the auto_search settings (also called when they're saved)."""
        try:
            config_service = container.get_config_service()
            auto_section = config_service.get_section("auto_search") or {}

            self.config["auto_download_enabled"] = to_bool(
                auto_section.get("auto_download_enabled"), False
            )
            self.config["scan_interval_seconds"] = max(
                30, to_int(auto_section.get("scan_interval_seconds"), 120)
            )
            self.config["max_batch_size"] = max(
                1, to_int(auto_section.get("max_batch_size"), 2)
            )

            self.config["schedule_window_enabled"] = to_bool(
                auto_section.get("schedule_window_enabled"), False
            )
            self.config["schedule_start_hour"] = min(23, max(
                0, to_int(auto_section.get("schedule_start_hour"), 0)
            ))
            self.config["schedule_end_hour"] = min(24, max(
                0, to_int(auto_section.get("schedule_end_hour"), 24)
            ))

            raw_skip_list = (auto_section.get("skip_book_ids") or "").strip()
            skip_ids: List[int] = []
            if raw_skip_list:
                for token in raw_skip_list.split(','):
                    token_clean = token.strip()
                    if not token_clean:
                        continue
                    try:
                        skip_ids.append(int(token_clean))
                    except ValueError:
                        self.logger.debug(
                            "Skipping invalid skip_book_id token",
                            extra={"token": token_clean},
                        )
            self._skip_book_ids = skip_ids

            self._round_robin_cursor = to_int(
                auto_section.get("round_robin_cursor"), self._round_robin_cursor
            )

        except Exception as exc:
            self.logger.error(
                "Failed to load automatic search configuration",
                extra={"error": str(exc)}, exc_info=True,
            )
            self.last_error = str(exc)

    def update_configuration(self, updates: Dict[str, Any]) -> bool:
        config_service = container.get_config_service()
        payload = {}
        for key, value in updates.items():
            payload[f"auto_search.{key}"] = value
        if payload:
            return config_service.update_multiple_config(payload)
        return True

    def _persist_skip_book_ids(self):
        skip_value = ",".join(str(book_id) for book_id in sorted(set(self._skip_book_ids)))
        container.get_config_service().update_multiple_config({
            "auto_search.skip_book_ids": skip_value
        })
        self.logger.debug(
            "Persisted skipped book IDs",
            extra={"count": len(self._skip_book_ids)},
        )

    def _persist_round_robin_cursor(self):
        container.get_config_service().update_multiple_config({
            "auto_search.round_robin_cursor": self._round_robin_cursor
        })
        self.logger.debug(
            "Persisted round-robin cursor",
            extra={"cursor": self._round_robin_cursor},
        )

    # --- Core loop ---

    def run_cycle(self) -> None:
        """One automatic search pass (run by the background task engine)."""
        self.last_run = datetime.utcnow()
        self.reload_configuration()

        if not self.config.get("auto_download_enabled"):
            self.logger.debug("Automatic downloads disabled; sleeping", extra={"auto_download_enabled": False})
            return
        if self.paused:
            self.logger.debug("Automatic downloads paused; sleeping", extra={"paused": True})
            return
        if not self._within_schedule_window():
            self.logger.debug(
                "Outside configured schedule window; sleeping",
                extra={
                    "schedule_start_hour": self.config.get("schedule_start_hour"),
                    "schedule_end_hour": self.config.get("schedule_end_hour"),
                },
            )
            return

        try:
            pending_books = self._collect_candidate_books(limit=None)
            self._queue_snapshot = pending_books

            if not pending_books:
                self.logger.debug("No eligible 'Wanted' books found this cycle", extra={"pending": 0})
                return
            batch = self._select_next_batch(pending_books, self.config.get("max_batch_size", 2))
            queued = self._queue_books(batch, max_batch=len(batch))
            if batch:
                self._round_robin_cursor = batch[-1]["id"]
                self._persist_round_robin_cursor()
            if queued:
                self.logger.info(
                    "Queued books for automatic download",
                    extra={"queued": queued, "pending": len(pending_books), "cursor": self._round_robin_cursor},
                )
        except Exception as exc:
            self.last_error = str(exc)
            self.logger.exception("Automatic search cycle failed", extra={"error": str(exc)})

    def scan_interval(self) -> float:
        """Seconds between automatic search passes."""
        return self.config.get("scan_interval_seconds", 120)

    def _within_schedule_window(self) -> bool:
        """True if automatic search is allowed to run right now. Disabled
        (always True) unless schedule_window_enabled is set. Supports an
        overnight window that wraps past midnight (start_hour > end_hour)."""
        if not self.config.get("schedule_window_enabled"):
            return True

        start_hour = self.config.get("schedule_start_hour", 0)
        end_hour = self.config.get("schedule_end_hour", 24)
        if start_hour == end_hour:
            return True

        current_hour = datetime.now().hour
        if start_hour < end_hour:
            return start_hour <= current_hour < end_hour
        return current_hour >= start_hour or current_hour < end_hour

    # --- Candidate discovery & queue orchestration ---

    def _collect_candidate_books(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        db_service = container.get_database_service()
        download_service = container.get_download_management_service()

        wanted_books = db_service.books.get_books_by_status("wanted")
        candidates: List[Dict[str, Any]] = []

        for book in wanted_books:
            book_id = book.get("ID") or book.get("id")
            asin = book.get("ASIN") or book.get("asin")

            if not book_id or not asin or asin == "N/A":
                continue

            if book_id in self._skip_book_ids:
                continue

            monitored = book.get("monitored")
            if monitored is not None and not monitored:
                continue

            existing = download_service.queue_manager.get_active_download_by_asin(asin)
            if existing:
                continue

            if self._is_future_release(book):
                continue

            candidates.append(
                {
                    "id": int(book_id),
                    "asin": asin,
                    "title": book.get("Title") or book.get("title") or "Unknown Title",
                    "author": book.get("Author") or book.get("author") or "Unknown Author",
                }
            )

        candidates.sort(key=lambda b: b["id"])

        if limit is not None:
            candidates = candidates[:limit]

        return candidates

    def _select_next_batch(self, candidates: List[Dict[str, Any]], batch_size: int) -> List[Dict[str, Any]]:
        """Round-robin selection over `candidates` (sorted by book ID ascending).

        Resumes just after `_round_robin_cursor`, wrapping back to the start
        of the list once the highest ID is passed - so every Wanted book
        gets a turn over time instead of the same early-ID subset winning
        every cycle while a later one silently never gets searched.
        """
        if not candidates or batch_size <= 0:
            return []

        start_index = 0
        for index, book in enumerate(candidates):
            if book["id"] > self._round_robin_cursor:
                start_index = index
                break
        else:
            start_index = 0

        rotated = candidates[start_index:] + candidates[:start_index]
        return rotated[:batch_size]

    def _queue_books(self, pending_books: List[Dict[str, Any]], max_batch: Optional[int] = None) -> int:
        batch_limit = max_batch if max_batch is not None else self.config.get("max_batch_size", 2)
        download_service = container.get_download_management_service()

        queued = 0
        for book in pending_books[:batch_limit]:
            if not self.running:
                break

            result = download_service.add_to_queue(
                book_asin=book["asin"],
                search_result_id=None,
                priority=5,
                title=book["title"],
                author=book["author"],
                download_type="torrent",
            )

            if result.get("success"):
                queued += 1
                self.metrics["total_books_queued"] += 1
                self.metrics["last_book_queued"] = book["asin"]
                self.metrics["last_queue_time"] = datetime.utcnow().isoformat()
                try:
                    self._queue_snapshot.remove(book)
                except ValueError:
                    pass
            else:
                self.logger.warning(
                    "Failed to queue book",
                    extra={
                        "title": book.get("title"),
                        "asin": book.get("asin"),
                        "message": result.get("message"),
                    },
                )

        return queued

    def _queue_single_book(self, book_id: int) -> Dict[str, Any]:
        db_service = container.get_database_service()
        download_service = container.get_download_management_service()

        book = db_service.books.get_book_by_id(book_id)
        if not book:
            return {"success": False, "error": f"Book {book_id} not found"}

        asin = book.get("ASIN") or book.get("asin")
        if not asin or asin == "N/A":
            return {"success": False, "error": "Book is missing a valid ASIN"}

        existing = download_service.queue_manager.get_active_download_by_asin(asin)
        if existing:
            return {
                "success": False,
                "error": "Book already has an active download in the queue",
                "download_id": existing.get("id"),
            }

        if self._is_future_release(book):
            return {"success": False, "error": "Book release date is in the future"}

        result = download_service.add_to_queue(
            asin,
            search_result_id=None,
            priority=5,
            title=book.get("Title", "Unknown Title"),
            author=book.get("Author", "Unknown Author"),
            download_type="torrent",
        )

        if not result.get("success"):
            return {"success": False, "error": result.get("message", "Failed to queue")}

        if book_id in self._skip_book_ids:
            self._skip_book_ids.remove(book_id)
            self._persist_skip_book_ids()
        self.metrics["total_books_queued"] += 1
        self.metrics["last_book_queued"] = asin
        self.metrics["last_queue_time"] = datetime.utcnow().isoformat()

        return {"success": True, "download_id": result.get("download_id")}

    # --- Release-date helpers ---

    def _is_future_release(self, book: Dict[str, Any]) -> bool:
        raw_value = (
            book.get("Release Date")
            or book.get("release_date")
            or book.get("releaseDate")
        )

        if not raw_value:
            return False

        if isinstance(raw_value, str):
            cleaned = raw_value.strip()
        else:
            cleaned = str(raw_value).strip()

        if not cleaned or cleaned.lower() in {"unknown", "n/a", "tbd", "none"}:
            return False

        parsed = self._parse_release_date(cleaned)
        if parsed is not None:
            return parsed.date() > datetime.utcnow().date()

        release_year = self._extract_year(cleaned)
        if release_year is None:
            return False
        return release_year > datetime.utcnow().year

    @staticmethod
    def _parse_release_date(date_str: str) -> Optional[datetime]:
        normalized = date_str.strip()
        if not normalized:
            return None

        iso_candidate = normalized
        if normalized.endswith("Z"):
            iso_candidate = normalized[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(iso_candidate)
        except ValueError:
            pass

        date_formats = [
            "%Y-%m-%d",
            "%Y-%m-%d %H:%M:%S",
            "%m/%d/%Y",
            "%m-%d-%Y",
            "%B %d, %Y",
            "%b %d, %Y",
            "%B %d %Y",
            "%b %d %Y",
        ]

        for fmt in date_formats:
            try:
                return datetime.strptime(normalized, fmt)
            except ValueError:
                continue

        return None

    @staticmethod
    def _extract_year(date_str: str) -> Optional[int]:
        match = re.search(r"\b(19|20)\d{2}\b", date_str)
        if match:
            try:
                return int(match.group())
            except ValueError:
                return None
        return None

    # --- Public control surface (used by API/UI) ---

    def get_status(self) -> Dict[str, Any]:
        pending = len(self._queue_snapshot)
        return {
            "running": self.running,
            "paused": self.paused,
            "auto_enabled": self.config.get("auto_download_enabled", False),
            "queue_size": pending,
            "scan_interval": self.config.get("scan_interval_seconds", 120),
            "max_batch_size": self.config.get("max_batch_size", 2),
            "round_robin_cursor": self._round_robin_cursor,
            "schedule_window_enabled": self.config.get("schedule_window_enabled", False),
            "schedule_start_hour": self.config.get("schedule_start_hour", 0),
            "schedule_end_hour": self.config.get("schedule_end_hour", 24),
            "within_schedule_window": self._within_schedule_window(),
            "metrics": self.metrics,
            "last_run": self.last_run.isoformat() if self.last_run else None,
            "last_error": self.last_error,
        }

    def get_search_queue(self) -> List[Dict[str, Any]]:
        pending_books = self._collect_candidate_books(limit=250)
        self._queue_snapshot = pending_books
        return pending_books

    def clear_queue(self) -> Dict[str, Any]:
        pending_books = self._collect_candidate_books(limit=None)
        skipped = 0
        for book in pending_books:
            book_id = book.get("id")
            if book_id is None:
                continue
            if book_id not in self._skip_book_ids:
                self._skip_book_ids.append(book_id)
                skipped += 1
        if skipped:
            self._persist_skip_book_ids()
        return {
            "success": True,
            "skipped": skipped,
            "message": "Current automatic queue cleared",
        }

    def force_search_book(self, book_id: int) -> Dict[str, Any]:
        if book_id in self._skip_book_ids:
            self._skip_book_ids.remove(book_id)
            self._persist_skip_book_ids()
        result = self._queue_single_book(book_id)
        result.setdefault("success", result.get("error") is None)
        return result
