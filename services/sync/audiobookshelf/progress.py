"""
Module Name: progress.py
Author: AuralArchivist
Created: October 3, 2026
Last Modified: October 9, 2026
Description:
    Listening progress from Audiobookshelf: syncing it onto linked books,
    the "finished book N, here's book N+1" notifications, and the progress
    shown on the Library page and book details.

Location:
    /services/sync/audiobookshelf/progress.py
"""

import re
import time
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text

from core.logging import get_module_logger
from integrations.audiobookshelf import AbsClient

from . import connection
from services.container import container

_LOGGER = get_module_logger("Service.Audiobookshelf.Progress")

# Only finishes this recent raise a next-in-series prompt, so the first sync
# doesn't announce years of listening history
NEXT_IN_SERIES_WINDOW_SECONDS = 3 * 86400
_NUMERIC = re.compile(r'^\d+(?:\.\d+)?$')


# --- Sync -------------------------------------------------------------------------

def _users_to_sync(client: AbsClient) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """[(id, username, progress entries)] for the configured users, and a note."""
    choice = connection.get_settings()['progress_users']
    caps = connection.capabilities()
    me = {'id': caps.get('user_id'), 'username': caps.get('user')}
    if choice == 'me' or not caps.get('is_admin'):
        note = None if choice == 'me' else "Only your own progress is synced because the key's user isn't an admin"
        return [{**me, 'progress': client.my_progress()}], note
    wanted = None if choice == 'all' else {name.strip().lower() for name in choice.split(',') if name.strip()}
    users = []
    for user in client.users():
        if not user.get('isActive', True):
            continue
        if wanted is not None and (user.get('username') or '').lower() not in wanted:
            continue
        detail = client.user(user['id'])
        users.append({'id': user['id'], 'username': user.get('username'), 'progress': detail.get('mediaProgress') or []})
    return users, None


def sync_progress(client: AbsClient) -> Dict[str, Any]:
    with container.get_engine().connect() as conn:
        links = conn.execute(text("SELECT book_id, abs_item_id, abs_media_id FROM abs_links")).fetchall()
        previous = {(r[0], r[1]): bool(r[2]) for r in conn.execute(
            text("SELECT book_id, abs_user_id, is_finished FROM abs_progress"))}
    by_media = {r[2]: r[0] for r in links if r[2]}
    by_item = {r[1]: r[0] for r in links}

    users, note = _users_to_sync(client)
    finished_now: List[Tuple[int, str]] = []
    rows_written = 0
    synced_users = set()
    with container.get_engine().begin() as conn:
        for user in users:
            synced_users.add(user['id'])
            seen_books = set()
            for entry in user['progress']:
                if entry.get('episodeId'):
                    continue
                book_id = by_media.get(entry.get('mediaItemId')) or by_item.get(entry.get('libraryItemId'))
                if not book_id or book_id in seen_books:
                    continue
                seen_books.add(book_id)
                finished = bool(entry.get('isFinished'))
                conn.execute(text(
                    "INSERT INTO abs_progress (book_id, abs_user_id, abs_username, progress, position_sec, duration, "
                    "is_finished, finished_at, started_at, last_update, hidden) VALUES (:b, :u, :n, :p, :c, :d, :f, :fa, "
                    ":sa, :lu, :h) ON CONFLICT(book_id, abs_user_id) DO UPDATE SET abs_username = excluded.abs_username, "
                    "progress = excluded.progress, position_sec = excluded.position_sec, duration = excluded.duration, "
                    "is_finished = excluded.is_finished, finished_at = excluded.finished_at, "
                    "started_at = excluded.started_at, last_update = excluded.last_update, hidden = excluded.hidden"
                ), {'b': book_id, 'u': user['id'], 'n': user.get('username'), 'p': float(entry.get('progress') or 0),
                    'c': float(entry.get('currentTime') or 0), 'd': float(entry.get('duration') or 0),
                    'f': 1 if finished else 0, 'fa': entry.get('finishedAt'), 'sa': entry.get('startedAt'),
                    'lu': entry.get('lastUpdate'), 'h': 1 if entry.get('hideFromContinueListening') else 0})
                rows_written += 1
                recent = (entry.get('finishedAt') or 0) / 1000 > time.time() - NEXT_IN_SERIES_WINDOW_SECONDS
                if finished and not previous.get((book_id, user['id'])) and recent:
                    finished_now.append((book_id, user.get('username') or ''))
            # Progress removed in Audiobookshelf
            existing = conn.execute(text("SELECT book_id FROM abs_progress WHERE abs_user_id = :u"),
                                    {'u': user['id']}).fetchall()
            for (book_id,) in existing:
                if book_id not in seen_books:
                    conn.execute(text("DELETE FROM abs_progress WHERE book_id = :b AND abs_user_id = :u"),
                                 {'b': book_id, 'u': user['id']})
        if synced_users:
            placeholders = ', '.join(f':u{i}' for i in range(len(synced_users)))
            conn.execute(text(f"DELETE FROM abs_progress WHERE abs_user_id NOT IN ({placeholders})"),
                         {f'u{i}': u for i, u in enumerate(synced_users)})

    prompts = 0
    if connection.get_settings()['next_in_series']:
        for book_id, username in finished_now:
            if next_in_series(book_id, username, len(users) > 1):
                prompts += 1
    return {'users': [u.get('username') for u in users], 'entries': rows_written,
            'newly_finished': len(finished_now), 'prompts': prompts, 'note': note}


# --- Next in series ---------------------------------------------------------------

def next_in_series(book_id: int, username: str = '', name_user: bool = False) -> bool:
    """Notify about the book after one that was just finished. True when sent."""
    from services.notifications import TYPE_NEXT_IN_SERIES, notify
    with container.get_engine().connect() as conn:
        book = conn.execute(text(
            "SELECT asin, title, series, series_asin, sequence FROM books WHERE id = :id"
        ), {'id': book_id}).fetchone()
        if not book or not book[3] or not _NUMERIC.match((book[4] or '').strip()):
            return False
        current = float(book[4])
        candidates = conn.execute(text(
            "SELECT book_asin, book_title, sequence FROM series_books WHERE series_asin = :s"
        ), {'s': book[3]}).fetchall()
        nxt = _pick_next(current, candidates)
        if not nxt:
            return False
        next_asin, next_title, next_seq = nxt
        status_row = conn.execute(text(
            "SELECT library_status, release_date FROM books WHERE UPPER(asin) = UPPER(:a) LIMIT 1"
        ), {'a': next_asin}).fetchone()
        calendar = conn.execute(text(
            "SELECT release_date FROM release_calendar WHERE asin = :a"
        ), {'a': next_asin}).fetchone()
        downloading = conn.execute(text(
            "SELECT 1 FROM download_queue WHERE UPPER(book_asin) = UPPER(:a) "
            "AND status NOT IN ('IMPORTED', 'FAILED', 'CANCELLED', 'SEEDING', 'SEEDING_COMPLETE') LIMIT 1"
        ), {'a': next_asin}).fetchone()

    series = book[2] or 'the series'
    who = f"{username} finished" if name_user and username else 'You finished'
    title = f"{who} {book[1]}"
    status = (status_row[0] if status_row else '') or ''
    release = (status_row[1] if status_row else None) or (calendar[0] if calendar else None)
    link = f"/series/{book[3]}"
    if status == 'owned':
        message = f"Book {next_seq} of {series}, {next_title}, is in your library."
        link = '/library'
    elif downloading:
        message = f"Book {next_seq} of {series}, {next_title}, is downloading."
    elif status == 'wanted':
        message = f"Book {next_seq} of {series}, {next_title}, is on your Wanted list."
    elif release and str(release)[:10] > time.strftime('%Y-%m-%d'):
        message = (f"Book {next_seq} of {series}, {next_title}, comes out on {str(release)[:10]}. "
                   f"Add it to Wanted to see it on the Release Calendar.")
    else:
        message = f"Book {next_seq} of {series}, {next_title}, is out. Open the series to search for it or add it to Wanted."
    notify(TYPE_NEXT_IN_SERIES, title, message, link=link, level='info',
           dedupe_key=f"next_in_series:{next_asin}")
    return True


def _pick_next(current: float, candidates) -> Optional[Tuple[str, str, str]]:
    """The next whole-number book after current; a .5 book only if no whole one follows."""
    numeric = []
    for asin, title, seq in candidates:
        seq = (seq or '').strip()
        if _NUMERIC.match(seq) and float(seq) > current:
            numeric.append((float(seq), asin, title, seq))
    if not numeric:
        return None
    whole = [c for c in numeric if c[0].is_integer()]
    best = min(whole or numeric)
    return best[1], best[2], best[3]


# --- Display ----------------------------------------------------------------------

def _play_url(item_id: str) -> Optional[str]:
    url = connection.get_settings()['url']
    return f"{url}/item/{item_id}" if url and item_id else None


def library_page_context() -> Dict[int, Dict[str, Any]]:
    """Per linked book: progress (highest of the synced users), finished, play link."""
    if not connection.is_enabled():
        return {}
    with container.get_engine().connect() as conn:
        links = conn.execute(text("SELECT book_id, abs_item_id FROM abs_links")).fetchall()
        rows = conn.execute(text(
            "SELECT book_id, MAX(progress), MAX(is_finished) FROM abs_progress GROUP BY book_id"
        )).fetchall()
    progress = {r[0]: (r[1] or 0, bool(r[2])) for r in rows}
    context = {}
    for book_id, item_id in links:
        value, finished = progress.get(book_id, (0, False))
        state = 'finished' if finished else ('in_progress' if value > 0 else 'not_started')
        context[book_id] = {'abs_linked': True, 'abs_state': state, 'abs_progress': round(value, 4),
                            'abs_play_url': _play_url(item_id)}
    return context


def book_detail(book_id: int) -> Optional[Dict[str, Any]]:
    if not connection.is_enabled():
        return None
    from . import linker
    link = linker.link_for_book(book_id)
    with container.get_engine().connect() as conn:
        rows = conn.execute(text(
            "SELECT abs_username, progress, position_sec, duration, is_finished, finished_at, last_update "
            "FROM abs_progress WHERE book_id = :b ORDER BY last_update DESC"
        ), {'b': book_id}).fetchall()
    return {
        'linked': bool(link),
        'link_method': link and link['link_method'],
        'play_url': link and _play_url(link['abs_item_id']),
        'progress': [
            {'user': r[0], 'progress': r[1], 'position': r[2], 'duration': r[3], 'finished': bool(r[4]),
             'finished_at': r[5], 'last_update': r[6]} for r in rows
        ],
    }
