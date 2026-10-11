"""
Module Name: library_check.py
Author: AuralArchivist
Created: September 24, 2026
Last Modified: October 9, 2026
Description:
    Periodic check that keeps the database in step with the library folder.

Location:
    /services/library/library_check.py
"""

import json
import math
import os
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text

from core.database.repositories.library_status import CHANGED, transition_library_status
from core.logging import get_module_logger
from core import events

from .matcher import asin_in_names, audio_files_in, book_folders, choose_primary_file
from services.container import container

_LOGGER = get_module_logger("Service.LibraryCheck")

MASS_MISSING_FRACTION = 0.10
MISSING_ALERT_SUMMARY_OVER = 5
_REVIEW_COUNT_TTL_SECONDS = 60

_review_cache_lock = threading.Lock()
_review_cache: Dict[str, Any] = {'at': 0.0, 'count': 0}


def _notifications():
    import services.notifications as notifications
    return notifications


def sync_review_notification() -> None:
    """Keep the header's "folders need review" alert in step with the list."""
    count = count_pending_reviews(fresh=True)
    notes = _notifications()
    if count:
        notes.notify(notes.TYPE_LIBRARY_REVIEW,
                     f"{count} library folder{'s' if count != 1 else ''} need{'s' if count == 1 else ''} review",
                     "Found in your library but couldn't be imported automatically.",
                     link='/import#review', level='warning', dedupe_key='library_review',
                     reopen_dismissed=True)
    else:
        notes.resolve('library_review')


def _primary_file(audio_files: List[str]) -> Optional[str]:
    return choose_primary_file(audio_files) if audio_files else None


class LibraryCheckService:
    """One library check at a time; state lives between prepare() and finish()."""

    def __init__(self, *, logger=None):
        self.logger = logger or _LOGGER
        self._details: Dict[str, Any] = {}

    # --- Phase 1: local pass (fast, no network) ---

    def prepare(self, *, allow_mass_missing: bool = False) -> Tuple[Optional[List[Dict[str, Any]]], Dict[str, Any]]:
        """Verify / relink / mark missing across every enabled library, then
        discover new folders.

        Each library is checked on its own: an unreachable or empty root skips
        only that library, its books are never marked missing while it's
        offline, and the mass-missing guard applies per library. Returns
        (units_to_match, details); units is None when the run was skipped.
        """
        repo = container.get_library_repository()
        libraries = repo.enabled()
        details: Dict[str, Any] = {
            'library': ', '.join(lib['root'] for lib in libraries), 'checked': 0, 'verified': 0,
            'relinked': 0, 'normalized': 0, 'missing': 0, 'recovered': 0, 'still_missing': 0,
            'no_path': 0, 'new_folders': 0, 'adopted': 0, 'review': 0, 'missing_blocked': [],
            'held': 0, 'unreachable': [], 'libraries': {},
        }
        self._details = details
        books = self._load_books()
        # A new install adds its existing books through Library Import, not here
        if not books:
            details['skipped'] = "The library is empty - add existing books with Import › Library Import"
            return None, details

        reachable: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
        skipped_ids = set()
        by_asin: Dict[str, str] = {}
        for lib in libraries:
            lib_details = {'name': lib['name'], 'root': lib['root'], 'status': 'ok', 'checked': 0,
                           'relinked': 0, 'missing': 0, 'recovered': 0, 'new_folders': 0}
            details['libraries'][lib['id']] = lib_details
            root = lib['root']
            if not os.path.isdir(root) or not os.access(root, os.R_OK | os.X_OK):
                self._mark_unreachable(lib, lib_details, details, f"folder isn't reachable: {root}")
                skipped_ids.add(lib['id'])
                continue
            index = self._index_library(root)
            owned_here = sum(1 for b in books if b['library_status'] == 'owned' and b['file_path']
                             and self._book_library_id(b, repo) == lib['id'])
            if not index['units'] and owned_here:
                self._mark_unreachable(lib, lib_details, details,
                                       f"folder is empty but {owned_here} owned books point into it (is the drive mounted?)")
                skipped_ids.add(lib['id'])
                continue
            _notifications().resolve(f"library_unreachable:{lib['id']}")
            for asin, primary in index['by_asin'].items():
                by_asin.setdefault(asin, primary)
            reachable.append((lib, index))

        if not reachable:
            details['skipped'] = ("No library folder is reachable - nothing was changed"
                                  if libraries else "No libraries are enabled")
            return None, details

        path_updates: Dict[int, str] = {}
        library_updates: Dict[int, int] = {}
        moves: List[Tuple[int, str, str]] = []
        to_missing: List[Dict[str, Any]] = []
        to_recover: List[Dict[str, Any]] = []

        for book in books:
            details['checked'] += 1
            stored = book['file_path']
            if not stored:
                details['no_path'] += 1
                continue
            home_id = self._book_library_id(book, repo)
            if home_id in details['libraries']:
                details['libraries'][home_id]['checked'] += 1

            found = self._resolve_existing(stored)
            if found:
                details['verified'] += 1
                if found != stored:
                    path_updates[book['id']] = found
                    details['normalized'] += 1
            else:
                found = self._relink(book, stored, [lib['root'] for lib, _ in reachable], by_asin)
                if found:
                    path_updates[book['id']] = found
                    moves.append((book['id'], stored, found))
                    details['relinked'] += 1
                    owner = repo.library_for_path(found)
                    if owner and owner['id'] in details['libraries']:
                        details['libraries'][owner['id']]['relinked'] += 1

            if found:
                owner = repo.library_for_path(found)
                if owner and owner['id'] != book['library_id']:
                    library_updates[book['id']] = owner['id']

            if found and book['library_status'] == 'missing':
                to_recover.append(book)
            elif not found:
                if home_id in skipped_ids:
                    details['held'] += 1
                elif book['library_status'] == 'owned':
                    book['_library_id'] = home_id
                    to_missing.append(book)
                else:
                    details['still_missing'] += 1

        to_missing = self._apply_missing_guard(to_missing, books, repo, details, allow_mass_missing)

        self._apply(path_updates, to_missing, to_recover, library_updates)
        for book_id, old, new in moves:
            events.publish(events.BOOK_FILE_MOVED, book_id=book_id, old_path=old, new_path=new)
        details['missing'] = len(to_missing)
        details['recovered'] = len(to_recover)
        for book in to_missing:
            lib_id = book.get('_library_id')
            if lib_id in details['libraries']:
                details['libraries'][lib_id]['missing'] += 1

        units: List[Dict[str, Any]] = []
        for lib, _index in reachable:
            found_units = self._new_units(lib['root'])
            for unit in found_units:
                unit['library_id'] = lib['id']
            details['libraries'][lib['id']]['new_folders'] = len(found_units)
            units.extend(found_units)
        details['new_folders'] = len(units)
        # JSON keys must be strings for the stored results
        details['libraries'] = {str(k): v for k, v in details['libraries'].items()}
        return units, details

    @staticmethod
    def _book_library_id(book: Dict[str, Any], repo) -> Optional[int]:
        if book.get('library_id'):
            return book['library_id']
        owner = repo.library_for_path(book.get('file_path'))
        return owner['id'] if owner else None

    def _mark_unreachable(self, lib: Dict[str, Any], lib_details: Dict[str, Any], details: Dict[str, Any],
                          reason: str) -> None:
        lib_details['status'] = 'unreachable'
        lib_details['reason'] = reason
        details['unreachable'].append({'id': lib['id'], 'name': lib['name'], 'reason': reason})
        self.logger.warning("Library check: skipping library", extra={"library": lib['name'], "reason": reason})
        notes = _notifications()
        notes.notify(notes.TYPE_STORAGE, f"{lib['name']} library is not reachable",
                     f"The library check skipped it because its {reason}. Its books are left as they are.",
                     link='/settings#storage', level='warning', dedupe_key=f"library_unreachable:{lib['id']}")

    def _apply_missing_guard(self, to_missing: List[Dict[str, Any]], books: List[Dict[str, Any]], repo,
                             details: Dict[str, Any], allow_mass_missing: bool) -> List[Dict[str, Any]]:
        """Hold back a library's missing list when too much of it vanished at once."""
        kept: List[Dict[str, Any]] = []
        blocked: List[Dict[str, Any]] = []
        limits = 0
        by_library: Dict[Any, List[Dict[str, Any]]] = {}
        for book in to_missing:
            by_library.setdefault(book.get('_library_id'), []).append(book)
        for lib_id, candidates in by_library.items():
            owned = sum(1 for b in books if b['library_status'] == 'owned'
                        and self._book_library_id(b, repo) == lib_id)
            limit = math.floor(owned * MASS_MISSING_FRACTION)
            if len(candidates) > limit and not allow_mass_missing:
                blocked.extend(candidates)
                limits += limit
                self.logger.warning(
                    "Library check: not marking books missing - too many at once",
                    extra={"library_id": lib_id, "would_mark": len(candidates), "limit": limit, "owned": owned},
                )
            else:
                kept.extend(candidates)
        if blocked:
            details['missing_blocked'] = [
                {'id': b['id'], 'title': b['title'], 'file_path': b['file_path']} for b in blocked
            ]
            details['missing_limit'] = limits
            _notifications().notify(
                _notifications().TYPE_LIBRARY_CHECK_BLOCKED,
                "Library check held back",
                f"{len(blocked)} books would be marked missing at once (limit {limits}). "
                f"Check your library folders, then confirm in Settings > Metadata.",
                link='/settings#metadata', level='warning', dedupe_key='library_check_blocked',
                reopen_dismissed=True,
            )
        else:
            _notifications().resolve('library_check_blocked')
        return kept

    def _index_library(self, library: str) -> Dict[str, Any]:
        """Book folders (directories directly holding audio) keyed by ASIN."""
        by_asin: Dict[str, str] = {}
        units: List[str] = []
        for root, audio in book_folders(library):
            units.append(root)
            primary = choose_primary_file(audio)
            asin = asin_in_names(*(os.path.basename(p) for p in audio), os.path.basename(root))
            if asin and asin not in by_asin:
                by_asin[asin] = primary
        return {'units': units, 'by_asin': by_asin}

    @staticmethod
    def _load_books() -> List[Dict[str, Any]]:
        with container.get_engine().connect() as conn:
            rows = conn.execute(text(
                "SELECT id, asin, title, file_path, library_status, library_id FROM books "
                "WHERE library_status IN ('owned', 'missing')"
            )).fetchall()
        return [
            {'id': r[0], 'asin': (r[1] or '').strip().upper(), 'title': r[2],
             'file_path': (r[3] or '').strip(), 'library_status': r[4], 'library_id': r[5]}
            for r in rows
        ]

    @staticmethod
    def _resolve_existing(path: str) -> Optional[str]:
        """The stored path if it's a file; its main audio file if it's a book folder."""
        if os.path.isfile(path):
            return path
        if os.path.isdir(path):
            return _primary_file(audio_files_in(path))
        return None

    def _relink(self, book: Dict[str, Any], stored: str, roots: List[str], by_asin: Dict[str, str]) -> Optional[str]:
        """Find a moved book: by ASIN anywhere first, then by the longest
        trailing part of its old path (at least Series/Title) under any root."""
        if book['asin'] and book['asin'] in by_asin:
            return by_asin[book['asin']]
        parts = [p for p in stored.replace('\\', '/').split('/') if p]
        for keep in range(len(parts) - 1, 1, -1):
            for root in roots:
                found = self._resolve_existing(os.path.join(root, *parts[-keep:]))
                if found:
                    return found
        return None

    def _apply(self, path_updates: Dict[int, str], to_missing: List[Dict[str, Any]],
               to_recover: List[Dict[str, Any]], library_updates: Optional[Dict[int, int]] = None) -> None:
        library_updates = library_updates or {}
        if not (path_updates or to_missing or to_recover or library_updates):
            return
        changed = []
        with container.get_engine().begin() as conn:
            for book_id, new_path in path_updates.items():
                conn.execute(
                    text("UPDATE books SET file_path = :p, updated_at = CURRENT_TIMESTAMP WHERE id = :id"),
                    {'p': new_path, 'id': book_id},
                )
            for book_id, library_id in library_updates.items():
                conn.execute(text("UPDATE books SET library_id = :l WHERE id = :id"),
                             {'l': library_id, 'id': book_id})
            for book in to_missing:
                if transition_library_status(conn, book['id'], 'missing', current_status='owned') == CHANGED:
                    changed.append((book['id'], 'missing'))
            for book in to_recover:
                if transition_library_status(conn, book['id'], 'owned', current_status='missing') == CHANGED:
                    changed.append((book['id'], 'owned'))
        for book_id, status in changed:
            events.publish(events.BOOK_STATUS_CHANGED, book_id=book_id, status=status)
        notes = _notifications()
        for book in to_missing:
            self.logger.info("Library check: book missing", extra={"book_id": book['id'], "title": book['title']})
            if len(to_missing) <= MISSING_ALERT_SUMMARY_OVER:
                notes.notify(notes.TYPE_BOOK_MISSING, f"Missing: {book['title']}",
                             "Its files are no longer in your library folder.",
                             link='/library', level='warning', dedupe_key=f"book_missing:{book['id']}")
        if len(to_missing) > MISSING_ALERT_SUMMARY_OVER:
            titles = ', '.join(b['title'] or '?' for b in to_missing[:3])
            notes.notify(notes.TYPE_BOOK_MISSING, f"{len(to_missing)} books went missing",
                         f"Including {titles}. Their files are no longer in your library folder.",
                         link='/library', level='warning')
        for book in to_recover:
            self.logger.info("Library check: book found again", extra={"book_id": book['id'], "title": book['title']})
            notes.resolve(f"book_missing:{book['id']}")

    def _new_units(self, library: str) -> List[Dict[str, Any]]:
        """Uncatalogued book folders that aren't already on the review list."""
        units = container.get_library_import_manager().discover_book_units(library)
        known = {}
        with container.get_engine().connect() as conn:
            for path, status in conn.execute(text("SELECT folder_path, status FROM library_review")):
                known[path] = status
        fresh = []
        touched = []
        for unit in units:
            if unit['path'] in known:
                touched.append(unit['path'])
            else:
                fresh.append(unit)
        if touched:
            with container.get_engine().begin() as conn:
                for path in touched:
                    conn.execute(text("UPDATE library_review SET last_seen = CURRENT_TIMESTAMP WHERE folder_path = :p"),
                                 {'p': path})
        return fresh

    # --- Phase 2: per new folder (network - paced by the scheduler) ---

    def process_unit(self, unit: Dict[str, Any]) -> bool:
        """Match one new folder; adopt it if confident, else put it on the
        review list. Returns True when adopted."""
        manager = container.get_library_import_manager()
        try:
            result = manager.matcher.match_folder(unit)
        except Exception as exc:
            self.logger.warning("Library check: matching failed", extra={"folder": unit['path'], "error": str(exc)})
            result = {'metadata': None, 'match': {'strategy': 'error', 'confidence': None},
                      'state': 'unmatched', 'message': f'Matching error: {exc}'}
        unit.update(metadata=result['metadata'], match=result['match'], state=result['state'])

        duplicate_of = self._existing_copy(result.get('metadata'), unit['path'])
        if duplicate_of:
            result['state'] = 'duplicate'
            result['message'] = (f"Duplicate copy - {duplicate_of['title'] or 'this book'} is already in your "
                                 f"library at {os.path.dirname(duplicate_of['file_path'])}")
        elif result['state'] == 'matched':
            adopted = manager.adopt_in_place(unit)
            if adopted.get('success'):
                self._details['adopted'] = self._details.get('adopted', 0) + 1
                self.logger.info("Library check: adopted new folder",
                                 extra={"folder": unit['rel_path'] or unit['name'], "title": adopted.get('title')})
                return True
            result['message'] = adopted.get('message') or 'Automatic import failed'
            result['state'] = 'needs_review'

        self._record_review(unit, result)
        self._details['review'] = self._details.get('review', 0) + 1
        return False

    @staticmethod
    def _existing_copy(metadata: Optional[Dict[str, Any]], folder: str) -> Optional[Dict[str, Any]]:
        """The owned book with this ASIN if its files live in another folder."""
        asin = ((metadata or {}).get('ASIN') or (metadata or {}).get('asin') or '').strip()
        if not asin:
            return None
        with container.get_engine().connect() as conn:
            row = conn.execute(text(
                "SELECT title, file_path FROM books WHERE UPPER(asin) = :asin "
                "AND file_path IS NOT NULL AND TRIM(file_path) != '' LIMIT 1"
            ), {'asin': asin.upper()}).fetchone()
        if not row or not os.path.exists(row[1]):
            return None
        existing_dir = row[1] if os.path.isdir(row[1]) else os.path.dirname(row[1])
        if os.path.normpath(existing_dir) == os.path.normpath(folder):
            return None
        return {'title': row[0], 'file_path': row[1]}

    @staticmethod
    def _record_review(unit: Dict[str, Any], result: Dict[str, Any]) -> None:
        metadata = result.get('metadata') or {}
        match = result.get('match') or {}
        values = {
            'folder_path': unit['path'],
            'rel_path': unit.get('rel_path') or '',
            'name': unit.get('name') or os.path.basename(unit['path']),
            'primary_file': unit.get('primary_file'),
            'file_count': unit.get('file_count') or 0,
            'size_bytes': unit.get('size_bytes') or 0,
            'state': result.get('state') or 'unmatched',
            'suggested_title': metadata.get('Title') or metadata.get('title'),
            'suggested_author': metadata.get('Author') or metadata.get('author'),
            'suggested_asin': metadata.get('ASIN') or metadata.get('asin'),
            'confidence': match.get('confidence'),
            'strategy': match.get('strategy'),
            'metadata_json': json.dumps(metadata, default=str) if metadata else None,
            'message': result.get('message'),
            'library_id': unit.get('library_id'),
        }
        with container.get_engine().begin() as conn:
            conn.execute(text("""
                INSERT INTO library_review (folder_path, rel_path, name, primary_file, file_count, size_bytes,
                    state, status, suggested_title, suggested_author, suggested_asin, confidence, strategy,
                    metadata_json, message, library_id)
                VALUES (:folder_path, :rel_path, :name, :primary_file, :file_count, :size_bytes,
                    :state, 'pending', :suggested_title, :suggested_author, :suggested_asin, :confidence, :strategy,
                    :metadata_json, :message, :library_id)
                ON CONFLICT(folder_path) DO UPDATE SET library_id = excluded.library_id,
                    state = excluded.state, suggested_title = excluded.suggested_title,
                    suggested_author = excluded.suggested_author, suggested_asin = excluded.suggested_asin,
                    confidence = excluded.confidence, strategy = excluded.strategy,
                    metadata_json = excluded.metadata_json, message = excluded.message,
                    last_seen = CURRENT_TIMESTAMP
            """), values)
        invalidate_review_count_cache()

    # --- Phase 3: wrap-up ---

    def finish(self) -> Dict[str, Any]:
        """Drop review entries whose folder is gone or has been catalogued,
        then refresh the review alert."""
        removed = prune_review_items()
        if removed:
            self._details['review_resolved'] = removed
        sync_review_notification()
        return dict(self._details)


# --- Review list (Import page) -----------------------------------------------

_REVIEW_COLUMNS = ('id', 'folder_path', 'rel_path', 'name', 'primary_file', 'file_count', 'size_bytes',
                   'state', 'status', 'suggested_title', 'suggested_author', 'suggested_asin',
                   'confidence', 'strategy', 'message', 'first_seen', 'last_seen', 'library_id')


def list_review_items(status: str = 'pending') -> List[Dict[str, Any]]:
    with container.get_engine().connect() as conn:
        rows = conn.execute(
            text(f"SELECT {', '.join(_REVIEW_COLUMNS)} FROM library_review WHERE status = :s "
                 "ORDER BY rel_path COLLATE NOCASE"),
            {'s': status},
        ).fetchall()
    return [dict(zip(_REVIEW_COLUMNS, row)) for row in rows]


def count_pending_reviews(*, fresh: bool = False) -> int:
    """Pending review count (Import badge), cached briefly unless fresh."""
    now = time.monotonic()
    with _review_cache_lock:
        if not fresh and now - _review_cache['at'] < _REVIEW_COUNT_TTL_SECONDS:
            return _review_cache['count']
    with container.get_engine().connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM library_review WHERE status = 'pending'")).scalar() or 0
    with _review_cache_lock:
        _review_cache.update(at=now, count=int(count))
    return int(count)


def invalidate_review_count_cache() -> None:
    with _review_cache_lock:
        _review_cache['at'] = 0.0


def ignore_review_item(item_id: int) -> bool:
    with container.get_engine().begin() as conn:
        updated = conn.execute(
            text("UPDATE library_review SET status = 'ignored' WHERE id = :id AND status = 'pending'"),
            {'id': int(item_id)},
        ).rowcount
    invalidate_review_count_cache()
    sync_review_notification()
    return bool(updated)


def accept_review_item(item_id: int) -> Dict[str, Any]:
    """Adopt a review item in place using its suggested match."""
    with container.get_engine().connect() as conn:
        row = conn.execute(
            text("SELECT folder_path, primary_file, metadata_json, suggested_asin FROM library_review "
                 "WHERE id = :id AND status = 'pending'"),
            {'id': int(item_id)},
        ).fetchone()
    if not row:
        return {'success': False, 'message': 'Review item not found'}
    folder_path, primary_file, metadata_json, suggested_asin = row
    if not metadata_json or not suggested_asin:
        return {'success': False, 'message': 'No suggested match to accept - use Library Import to pick one'}
    if not primary_file or not os.path.isfile(primary_file):
        audio = audio_files_in(folder_path)
        primary_file = _primary_file(audio)
        if not primary_file:
            return {'success': False, 'message': 'The folder no longer contains audio files'}
    folder = {
        'folder_id': uuid.uuid5(uuid.NAMESPACE_URL, os.path.abspath(folder_path)).hex,
        'path': folder_path,
        'primary_file': primary_file,
        'metadata': json.loads(metadata_json),
        'monitored': True,
    }
    result = container.get_library_import_manager().adopt_in_place(folder)
    if result.get('success'):
        with container.get_engine().begin() as conn:
            conn.execute(text("DELETE FROM library_review WHERE id = :id"), {'id': int(item_id)})
        invalidate_review_count_cache()
        sync_review_notification()
    return result


def prune_review_items() -> int:
    """Remove review entries whose folder no longer exists or whose audio is
    now catalogued (e.g. adopted through Library Import)."""
    with container.get_engine().connect() as conn:
        items = conn.execute(text("SELECT id, folder_path FROM library_review")).fetchall()
        catalogued = {
            os.path.abspath(r[0]) for r in conn.execute(
                text("SELECT file_path FROM books WHERE file_path IS NOT NULL AND TRIM(file_path) != ''")
            )
        }
    stale = []
    for item_id, folder_path in items:
        if not os.path.isdir(folder_path):
            stale.append(item_id)
            continue
        if any(os.path.abspath(p) in catalogued for p in audio_files_in(folder_path)):
            stale.append(item_id)
    if stale:
        with container.get_engine().begin() as conn:
            for item_id in stale:
                conn.execute(text("DELETE FROM library_review WHERE id = :id"), {'id': item_id})
        invalidate_review_count_cache()
        sync_review_notification()
    return len(stale)
