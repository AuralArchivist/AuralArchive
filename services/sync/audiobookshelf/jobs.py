"""
Module Name: jobs.py
Author: AuralArchivist
Created: October 3, 2026
Last Modified: October 9, 2026
Description:
    Background work for Audiobookshelf, kept in abs_jobs so imports never wait
    on it and nothing is lost on restart: tell Audiobookshelf about new or
    moved books, find the item it creates, then match its metadata.

Location:
    /services/sync/audiobookshelf/jobs.py
"""

import json
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from core.logging import get_module_logger
from integrations.audiobookshelf import AbsAuthError, AbsError, AbsUnavailable

from . import connection
from . import mapping as abs_mapping
from services.container import container

_LOGGER = get_module_logger("Service.Audiobookshelf.Jobs")

JOBS_PER_TICK = 10
BULK_SCAN_THRESHOLD = 20
SCAN_DEBOUNCE_SECONDS = 60
RETRY_DELAYS = (60, 300, 1800)
FIND_DELAYS_ADMIN = (20, 60, 180, 600)
FIND_DELAYS_NO_NOTIFY = (60, 300, 900, 1800, 3600)
SCAN_FALLBACK_AFTER_ATTEMPT = 3
AUTH_PAUSE_SECONDS = 1800

_scan_requested: Dict[str, float] = {}
_scan_lock = threading.Lock()
_process_lock = threading.Lock()


def _at(seconds: float) -> str:
    # UTC, like SQLite's CURRENT_TIMESTAMP
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).strftime('%Y-%m-%d %H:%M:%S')


def enqueue(action: str, book_id: Optional[int], payload: Dict[str, Any], delay: float = 0) -> None:
    with container.get_engine().begin() as conn:
        conn.execute(text(
            "INSERT INTO abs_jobs (book_id, action, payload, next_run_at) VALUES (:b, :a, :p, :t)"
        ), {'b': book_id, 'a': action, 'p': json.dumps(payload), 't': _at(delay)})


# --- Book events -----------------------------------------------------------------

def _book_library(book_id: int) -> Optional[Dict[str, Any]]:
    with container.get_engine().connect() as conn:
        row = conn.execute(text("SELECT library_id FROM books WHERE id = :id"), {'id': book_id}).fetchone()
    return container.get_library_repository().get(row[0]) if row and row[0] else None


def on_book_added(book_id: int, path: str) -> None:
    if not connection.is_enabled():
        return
    library = _book_library(book_id)
    if library and abs_mapping.mapping_for_library(library['id']):
        enqueue('notify', book_id, {'library_id': library['id'], 'path': path, 'change': 'add'})


def on_book_moved(book_id: int, old_path: str, new_path: str) -> None:
    if not connection.is_enabled():
        return
    repo = container.get_library_repository()
    new_library = repo.library_for_path(new_path)
    old_library = repo.library_for_path(old_path)
    if new_library and abs_mapping.mapping_for_library(new_library['id']):
        enqueue('notify', book_id, {'library_id': new_library['id'], 'path': new_path, 'change': 'add',
                                    'old_library_id': old_library and old_library['id'], 'old_path': old_path})


# --- Processing ------------------------------------------------------------------

def process_due(limit: int = JOBS_PER_TICK) -> int:
    """Run jobs whose time has come. Called from the scheduler tick."""
    if not connection.is_enabled() or not _process_lock.acquire(blocking=False):
        return 0
    try:
        with container.get_engine().connect() as conn:
            rows = conn.execute(text(
                "SELECT id, book_id, action, payload, attempts FROM abs_jobs WHERE state = 'pending' "
                "AND next_run_at <= :now ORDER BY next_run_at, id LIMIT :n"
            ), {'now': _at(0), 'n': limit}).fetchall()
        if not rows:
            return 0
        client = connection.get_client()
        jobs = [{'id': r[0], 'book_id': r[1], 'action': r[2], 'payload': json.loads(r[3] or '{}'),
                 'attempts': r[4]} for r in rows]
        notify_jobs = [j for j in jobs if j['action'] == 'notify']
        handled = set()
        if notify_jobs:
            handled |= _bulk_scan_if_many(client, notify_jobs)
        for job in jobs:
            if job['id'] in handled:
                continue
            try:
                _run(client, job)
            except AbsAuthError as exc:
                connection.record_error(str(exc))
                connection.notify_auth_failure()
                _reschedule(job, AUTH_PAUSE_SECONDS, str(exc), count=False)
                break
            except AbsUnavailable as exc:
                connection.record_error(str(exc))
                _retry(job, str(exc))
            except AbsError as exc:
                _retry(job, str(exc))
            except Exception as exc:
                _LOGGER.warning("Audiobookshelf job failed", extra={"job": job['id'], "action": job['action']},
                                exc_info=True)
                _retry(job, str(exc))
        return len(jobs)
    except AbsError as exc:
        connection.record_error(str(exc))
        return 0
    finally:
        _process_lock.release()


def _run(client, job: Dict[str, Any]) -> None:
    action = job['action']
    if action == 'notify':
        _notify(client, job)
    elif action == 'find_item':
        _find_item(client, job)
    elif action == 'match':
        from . import metadata
        metadata.match_book(client, job['book_id'])
        _done(job)
    else:
        _done(job, f'Unknown action {action}')


def _notify(client, job: Dict[str, Any]) -> None:
    payload = job['payload']
    library = container.get_library_repository().get(payload.get('library_id'))
    row = abs_mapping.mapping_for_library(payload.get('library_id'))
    if not library or not row:
        _done(job, 'Library is no longer mapped')
        return
    settings = connection.get_settings()
    caps = connection.capabilities()
    notified = False
    if settings['notify_on_import'] and caps.get('is_admin'):
        abs_path = abs_mapping.to_abs_path(row, library['root'], payload['path'])
        if abs_path is None:
            _done(job, 'File is outside its library')
            return
        if row['watcher_enabled']:
            old_row = abs_mapping.mapping_for_library(payload.get('old_library_id'))
            old_library = container.get_library_repository().get(payload.get('old_library_id'))
            if payload.get('old_path') and old_row and old_library and old_row['abs_library_id'] == row['abs_library_id']:
                old_abs = abs_mapping.to_abs_path(old_row, old_library['root'], payload['old_path'])
                client.watcher_update(row['abs_library_id'], abs_path, 'rename', old_path=old_abs)
            else:
                if payload.get('old_path') and old_row and old_library:
                    old_abs = abs_mapping.to_abs_path(old_row, old_library['root'], payload['old_path'])
                    if old_abs:
                        client.watcher_update(old_row['abs_library_id'], old_abs, 'unlink')
                client.watcher_update(row['abs_library_id'], abs_path, 'add')
        else:
            _request_scan(client, row['abs_library_id'])
        notified = True
    delays = FIND_DELAYS_ADMIN if notified else FIND_DELAYS_NO_NOTIFY
    enqueue('find_item', job['book_id'], {'library_id': library['id'], 'notified': notified, 'step': 0}, delays[0])
    _done(job)


def _bulk_scan_if_many(client, notify_jobs: List[Dict[str, Any]]) -> set:
    """A burst of imports into one library becomes a single library scan."""
    caps = connection.capabilities()
    if not caps.get('is_admin') or not connection.get_settings()['notify_on_import']:
        return set()
    by_abs_library: Dict[str, List[Dict[str, Any]]] = {}
    with container.get_engine().connect() as conn:
        pending = conn.execute(text(
            "SELECT payload FROM abs_jobs WHERE state = 'pending' AND action = 'notify'"
        )).fetchall()
    totals: Dict[str, int] = {}
    for (raw,) in pending:
        row = abs_mapping.mapping_for_library(json.loads(raw or '{}').get('library_id'))
        if row:
            totals[row['abs_library_id']] = totals.get(row['abs_library_id'], 0) + 1
    for job in notify_jobs:
        row = abs_mapping.mapping_for_library(job['payload'].get('library_id'))
        if row and totals.get(row['abs_library_id'], 0) > BULK_SCAN_THRESHOLD:
            by_abs_library.setdefault(row['abs_library_id'], []).append(job)
    handled = set()
    for abs_library_id, jobs in by_abs_library.items():
        _request_scan(client, abs_library_id, force=True)
        with container.get_engine().begin() as conn:
            rows = conn.execute(text(
                "SELECT id, book_id, payload FROM abs_jobs WHERE state = 'pending' AND action = 'notify'"
            )).fetchall()
            for job_id, book_id, raw in rows:
                payload = json.loads(raw or '{}')
                row = abs_mapping.mapping_for_library(payload.get('library_id'))
                if row and row['abs_library_id'] == abs_library_id:
                    conn.execute(text("UPDATE abs_jobs SET state = 'done' WHERE id = :id"), {'id': job_id})
                    conn.execute(text(
                        "INSERT INTO abs_jobs (book_id, action, payload, next_run_at) VALUES (:b, 'find_item', :p, :t)"
                    ), {'b': book_id, 'p': json.dumps({'library_id': payload.get('library_id'), 'notified': True,
                                                       'step': 0, 'scanned': True}),
                        't': _at(FIND_DELAYS_ADMIN[1])})
                    handled.add(job_id)
        handled |= {j['id'] for j in jobs}
    return handled


def _request_scan(client, abs_library_id: str, force: bool = False) -> None:
    now = time.monotonic()
    with _scan_lock:
        last = _scan_requested.get(abs_library_id, 0)
        if not force and now - last < SCAN_DEBOUNCE_SECONDS:
            return
        _scan_requested[abs_library_id] = now
    client.scan_library(abs_library_id)


def _find_item(client, job: Dict[str, Any]) -> None:
    from . import linker
    payload = job['payload']
    library = container.get_library_repository().get(payload.get('library_id'))
    row = abs_mapping.mapping_for_library(payload.get('library_id'))
    with container.get_engine().connect() as conn:
        book_row = conn.execute(text(
            "SELECT id, UPPER(COALESCE(asin, '')), title, file_path FROM books WHERE id = :id"
        ), {'id': job['book_id']}).fetchone()
    if not library or not row or not book_row or not book_row[3]:
        _done(job, 'Book or mapping no longer exists')
        return
    book = {'id': book_row[0], 'asin': book_row[1], 'title': book_row[2], 'file_path': book_row[3]}
    item = linker.find_new_item(client, book, row, library['root'])
    if item:
        if connection.get_settings()['metadata_mode'] != 'off':
            enqueue('match', book['id'], {})
        _done(job)
        return

    step = int(payload.get('step', 0)) + 1
    delays = FIND_DELAYS_ADMIN if payload.get('notified') else FIND_DELAYS_NO_NOTIFY
    # watcher/update returns 200 but does nothing when the server-wide watcher
    # is off, so fall back to a library scan once
    if (payload.get('notified') and not payload.get('scanned') and step >= SCAN_FALLBACK_AFTER_ATTEMPT
            and connection.capabilities().get('is_admin')):
        _request_scan(client, row['abs_library_id'])
        payload['scanned'] = True
    if step >= len(delays):
        _done(job, 'Not found in Audiobookshelf yet; the next link sync will try again')
        return
    payload['step'] = step
    with container.get_engine().begin() as conn:
        conn.execute(text(
            "UPDATE abs_jobs SET payload = :p, attempts = attempts + 1, next_run_at = :t WHERE id = :id"
        ), {'p': json.dumps(payload), 't': _at(delays[step]), 'id': job['id']})


# --- Bookkeeping ------------------------------------------------------------------

def _done(job: Dict[str, Any], note: Optional[str] = None) -> None:
    with container.get_engine().begin() as conn:
        conn.execute(text("UPDATE abs_jobs SET state = 'done', last_error = :e WHERE id = :id"),
                     {'e': note, 'id': job['id']})


def _retry(job: Dict[str, Any], error: str) -> None:
    attempt = job['attempts']
    if attempt >= len(RETRY_DELAYS):
        with container.get_engine().begin() as conn:
            conn.execute(text("UPDATE abs_jobs SET state = 'failed', last_error = :e WHERE id = :id"),
                         {'e': error[:500], 'id': job['id']})
        return
    _reschedule(job, RETRY_DELAYS[attempt], error)


def _reschedule(job: Dict[str, Any], delay: float, error: str, count: bool = True) -> None:
    with container.get_engine().begin() as conn:
        conn.execute(text(
            f"UPDATE abs_jobs SET {'attempts = attempts + 1, ' if count else ''}next_run_at = :t, last_error = :e "
            "WHERE id = :id"
        ), {'t': _at(delay), 'e': error[:500], 'id': job['id']})


def queue_summary() -> Dict[str, int]:
    with container.get_engine().connect() as conn:
        rows = conn.execute(text("SELECT state, COUNT(*) FROM abs_jobs GROUP BY state")).fetchall()
    return {state: count for state, count in rows}


def recent_failures(limit: int = 10) -> List[Dict[str, Any]]:
    with container.get_engine().connect() as conn:
        rows = conn.execute(text(
            "SELECT j.id, j.action, j.last_error, j.created_at, b.title FROM abs_jobs j "
            "LEFT JOIN books b ON b.id = j.book_id WHERE j.state = 'failed' ORDER BY j.id DESC LIMIT :n"
        ), {'n': limit}).fetchall()
    return [dict(zip(('id', 'action', 'error', 'created_at', 'title'), r)) for r in rows]


def prune(days: int = 14) -> None:
    with container.get_engine().begin() as conn:
        conn.execute(text(
            "DELETE FROM abs_jobs WHERE state IN ('done', 'failed') AND created_at < :t"
        ), {'t': _at(-days * 86400)})


def retry_failed() -> int:
    with container.get_engine().begin() as conn:
        return conn.execute(text(
            "UPDATE abs_jobs SET state = 'pending', attempts = 0, next_run_at = :t WHERE state = 'failed'"
        ), {'t': _at(0)}).rowcount or 0
