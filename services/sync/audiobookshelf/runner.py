"""
Module Name: runner.py
Author: AuralArchivist
Created: October 3, 2026
Last Modified: October 9, 2026
Description:
    Background work for Audiobookshelf. It runs due jobs every few seconds
    (a just-imported book should be found within a minute, which the 15
    minute metadata scheduler tick can't do) and a full sync of links and
    progress every few hours or on request.

Location:
    /services/sync/audiobookshelf/runner.py
"""

import threading
import time
from datetime import datetime
from typing import Any, Dict

from core.logging import get_module_logger
from integrations.audiobookshelf import AbsAuthError, AbsError

from . import connection

_LOGGER = get_module_logger("Service.Audiobookshelf.Runner")

TASK_NAME = 'audiobookshelf'
JOB_POLL_SECONDS = 15
STARTUP_DELAY_SECONDS = 45
PRUNE_EVERY_SECONDS = 24 * 3600


class AbsRunner:

    def __init__(self, *, logger=None):
        self.logger = logger or _LOGGER
        self._sync_requested = False
        self._syncing = False
        self._last_sync_at = 0.0
        self._last_prune_at = 0.0
        self._lock = threading.Lock()

    def request_sync(self) -> str:
        with self._lock:
            if self._syncing:
                return 'running'
            self._sync_requested = True
        return 'queued'

    @property
    def syncing(self) -> bool:
        return self._syncing

    def tick(self) -> None:
        if not connection.is_enabled():
            return
        from . import jobs
        interval = connection.get_settings()['sync_interval_hours'] * 3600
        with self._lock:
            due = self._sync_requested or (interval > 0 and time.time() - self._last_sync_at >= interval)
            self._sync_requested = False
        if due:
            self.full_sync()
        jobs.process_due()
        if time.time() - self._last_prune_at >= PRUNE_EVERY_SECONDS:
            jobs.prune()
            self._last_prune_at = time.time()

    def full_sync(self) -> Dict[str, Any]:
        from . import linker, mapping, progress
        with self._lock:
            if self._syncing:
                return {'skipped': 'A sync is already running'}
            self._syncing = True
        started = time.time()
        result: Dict[str, Any] = {}
        try:
            client = connection.get_client()
            caps = connection.capabilities_from_user(client.me())
            connection.set_state(capabilities=caps)
            mapping.refresh_watcher_state(client)
            result['links'] = linker.sync_links(client)
            result['progress'] = progress.sync_progress(client)
            connection.set_state(last_sync=datetime.now().isoformat(timespec='seconds'), last_error=None,
                                 last_sync_result=_summary(result), unlinked=result['links']['unlinked'],
                                 orphans=result['links']['orphans'])
            connection.clear_auth_failure()
            self.logger.info("Audiobookshelf sync finished", extra=_summary(result))
        except AbsAuthError as exc:
            connection.record_error(str(exc))
            connection.notify_auth_failure()
            result['error'] = str(exc)
        except AbsError as exc:
            connection.record_error(str(exc))
            result['error'] = str(exc)
            self.logger.warning("Audiobookshelf sync failed", extra={"error": str(exc)})
        finally:
            self._last_sync_at = time.time()
            self._syncing = False
            result['seconds'] = round(time.time() - started, 1)
        return result


def _summary(result: Dict[str, Any]) -> Dict[str, Any]:
    links = result.get('links') or {}
    prog = result.get('progress') or {}
    return {'linked': links.get('linked', 0), 'new_links': links.get('new_links', 0),
            'unlinked': len(links.get('unlinked') or []), 'orphans': len(links.get('orphans') or []),
            'dropped': links.get('dropped', 0), 'progress_entries': prog.get('entries', 0),
            'newly_finished': prog.get('newly_finished', 0), 'prompts': prog.get('prompts', 0),
            'users': prog.get('users') or [], 'note': prog.get('note')}
