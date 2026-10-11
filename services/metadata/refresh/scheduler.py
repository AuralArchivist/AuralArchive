"""
Module Name: scheduler.py
Author: AuralArchivist
Created: September 24, 2026
Last Modified: October 9, 2026
Description:
    Scheduled library, series, book and author metadata refresh, and Hardcover matching.

Location:
    /services/metadata/refresh/scheduler.py
"""

import re
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import text

from core.logging import get_module_logger
from core.scheduler import get_scheduler
from services.metadata.series import get_series_service
from services.container import container

_LOGGER = get_module_logger("Service.MetadataRefresh.Scheduler")

SECTION = 'metadata_refresh'
JOBS = ('library', 'authors', 'series', 'books', 'hardcover', 'calendar')
MISSING_AUTHORS_JOB = 'authors_missing'
MISSING_SERIES_JOB = 'series_missing'
JOB_LABELS = {
    'library': 'Library check',
    'authors': 'Authors',
    'series': 'Series',
    'books': 'Books',
    'hardcover': 'Hardcover matching',
    'calendar': 'Release calendar',
    MISSING_AUTHORS_JOB: 'New authors',
    MISSING_SERIES_JOB: 'New series',
}
DEFAULTS = {
    'enabled': True,
    'library_interval_days': 1,
    'authors_interval_days': 7,
    'series_interval_days': 7,
    'books_interval_days': 30,
    'hardcover_interval_days': 7,
    'calendar_interval_days': 1,
    'request_delay_seconds': 2,
}
MAX_INTERVAL_DAYS = 365
MAX_DELAY_SECONDS = 30

TASK_NAME = 'metadata_refresh'
CHECK_INTERVAL_SECONDS = 15 * 60
STARTUP_DELAY_SECONDS = 60
MISSING_AUTHOR_RETRY_SECONDS = 24 * 3600
MISSING_SERIES_RETRY_SECONDS = 24 * 3600
SERIES_LOOKUP_BATCH = 50
_AUDIBLE_ASIN = re.compile(r'^[A-Z0-9]{10}$')
AUDIBLE_AUTH_CHECK_SECONDS = 6 * 3600


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed


class MetadataRefreshScheduler:
    """Scheduled author / series / book metadata refresh. Use container.get_metadata_refresh_scheduler()."""

    def __init__(self, *, logger=None):
        self.logger = logger or _LOGGER
        self._lock = threading.Lock()
        self._pending: List[str] = []
        self._running_job: Optional[str] = None
        self._progress: Dict[str, Any] = {'done': 0, 'total': 0}
        self._stop_requested = False
        self._last_results: Dict[str, Dict[str, Any]] = {}
        self._failed_authors: Dict[str, float] = {}
        self._failed_series_books: Dict[str, float] = {}
        self._series_done: Set[str] = set()
        self._series_requeue = False
        self._library_allow_mass_missing = False
        self._library_details: Dict[str, Any] = {}
        self._next_audible_check = 0.0

    # --- Settings ---

    @staticmethod
    def _config():
        return container.get_config_service()

    def get_settings(self) -> Dict[str, Any]:
        section = self._config().get_section(SECTION) or {}

        def as_int(key: str, upper: int) -> int:
            try:
                return max(0, min(upper, int(section.get(key, DEFAULTS[key]))))
            except (TypeError, ValueError):
                return DEFAULTS[key]

        enabled_raw = section.get('enabled', DEFAULTS['enabled'])
        enabled = enabled_raw if isinstance(enabled_raw, bool) else str(enabled_raw).strip().lower() in ('1', 'true', 'yes', 'on')
        return {
            'enabled': enabled,
            'library_interval_days': as_int('library_interval_days', MAX_INTERVAL_DAYS),
            'authors_interval_days': as_int('authors_interval_days', MAX_INTERVAL_DAYS),
            'series_interval_days': as_int('series_interval_days', MAX_INTERVAL_DAYS),
            'books_interval_days': as_int('books_interval_days', MAX_INTERVAL_DAYS),
            'hardcover_interval_days': as_int('hardcover_interval_days', MAX_INTERVAL_DAYS),
            'calendar_interval_days': as_int('calendar_interval_days', MAX_INTERVAL_DAYS),
            'request_delay_seconds': as_int('request_delay_seconds', MAX_DELAY_SECONDS),
        }

    def save_settings(self, data: Dict[str, Any]) -> Dict[str, str]:
        """Validate and save settings. Returns {field: error} (empty on success)."""
        updates, errors = {}, {}
        if 'enabled' in data:
            updates[f'{SECTION}.enabled'] = 'true' if bool(data['enabled']) else 'false'
        for key, upper in (
            ('library_interval_days', MAX_INTERVAL_DAYS),
            ('authors_interval_days', MAX_INTERVAL_DAYS),
            ('series_interval_days', MAX_INTERVAL_DAYS),
            ('books_interval_days', MAX_INTERVAL_DAYS),
            ('hardcover_interval_days', MAX_INTERVAL_DAYS),
            ('calendar_interval_days', MAX_INTERVAL_DAYS),
            ('request_delay_seconds', MAX_DELAY_SECONDS),
        ):
            if key not in data:
                continue
            try:
                value = int(data[key])
            except (TypeError, ValueError):
                errors[key] = 'Must be a whole number'
                continue
            if not 0 <= value <= upper:
                errors[key] = f'Must be between 0 and {upper}'
                continue
            updates[f'{SECTION}.{key}'] = str(value)
        if not errors and updates:
            if not self._config().update_multiple_config(updates):
                errors['_'] = 'Failed to save configuration'
            else:
                self._kick()
        return errors

    def _last_run(self, job: str) -> Optional[datetime]:
        return _parse_dt(self._config().get_config_value(SECTION, f'last_{job}_run'))

    def _set_last_run(self, job: str) -> None:
        self._config().update_config(SECTION, f'last_{job}_run', datetime.now().isoformat(timespec='seconds'))

    def _is_due(self, job: str, settings: Dict[str, Any]) -> bool:
        interval = settings[f'{job}_interval_days']
        if interval <= 0:
            return False
        last = self._last_run(job)
        return last is None or datetime.now() - last >= timedelta(days=interval)

    # --- Status / control ---

    def get_status(self) -> Dict[str, Any]:
        settings = self.get_settings()
        jobs = {}
        for job in JOBS:
            interval = settings[f'{job}_interval_days']
            last = self._last_run(job)
            next_run = None
            if settings['enabled'] and interval > 0:
                next_run = (last + timedelta(days=interval)) if last else datetime.now()
            jobs[job] = {
                'interval_days': interval,
                'last_run': last.isoformat(timespec='seconds') if last else None,
                'next_run': next_run.isoformat(timespec='seconds') if next_run else None,
                'last_result': self._last_results.get(job),
                'queued': job in self._pending,
            }
        with self._lock:
            running = self._running_job
            progress = dict(self._progress)
        return {
            'settings': settings,
            'jobs': jobs,
            'running': running,
            'running_label': JOB_LABELS.get(running) if running else None,
            'progress': progress,
            'hardcover_connected': self._hardcover_client() is not None,
            'authors_missing': self._count_missing_authors(),
        }

    def request_run(self, job: str) -> str:
        """Queue a job to run as soon as possible. Returns 'queued',
        'running' (already running) or raises ValueError."""
        if job not in JOBS and job not in (MISSING_AUTHORS_JOB, MISSING_SERIES_JOB):
            raise ValueError(f"Unknown job: {job}")
        with self._lock:
            if self._running_job == job:
                return 'running'
            if job not in self._pending:
                self._pending.append(job)
        self._kick()
        return 'queued'

    def request_missing_authors(self) -> None:
        """Called when the Authors page sees authors without metadata."""
        settings = self.get_settings()
        if settings['enabled'] and settings['authors_interval_days'] > 0 and self._running_job != MISSING_AUTHORS_JOB:
            self.request_run(MISSING_AUTHORS_JOB)

    def request_missing_series(self) -> None:
        """Called when books are added: sync series nobody has fetched yet.

        Queued even while the job runs, so books added mid-run are picked up next.
        """
        settings = self.get_settings()
        if not (settings['enabled'] and settings['series_interval_days'] > 0):
            return
        with self._lock:
            if MISSING_SERIES_JOB not in self._pending:
                self._pending.append(MISSING_SERIES_JOB)
        self._kick()

    def apply_blocked_missing(self) -> str:
        """User confirmed the blocked 'mark missing' result: re-run the
        library check once with the safety limit lifted."""
        self._library_allow_mass_missing = True
        return self.request_run('library')

    def request_stop(self) -> bool:
        """Stop the running job after its current item and drop queued jobs."""
        with self._lock:
            self._pending.clear()
            if not self._running_job:
                return False
            self._stop_requested = True
            return True

    def _kick(self) -> None:
        get_scheduler().run_now(TASK_NAME)

    # --- Tick (run by the background task engine) ---

    def tick(self) -> None:
        try:
            from services.notifications.watchers import check_new_releases
            check_new_releases()
        except Exception:
            self.logger.exception("Release check failed")
        try:
            from services.metadata.release_calendar import service as release_calendar
            release_calendar.check_notifications()
        except Exception:
            self.logger.exception("Release calendar alerts failed")
        from services.notifications import events
        events.check_storage()
        if time.monotonic() >= self._next_audible_check:
            self._next_audible_check = time.monotonic() + AUDIBLE_AUTH_CHECK_SECONDS
            events.check_audible_auth()

        while True:
            with self._lock:
                job = self._pending.pop(0) if self._pending else None
            if job is None:
                break
            self._run(job, self.get_settings())

        settings = self.get_settings()
        if not settings['enabled']:
            return
        if settings['authors_interval_days'] > 0:
            self._run(MISSING_AUTHORS_JOB, settings, quiet_if_empty=True)
        if settings['series_interval_days'] > 0:
            self._run(MISSING_SERIES_JOB, settings, quiet_if_empty=True)
        for job in JOBS:
            if self._is_due(job, settings):
                self._run(job, settings)

    def _run(self, job: str, settings: Dict[str, Any], *, quiet_if_empty: bool = False) -> None:
        handler = {
            'library': self._check_library,
            'authors': self._refresh_authors,
            'series': self._refresh_series,
            'books': self._refresh_books,
            'hardcover': self._match_hardcover,
            'calendar': self._sync_calendar,
            MISSING_AUTHORS_JOB: self._fetch_missing_authors,
            MISSING_SERIES_JOB: self._fetch_missing_series,
        }[job]
        items = handler(settings, collect=True)
        if items is None:
            return
        if not items and quiet_if_empty:
            return

        label = JOB_LABELS[job]
        with self._lock:
            self._running_job = job
            self._progress = {'done': 0, 'total': len(items)}
            self._stop_requested = False
        event_id = self._status('start_event', category='metadata', title=f'Metadata refresh: {label}',
                                message=f'0 / {len(items)}', progress=0, source='Metadata')
        started = time.monotonic()
        ok = failed = 0
        stopped = False
        try:
            for index, item in enumerate(items, 1):
                if self._stop_requested:
                    stopped = True
                    break
                try:
                    if handler(settings, item=item):
                        ok += 1
                    else:
                        failed += 1
                except Exception:
                    failed += 1
                    self.logger.exception("Metadata refresh item failed", extra={"job": job, "item": str(item)})
                with self._lock:
                    self._progress = {'done': index, 'total': len(items)}
                if event_id is not None:
                    self._status('update_event', event_id, message=f'{index} / {len(items)}',
                                 progress=round(index * 100 / max(1, len(items)), 1))
                if index < len(items) and settings['request_delay_seconds']:
                    time.sleep(settings['request_delay_seconds'])

            if job in ('series', MISSING_SERIES_JOB) and not stopped:
                self._sync_series_library_status()
            if job == 'library':
                self._library_details = self._library_service().finish()
        finally:
            with self._lock:
                self._running_job = None
                self._progress = {'done': 0, 'total': 0}

        summary = f"{ok} updated, {failed} failed" + (" (stopped)" if stopped else "")
        self._last_results[job] = {
            'at': datetime.now().isoformat(timespec='seconds'),
            'updated': ok, 'failed': failed, 'total': len(items), 'stopped': stopped,
            'seconds': round(time.monotonic() - started),
        }
        if job == 'library':
            details = self._library_details
            self._last_results[job]['details'] = details
            summary = self._library_summary(details) + (" (stopped)" if stopped else "")
            failed = len(details.get('missing_blocked') or []) + details.get('review', 0)
        if job == 'hardcover':
            summary = f"{ok} linked, {failed} with no confident match" + (" (stopped)" if stopped else "")
            failed = 0
        if job == 'calendar':
            summary = f"{ok} of {len(items)} watches refreshed" + (" (stopped)" if stopped else "")
        if job in JOBS and not stopped:
            self._set_last_run(job)
        if job == MISSING_SERIES_JOB and self._series_requeue and not stopped:
            # Series IDs were just found; their series sync in a follow-up run
            self._series_requeue = False
            with self._lock:
                if MISSING_SERIES_JOB not in self._pending:
                    self._pending.append(MISSING_SERIES_JOB)
        if job in ('authors', 'series', 'books'):
            from services.notifications.events import metadata_job_finished
            metadata_job_finished(job, label, failed, len(items), stopped)
        if event_id is not None:
            self._status('complete_event', event_id, message=summary,
                         level='warning' if failed or stopped else 'success')
        self.logger.info(f"Metadata refresh finished: {label}", extra={"job": job, "updated": ok, "failed": failed, "stopped": stopped})

    # --- Jobs: called with collect=True for the work list, then once per item ---

    def _sync_calendar(self, settings: Dict[str, Any], *, collect: bool = False, item: Any = None):
        from services.metadata.release_calendar import service as release_calendar
        if collect:
            watches = release_calendar.list_watches(enabled_only=True)
            return [w['id'] for w in watches] if watches else None
        release_calendar.sync_watch(item)
        return True

    @staticmethod
    def _library_service():
        return container.get_library_check_service()

    @staticmethod
    def _library_summary(details: Dict[str, Any]) -> str:
        parts = [f"{details.get('checked', 0)} checked"]
        for key, label in (('relinked', 'relinked'), ('missing', 'missing'), ('recovered', 'found again'),
                           ('adopted', 'new imported'), ('review', 'need review')):
            if details.get(key):
                parts.append(f"{details[key]} {label}")
        if details.get('missing_blocked'):
            parts.append(f"{len(details['missing_blocked'])} missing held for review")
        if details.get('unreachable'):
            names = ', '.join(u['name'] for u in details['unreachable'])
            parts.append(f"skipped {names} (not reachable, {details.get('held', 0)} books left as they are)")
        return ', '.join(parts)

    def _check_library(self, settings, *, collect=False, item=None):
        """collect: fast local pass (verify, relink, missing, discovery);
        items: new folders to match and adopt, paced like other jobs."""
        service = self._library_service()
        if collect:
            allow = self._library_allow_mass_missing
            self._library_allow_mass_missing = False
            units, details = service.prepare(allow_mass_missing=allow)
            self._library_details = details
            if units is None:
                self._last_results['library'] = {'at': datetime.now().isoformat(timespec='seconds'),
                                                 'skipped': details.get('skipped'), 'details': details}
                self.logger.warning("Library check skipped", extra={"reason": details.get('skipped')})
                return None
            return units
        return service.process_unit(item)

    def _fetch_missing_authors(self, settings, *, collect=False, item=None):
        if collect:
            now = time.monotonic()
            return [name for name in self._missing_authors()
                    if now - self._failed_authors.get(name, -MISSING_AUTHOR_RETRY_SECONDS) >= MISSING_AUTHOR_RETRY_SECONDS]
        from services.metadata.authors import ensure_author_metadata_cached
        metadata = ensure_author_metadata_cached(item, refresh_if_stale=False)
        if metadata:
            self._failed_authors.pop(item, None)
            return True
        self._failed_authors[item] = time.monotonic()
        return False

    def _refresh_authors(self, settings, *, collect=False, item=None):
        max_age = timedelta(days=max(1, settings['authors_interval_days']))
        if collect:
            db = self._db()
            stale = []
            for name in db.authors.get_all_authors():
                metadata = db.authors.get_author_metadata(name) or {}
                fetched = _parse_dt(metadata.get('last_fetched_at'))
                if metadata and (fetched is None or datetime.utcnow() - fetched >= max_age):
                    stale.append(name)
            return stale
        from services.metadata.authors import ensure_author_metadata_cached
        from services.notifications.events import author_titles_changed
        before = int((self._db().authors.get_author_metadata(item) or {}).get('audible_books_count') or 0)
        metadata = ensure_author_metadata_cached(item, refresh_if_stale=True, max_age_hours=0)
        author_titles_changed(item, before, int((metadata or {}).get('audible_books_count') or 0))
        return bool(metadata)

    def _refresh_series(self, settings, *, collect=False, item=None):
        service = get_series_service()
        if collect:
            return self._db().series.get_all_series_asins()
        return bool(service.refresh_series(item).get('success'))

    def _fetch_missing_series(self, settings, *, collect=False, item=None):
        """Books with a series name but no series ID get it looked up in batches;
        series that books point to but were never fetched get synced, once each."""
        service = get_series_service()
        if collect:
            self._series_done = set()
            self._series_requeue = False
            books, series = self._missing_series()
            now = time.monotonic()
            books = [a for a in books
                     if now - self._failed_series_books.get(a, -MISSING_SERIES_RETRY_SECONDS) >= MISSING_SERIES_RETRY_SECONDS]
            items: List[Tuple[str, Any]] = [('books', books[i:i + SERIES_LOOKUP_BATCH])
                                            for i in range(0, len(books), SERIES_LOOKUP_BATCH)]
            items += [('series', asin) for asin in series]
            return items
        kind, value = item
        if kind == 'books':
            return self._resolve_series_ids(service, value)
        if value in self._series_done:
            return True
        self._series_done.add(value)
        return bool(service.refresh_series(value).get('success'))

    def _resolve_series_ids(self, service, asins: List[str]) -> bool:
        products = service.fetcher.fetch_products(asins, response_groups='series,relationships')
        if not products:
            for asin in asins:
                self._failed_series_books[asin] = time.monotonic()
            return False
        for asin in asins:
            product = products.get(asin)
            found = service.extractor.extract_series_metadata(product) if product else None
            series_asin = (found or {}).get('series_asin')
            if series_asin:
                service.sync.update_book_series_asin(asin, series_asin)
                self._failed_series_books.pop(asin, None)
                self._series_requeue = True
            else:
                self._failed_series_books[asin] = time.monotonic()
        return True

    def _missing_series(self) -> Tuple[List[str], List[str]]:
        engine = self._db().connection_manager.get_engine()
        with engine.connect() as conn:
            books = [row[0] for row in conn.execute(text(
                "SELECT DISTINCT asin FROM books "
                "WHERE (series_asin IS NULL OR series_asin = '') "
                "AND series IS NOT NULL AND TRIM(series) NOT IN ('', 'N/A') AND asin IS NOT NULL"
            ))]
            series = [row[0] for row in conn.execute(text(
                "SELECT DISTINCT b.series_asin FROM books b "
                "LEFT JOIN series_metadata m ON m.series_asin = b.series_asin "
                "WHERE b.series_asin IS NOT NULL AND b.series_asin != '' AND m.series_asin IS NULL"
            ))]
        return [a for a in books if _AUDIBLE_ASIN.match(str(a).upper())], series

    def _refresh_books(self, settings, *, collect=False, item=None):
        if collect:
            engine = self._db().connection_manager.get_engine()
            with engine.connect() as conn:
                rows = conn.execute(text(
                    "SELECT id FROM books ORDER BY COALESCE(updated_at, created_at) ASC"
                )).fetchall()
            return [int(row[0]) for row in rows]
        success, _message = container.get_metadata_update_service().update_single_book(
            int(item), notify_status=False)
        return bool(success)

    def _match_hardcover(self, settings, *, collect=False, item=None):
        client = self._hardcover_client()
        if client is None:
            return None
        from core.database.repositories import hardcover_cache
        engine = self._db().connection_manager.get_engine()
        if collect:
            with engine.connect() as conn:
                return hardcover_cache.get_unmatched_book_ids(conn)
        book = self._db().books.get_book_by_id(int(item))
        if not book:
            return False
        from integrations.hardcover.matcher import match_local_book
        match = match_local_book(client, book.get('Title'), book.get('Author'), book.get('ASIN'))['match']
        with engine.begin() as conn:
            if match:
                hardcover_cache.upsert_link(
                    conn, int(item), match['hardcover_book_id'],
                    hardcover_edition_id=match.get('edition_id'), asin=match.get('asin'),
                    match_confidence=match['confidence'],
                )
            else:
                hardcover_cache.record_match_attempt(conn, int(item))
        return bool(match)

    # --- Helpers ---

    def _hardcover_client(self):
        """Hardcover client, or None when no account is connected."""
        try:
            from integrations.hardcover.connection import get_client
            hc_config = self._config().get_hardcover_config() or {}
            return get_client(self._config()) if hc_config.get('token') else None
        except Exception:
            self.logger.warning("Could not create the Hardcover client; skipping Hardcover work", exc_info=True)
            return None

    @staticmethod
    def _db():
        return container.get_database_service()

    def _missing_authors(self) -> List[str]:
        db = self._db()
        engine = db.connection_manager.get_engine()
        with engine.connect() as conn:
            known = {row[0] for row in conn.execute(text("SELECT name FROM authors"))}
        return [name for name in db.authors.get_all_authors() if name not in known]

    def _count_missing_authors(self) -> int:
        try:
            return len(self._missing_authors())
        except Exception:
            self.logger.debug("Could not count authors missing metadata", exc_info=True)
            return 0

    def _sync_series_library_status(self) -> None:
        try:
            self._db().series.sync_library_status()
        except Exception:
            self.logger.exception("Series library status sync failed")

    def _status(self, method: str, *args, **kwargs) -> Optional[int]:
        """Best-effort status console update; returns the event id for start_event."""
        try:
            status_service = container.get_status_service()
            if not status_service:
                return None
            result = getattr(status_service, method)(*args, **kwargs)
            return result.get('id') if method == 'start_event' and result else None
        except Exception:
            self.logger.debug("Status console update failed", exc_info=True)
            return None
