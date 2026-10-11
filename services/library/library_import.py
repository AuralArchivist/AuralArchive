"""
Module Name: library_import.py
Author: AuralArchivist
Created: July 6, 2026
Last Modified: October 9, 2026
Description:
    Library Import: scan an organized library and adopt matched books in place.

Location:
    /services/library/library_import.py
"""

import os
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Set

from core.logging import get_module_logger
from core.paths import ensure_within_directory

from .files import record_book_file
from .matcher import BookFileMatcher, book_folders, choose_primary_file
from services.container import container

_LOGGER = get_module_logger("Service.LibraryImport")

SESSION_TTL_SECONDS = 6 * 60 * 60

MAX_CONCURRENT_SCANS = 1

MIN_FOLDER_PROCESS_INTERVAL_SECONDS = 1.0
_CANCEL_CHECK_TICK_SECONDS = 0.05


class LibraryImportBusyError(Exception):
    """Raised when a scan is requested while another is already running."""

    def __init__(self, running_session_id: str) -> None:
        self.running_session_id = running_session_id
        super().__init__(
            f"A library scan is already in progress (session {running_session_id}); "
            "cancel it or wait for it to finish before starting another."
        )


class LibraryImportManager:
    """Owns library-import scan sessions and their background workers."""

    def __init__(self, *, logger=None) -> None:
        self.logger = logger or _LOGGER
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._matcher: Optional[BookFileMatcher] = None
        self._active_scan_ids: Set[str] = set()

    # --- Lazy dependencies ---

    @property
    def matcher(self) -> BookFileMatcher:
        """The shared on-disk matcher, created on first use."""
        if self._matcher is None:
            self._matcher = BookFileMatcher(logger=self.logger)
        return self._matcher

    def resolve_library_path(self, override: Optional[str] = None, library_id: Optional[int] = None) -> str:
        """Determine the directory to scan.

        Defaults to the chosen (or default) library's root. An override may
        only narrow the scan to a folder inside one of the libraries (raises
        ValueError otherwise), so a client can't enumerate arbitrary
        directories on the server.
        """
        repo = container.get_library_repository()
        library = repo.get(library_id) if library_id not in (None, '') else None
        if library_id not in (None, '') and not library:
            raise ValueError('That library no longer exists')

        if override and override.strip():
            owner = library or repo.library_for_path(override.strip()) or repo.default()
            return ensure_within_directory(override.strip(), owner['root'], what='library_path')
        return (library or repo.default())['root']

    # --- Scan lifecycle ---

    def start_scan(self, library_path: Optional[str] = None, library_id: Optional[int] = None) -> Dict[str, Any]:
        """Discover book folders and kick off background matching."""
        with self._lock:
            if len(self._active_scan_ids) >= MAX_CONCURRENT_SCANS:
                running_id = next(iter(self._active_scan_ids))
                self.logger.info(
                    "Rejected library scan request - another scan is already running",
                    extra={"running_session_id": running_id},
                )
                raise LibraryImportBusyError(running_id)

        resolved = self.resolve_library_path(library_path, library_id)
        session_id = str(uuid.uuid4())
        self.logger.info("Starting library scan", extra={"library_path": resolved, "session_id": session_id})

        session: Dict[str, Any] = {
            'session_id': session_id,
            'library_path': resolved,
            'state': 'scanning',
            'error': None,
            'folders': {},
            'order': [],
            'cancel_requested': False,
            'created_at': time.time(),
            'updated_at': time.time(),
        }
        with self._lock:
            self._sessions[session_id] = session
            self._cleanup_sessions_locked()

        if not os.path.isdir(resolved):
            self.logger.warning("Library scan aborted - path does not exist", extra={"library_path": resolved})
            session['state'] = 'error'
            session['error'] = f'Library path does not exist or is not a directory: {resolved}'
            session['updated_at'] = time.time()
            return self._serialize_session(session)

        try:
            book_units = self.discover_book_units(resolved)
        except Exception as exc:
            self.logger.exception("Library scan failed for %s", resolved)
            session['state'] = 'error'
            session['error'] = f'Scan failed: {exc}'
            session['updated_at'] = time.time()
            return self._serialize_session(session)

        with self._lock:
            for unit in book_units:
                session['folders'][unit['folder_id']] = unit
                session['order'].append(unit['folder_id'])
            session['state'] = 'processing' if book_units else 'ready'
            session['updated_at'] = time.time()

        if book_units:
            self.logger.info(
                "Library scan discovered unmapped book folders",
                extra={"library_path": resolved, "session_id": session_id, "folder_count": len(book_units)},
            )
        else:
            self.logger.info(
                "Library scan found no new folders - library already up to date",
                extra={"library_path": resolved, "session_id": session_id},
            )

        if book_units:
            with self._lock:
                self._active_scan_ids.add(session_id)
            worker = threading.Thread(
                target=self._process_session,
                args=(session_id,),
                name=f"library-import-{session_id[:8]}",
                daemon=True,
            )
            worker.start()

        return self._serialize_session(session)

    def discover_book_units(self, library_path: str) -> List[Dict[str, Any]]:
        """Find every folder that directly contains audio files (a book unit),
        skipping any whose files are already cataloged (unmapped-folder diff)."""
        existing_paths = self._existing_file_paths()
        units: List[Dict[str, Any]] = []

        for root, audio_files in book_folders(library_path):
            if any(os.path.abspath(p) in existing_paths for p in audio_files):
                self.logger.debug("Skipping already-cataloged folder: %s", root)
                continue

            primary = choose_primary_file(audio_files)
            total_size = 0
            for path in audio_files:
                try:
                    total_size += os.path.getsize(path)
                except OSError:
                    continue

            rel_path = os.path.relpath(root, library_path)
            rel_path = '' if rel_path in ('.', './') else rel_path

            units.append({
                'folder_id': uuid.uuid5(uuid.NAMESPACE_URL, os.path.abspath(root)).hex,
                'path': os.path.abspath(root),
                'rel_path': rel_path,
                'name': os.path.basename(root.rstrip(os.sep)) or root,
                'audio_files': audio_files,
                'primary_file': primary,
                'file_count': len(audio_files),
                'size_bytes': total_size,
                'state': 'unsearched',
                'metadata': None,
                'match': {'strategy': None, 'confidence': None},
                'selected': False,
                'monitored': True,
                'messages': [],
            })

        units.sort(key=lambda u: u['rel_path'].lower() or u['name'].lower())
        return units

    def _existing_file_paths(self) -> Set[str]:
        """All file_paths already recorded in the library, absolute."""
        paths: Set[str] = set()
        try:
            books = container.get_database_service().books.get_all_books() or []
        except Exception as exc:
            self.logger.warning("Unable to load existing books for diff: %s", exc)
            return paths
        for book in books:
            fp = book.get('file_path') or book.get('File Path')
            if fp:
                paths.add(os.path.abspath(str(fp)))
        return paths

    # --- Background matching worker (per-folder state machine) ---

    def _process_session(self, session_id: str) -> None:
        try:
            self._run_match_pump(session_id)
        except Exception as exc:
            self.logger.exception("Library import worker crashed for session %s", session_id)
            with self._lock:
                session = self._sessions.get(session_id)
                if session:
                    session['state'] = 'error'
                    session['error'] = f'Matching worker crashed: {exc}'
                    session['updated_at'] = time.time()
        finally:
            with self._lock:
                self._active_scan_ids.discard(session_id)

    def _run_match_pump(self, session_id: str) -> None:
        """Sequentially match one folder at a time, pacing between each so a
        large library can't hammer the DB or external metadata APIs."""
        while True:
            with self._lock:
                session = self._sessions.get(session_id)
                if not session or session.get('cancel_requested'):
                    if session:
                        session['state'] = 'cancelled' if session.get('cancel_requested') else session['state']
                        session['updated_at'] = time.time()
                    return
                folder = self._next_unsearched_locked(session)
                if folder is None:
                    session['state'] = 'ready'
                    session['updated_at'] = time.time()
                    raw_folders = [session['folders'][fid] for fid in session['order'] if fid in session['folders']]
                    self.logger.info(
                        "Library scan matching complete",
                        extra={"session_id": session_id, **self._summarize(raw_folders)},
                    )
                    return
                folder['state'] = 'processing'
                session['updated_at'] = time.time()

            try:
                result = self.matcher.match_folder(folder)
            except Exception as exc:
                self.logger.warning("Matching failed for %s: %s", folder['path'], exc)
                result = {
                    'metadata': None,
                    'match': {'strategy': 'error', 'confidence': None},
                    'state': 'unmatched',
                    'message': f'Matching error: {exc}',
                }

            with self._lock:
                folder['metadata'] = result['metadata']
                folder['match'] = result['match']
                folder['state'] = result['state']
                folder['selected'] = result['state'] in ('matched', 'needs_review')
                if result.get('message'):
                    folder['messages'].append(result['message'])
                session['updated_at'] = time.time()

            matched_title = (result['metadata'] or {}).get('Title') if result['metadata'] else None
            self.logger.info(
                "Matched library folder",
                extra={
                    "session_id": session_id,
                    "folder": folder['rel_path'] or folder['name'],
                    "result": result['state'],
                    "title": matched_title,
                    "strategy": (result['match'] or {}).get('strategy'),
                },
            )

            if self._wait_for_next_tick(session_id):
                return

    def _wait_for_next_tick(self, session_id: str) -> bool:
        """Pace the pump between folders. Returns True if a cancel arrived
        mid-wait so the caller can stop immediately instead of finishing the
        full pacing interval."""
        waited = 0.0
        while waited < MIN_FOLDER_PROCESS_INTERVAL_SECONDS:
            with self._lock:
                session = self._sessions.get(session_id)
                if not session or session.get('cancel_requested'):
                    if session:
                        session['state'] = 'cancelled'
                        session['updated_at'] = time.time()
                    return True
            time.sleep(_CANCEL_CHECK_TICK_SECONDS)
            waited += _CANCEL_CHECK_TICK_SECONDS
        return False

    def _next_unsearched_locked(self, session: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        for folder_id in session['order']:
            folder = session['folders'].get(folder_id)
            if folder and folder['state'] == 'unsearched':
                return folder
        return None

    # --- Manual re-match (assign) ---

    def assign_match(self, session_id: str, folder_id: str,
                    metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Apply a user-chosen match to a folder, keeping it in place."""
        with self._lock:
            session = self._require_session_locked(session_id)
            folder = session['folders'].get(folder_id)
            if not folder:
                raise KeyError(folder_id)

            applied = dict(metadata or {})
            applied['file_path'] = folder['primary_file']
            folder['metadata'] = applied
            folder['match'] = {'strategy': 'manual_assign', 'confidence': 1.0}
            folder['state'] = 'matched'
            folder['selected'] = True
            folder['messages'] = []
            session['updated_at'] = time.time()
            return self._serialize_folder(folder)

    def update_folder_flags(self, session_id: str, folder_id: str,
                           selected: Optional[bool] = None,
                           monitored: Optional[bool] = None) -> Dict[str, Any]:
        with self._lock:
            session = self._require_session_locked(session_id)
            folder = session['folders'].get(folder_id)
            if not folder:
                raise KeyError(folder_id)
            if selected is not None:
                folder['selected'] = bool(selected)
            if monitored is not None:
                folder['monitored'] = bool(monitored)
            session['updated_at'] = time.time()
            return self._serialize_folder(folder)

    def cancel_scan(self, session_id: str) -> Dict[str, Any]:
        self.logger.info("Cancelling library scan", extra={"session_id": session_id})
        with self._lock:
            session = self._require_session_locked(session_id)
            session['cancel_requested'] = True
            if session['state'] == 'processing':
                session['state'] = 'cancelling'
            session['updated_at'] = time.time()
            return self._serialize_session(session)

    # --- Commit (adopt in place) ---

    def import_selected(self, session_id: str,
                       folder_ids: Optional[List[str]] = None) -> Dict[str, Any]:
        """Catalog the chosen book folders in place. No files are moved."""
        with self._lock:
            session = self._require_session_locked(session_id)
            targets = self._select_import_targets_locked(session, folder_ids)
            for folder in targets:
                folder['state'] = 'importing'
            session['state'] = 'importing'
            session['updated_at'] = time.time()

        self.logger.info(
            "Importing selected library folders in place",
            extra={"session_id": session_id, "folder_count": len(targets)},
        )

        results: List[Dict[str, Any]] = []
        success = 0
        failure = 0
        for folder in targets:
            outcome = self.adopt_in_place(folder)
            with self._lock:
                folder['state'] = 'imported' if outcome['success'] else 'failed'
                if outcome.get('message'):
                    folder['messages'].append(outcome['message'])
                session['updated_at'] = time.time()
            results.append(outcome)
            if outcome['success']:
                success += 1
            else:
                failure += 1

        with self._lock:
            session['state'] = 'ready'
            session['updated_at'] = time.time()

        if success:
            try:
                from .library_check import prune_review_items
                prune_review_items()
            except Exception as exc:
                self.logger.debug("Review list prune skipped: %s", exc)

        self.logger.info(
            "Library import commit finished",
            extra={"session_id": session_id, "successful": success, "failed": failure, "total": len(targets)},
        )

        return {
            'summary': {'total': len(targets), 'successful': success, 'failed': failure},
            'results': results,
        }

    def _select_import_targets_locked(self, session: Dict[str, Any],
                                     folder_ids: Optional[List[str]]) -> List[Dict[str, Any]]:
        importable = {'matched', 'needs_review'}
        if folder_ids:
            requested = set(folder_ids)
            return [
                session['folders'][fid]
                for fid in session['order']
                if fid in requested
                and fid in session['folders']
                and session['folders'][fid]['state'] in importable
                and session['folders'][fid].get('metadata')
            ]
        return [
            session['folders'][fid]
            for fid in session['order']
            if session['folders'][fid]['state'] in importable
            and session['folders'][fid].get('selected')
            and session['folders'][fid].get('metadata')
        ]

    def adopt_in_place(self, folder: Dict[str, Any]) -> Dict[str, Any]:
        """Write the book to the DB at its current on-disk location."""
        metadata = dict(folder.get('metadata') or {})
        primary = folder['primary_file']
        metadata['file_path'] = primary

        result: Dict[str, Any] = {
            'folder_id': folder['folder_id'],
            'path': folder['path'],
            'title': metadata.get('Title') or metadata.get('title'),
            'asin': metadata.get('ASIN') or metadata.get('asin'),
            'success': False,
            'message': None,
        }

        db_service = container.get_database_service()
        try:
            db_service.books.add_book(metadata, library_status='owned')
        except Exception as exc:
            self.logger.error("Failed to add %s to the library", primary, exc_info=True)
            result['message'] = f'Failed to add book: {exc}'
            return result

        asin = metadata.get('ASIN') or metadata.get('asin')
        if not asin:
            result['message'] = 'Book added but no ASIN available to finalize import info.'
            result['success'] = True
            return result

        try:
            from services.processing.filing.database_operations import ImportDatabaseOperations
            file_size = 0
            try:
                file_size = os.path.getsize(primary)
            except OSError:
                pass
            file_format = os.path.splitext(primary)[1].lstrip('.').upper() or 'UNKNOWN'

            ImportDatabaseOperations(logger=self.logger).update_book_import_info(
                asin=asin,
                file_path=primary,
                file_size=file_size,
                file_format=file_format,
                file_quality='unknown',
                naming_template='in_place',
                database_service=db_service,
                source_label='library_import',
            )
        except Exception as exc:
            self.logger.warning("Book %s added but its file info was not saved", asin, exc_info=True)
            result['message'] = f'Book added but finalizing import info failed: {exc}'
            result['success'] = True
            return result

        try:
            if not folder.get('monitored', True):
                self._set_monitored(db_service, asin, False)
        except Exception:
            self.logger.warning("Could not mark %s unmonitored; it will stay monitored", asin, exc_info=True)

        record_book_file(asin, primary)
        result['success'] = True
        return result

    @staticmethod
    def _set_monitored(db_service, asin: str, monitored: bool) -> None:
        from sqlalchemy import text
        engine = db_service.connection_manager.get_engine()
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE books SET monitored = :m WHERE asin = :asin"),
                {"m": 1 if monitored else 0, "asin": asin},
            )

    # --- Session access / serialization ---

    def get_session(self, session_id: str) -> Dict[str, Any]:
        with self._lock:
            session = self._require_session_locked(session_id)
            return self._serialize_session(session)

    def _require_session_locked(self, session_id: str) -> Dict[str, Any]:
        session = self._sessions.get(session_id)
        if not session:
            raise KeyError(session_id)
        session['accessed_at'] = time.time()
        return session

    def _cleanup_sessions_locked(self) -> None:
        now = time.time()
        expired = [
            sid for sid, s in self._sessions.items()
            if now - (s.get('accessed_at') or s.get('updated_at') or s.get('created_at') or now) > SESSION_TTL_SECONDS
        ]
        for sid in expired:
            self._sessions.pop(sid, None)

    def _serialize_session(self, session: Dict[str, Any]) -> Dict[str, Any]:
        folders = [self._serialize_folder(session['folders'][fid])
                   for fid in session['order'] if fid in session['folders']]
        return {
            'session_id': session['session_id'],
            'library_path': session['library_path'],
            'state': session['state'],
            'error': session.get('error'),
            'created_at': session.get('created_at'),
            'updated_at': session.get('updated_at'),
            'summary': self._summarize(folders),
            'folders': folders,
        }

    @staticmethod
    def _serialize_folder(folder: Dict[str, Any]) -> Dict[str, Any]:
        metadata = folder.get('metadata') or {}
        return {
            'folder_id': folder['folder_id'],
            'path': folder['path'],
            'rel_path': folder['rel_path'],
            'name': folder['name'],
            'file_count': folder['file_count'],
            'size_bytes': folder['size_bytes'],
            'state': folder['state'],
            'selected': folder['selected'],
            'monitored': folder['monitored'],
            'match': folder.get('match') or {'strategy': None, 'confidence': None},
            'messages': folder.get('messages', []),
            'book': {
                'asin': metadata.get('ASIN') or metadata.get('asin'),
                'title': metadata.get('Title') or metadata.get('title'),
                'author': metadata.get('Author') or metadata.get('author'),
                'narrator': metadata.get('Narrator') or metadata.get('narrator'),
                'series': metadata.get('Series') or metadata.get('series'),
                'sequence': metadata.get('Sequence') or metadata.get('sequence'),
                'cover_image': metadata.get('Cover Image') or metadata.get('cover_image'),
                'source': metadata.get('source'),
            } if metadata else None,
        }

    @staticmethod
    def _summarize(folders: List[Dict[str, Any]]) -> Dict[str, int]:
        summary = {
            'total': len(folders),
            'unsearched': 0, 'processing': 0, 'matched': 0,
            'needs_review': 0, 'unmatched': 0, 'importing': 0,
            'imported': 0, 'failed': 0, 'skipped': 0,
            'selected': 0,
        }
        for folder in folders:
            state = folder['state']
            summary[state] = summary.get(state, 0) + 1
            if folder['selected']:
                summary['selected'] += 1
        return summary
