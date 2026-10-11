"""
Module Name: event_subscribers.py
Author: AuralArchivist
Created: October 9, 2026
Last Modified: October 9, 2026
Description:
    Wires follow-up work to application events: covers, authors and series
    when a book is added, Hardcover shelf updates when its status changes,
    Audiobookshelf notices when a book file is added or moved, and reloading
    services whose settings were saved.

Location:
    /services/event_subscribers.py
"""

import threading
import time
from typing import Any, Dict, List, Optional

from core import events
from core.logging import get_module_logger
from services.container import container

_LOGGER = get_module_logger("Service.EventSubscribers")

AUTHOR_REFETCH_SECONDS = 60 * 60

_recent_authors: Dict[str, float] = {}
_authors_lock = threading.Lock()


def register() -> None:
    """Subscribe every follow-up handler; safe to call more than once."""
    from services.sync.audiobookshelf import jobs as abs_jobs
    events.subscribe(events.BOOK_ADDED, cache_cover, background=True)
    events.subscribe(events.BOOK_ADDED, fetch_authors, background=True)
    events.subscribe(events.BOOK_ADDED, queue_series, background=True)
    events.subscribe(events.SERIES_LOOKUP_NEEDED, queue_series, background=True)
    events.subscribe(events.BOOK_STATUS_CHANGED, push_hardcover_status, background=True)
    events.subscribe(events.BOOK_FILE_ADDED, abs_jobs.on_book_added)
    events.subscribe(events.BOOK_FILE_MOVED, abs_jobs.on_book_moved)
    events.subscribe(events.CONFIG_CHANGED, reload_settings)


def cache_cover(cover_url: Optional[str] = None, **_: Any) -> None:
    if cover_url:
        from services.metadata.covers import cache_book_cover
        cache_book_cover(cover_url)


def fetch_authors(contributors: Optional[List[Dict[str, Any]]] = None, **_: Any) -> None:
    """Fetch details for the book's authors, once per author within AUTHOR_REFETCH_SECONDS."""
    authors = [c for c in contributors or []
               if isinstance(c, dict) and c.get('Role') == 'Author' and c.get('Name') and c.get('ASIN')]
    now = time.monotonic()
    with _authors_lock:
        fresh = [c for c in authors if now - _recent_authors.get(c['ASIN'], -AUTHOR_REFETCH_SECONDS) >= AUTHOR_REFETCH_SECONDS]
        for contributor in fresh:
            _recent_authors[contributor['ASIN']] = now
    if fresh:
        from services.metadata.authors import process_book_contributors_for_authors
        process_book_contributors_for_authors({'Contributors': fresh})


def queue_series(asin: Optional[str] = None, **_: Any) -> None:
    """Ask the scheduler's New series job to run; it finds what needs syncing."""
    if asin:
        container.get_metadata_refresh_scheduler().request_missing_series()


def push_hardcover_status(book_id: int, **_: Any) -> None:
    hardcover = container.get_hardcover_service()
    if hardcover.is_connected():
        hardcover.enqueue_status_push(book_id)


# --- Saved settings -----------------------------------------------------------

# Services that keep settings in memory, and the config sections they read
_SETTINGS_READERS = (
    ('download_management', {'download_management', 'conversion', 'audible', 'jackett',
                             'qbittorrent', 'sabnzbd', 'nzbget'}),
    ('automatic_download', {'auto_search'}),
    ('import', {'import', 'naming'}),
    ('file_naming', {'naming'}),
)


def reload_settings(sections: List[str], **_: Any) -> None:
    """Reload services that were already created and read a changed section."""
    changed = set(sections)
    for name, reads in _SETTINGS_READERS:
        service = container.created(name)
        if service is not None and changed & reads:
            service.reload_configuration()
