"""
Module Name: events.py
Author: AuralArchivist
Created: October 9, 2026
Last Modified: October 9, 2026
Description:
    Application events. Code that changes something publishes an event;
    follow-up work subscribes, either inline or on a small background pool.

Location:
    /core/events.py
"""

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, List, Tuple

from core.logging import get_module_logger

_LOGGER = get_module_logger("Core.Events")

BOOK_ADDED = 'book_added'
BOOK_FILE_ADDED = 'book_file_added'
BOOK_FILE_MOVED = 'book_file_moved'
BOOK_STATUS_CHANGED = 'book_status_changed'
SERIES_LOOKUP_NEEDED = 'series_lookup_needed'
CONFIG_CHANGED = 'config_changed'
EVENTS = (BOOK_ADDED, BOOK_FILE_ADDED, BOOK_FILE_MOVED, BOOK_STATUS_CHANGED, SERIES_LOOKUP_NEEDED,
          CONFIG_CHANGED)

BACKGROUND_WORKERS = 4

_listeners: Dict[str, List[Tuple[Callable[..., None], bool]]] = {event: [] for event in EVENTS}
_lock = threading.Lock()
_executor = ThreadPoolExecutor(max_workers=BACKGROUND_WORKERS, thread_name_prefix='events')


def subscribe(event: str, callback: Callable[..., None], *, background: bool = False) -> None:
    """Call `callback(**payload)` whenever `event` is published."""
    with _lock:
        if not any(existing is callback for existing, _ in _listeners[event]):
            _listeners[event].append((callback, background))


def publish(event: str, **payload) -> None:
    """Run every subscriber; a failing subscriber never fails the publisher."""
    with _lock:
        listeners = list(_listeners[event])
    for callback, background in listeners:
        if background:
            _executor.submit(_run, event, callback, payload)
        else:
            _run(event, callback, payload)


def _run(event: str, callback: Callable[..., None], payload: dict) -> None:
    try:
        callback(**payload)
    except Exception:
        _LOGGER.warning("Event subscriber failed", extra={"event": event, "subscriber": callback.__qualname__},
                        exc_info=True)
