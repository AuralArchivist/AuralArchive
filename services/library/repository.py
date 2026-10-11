"""
Module Name: repository.py
Author: AuralArchivist
Created: October 3, 2026
Last Modified: October 9, 2026
Description:
    The libraries table: list, create, update and delete libraries, validate
    their root folders, and find which library a path belongs to. The first
    library has no root_path of its own and follows the Storage library
    folder, so AURALARCHIVE_LIBRARY_DIR and [paths] library keep working.

Location:
    /services/library/repository.py
"""

import os
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from core.logging import get_module_logger
from core.paths import get_path_resolver
from services.container import container

_LOGGER = get_module_logger("Service.Libraries")

RULE_KINDS = ('language', 'author', 'series', 'narrator')
COLUMNS = ('id', 'name', 'root_path', 'naming_template', 'is_default', 'enabled', 'sort_order')


class LibraryError(ValueError):
    """A library change that was refused, with a message for the UI."""


def _real(path: str) -> str:
    return os.path.normcase(os.path.realpath(path))


def _overlaps(a: str, b: str) -> bool:
    """True when one folder is the other or sits inside it."""
    ra, rb = _real(a), _real(b)
    try:
        common = os.path.commonpath([ra, rb])
    except ValueError:
        return False
    return common in (ra, rb)


class LibraryRepository:

    def __init__(self, *, logger=None):
        self.logger = logger or _LOGGER

    # --- Reading ---

    def all(self) -> List[Dict[str, Any]]:
        with container.get_engine().connect() as conn:
            rows = conn.execute(text(
                f"SELECT {', '.join(COLUMNS)} FROM libraries ORDER BY sort_order, id"
            )).fetchall()
        return [self._row(r) for r in rows]

    def enabled(self) -> List[Dict[str, Any]]:
        return [lib for lib in self.all() if lib['enabled']]

    def get(self, library_id: Optional[int]) -> Optional[Dict[str, Any]]:
        if library_id is None:
            return None
        for lib in self.all():
            if lib['id'] == int(library_id):
                return lib
        return None

    def default(self) -> Dict[str, Any]:
        libraries = self.all()
        for lib in libraries:
            if lib['is_default']:
                return lib
        if libraries:
            return libraries[0]
        # Only before migration 25 has run
        return {'id': None, 'name': 'Main', 'root_path': None, 'naming_template': None,
                'is_default': True, 'enabled': True, 'sort_order': 0,
                'root': get_path_resolver().get_library_dir(), 'uses_storage_path': True}

    def count(self) -> int:
        return len(self.all())

    def library_for_path(self, path: Optional[str]) -> Optional[Dict[str, Any]]:
        """The library whose root holds path (longest root wins)."""
        if not path:
            return None
        best, best_len = None, -1
        real = _real(path)
        for lib in self.all():
            root = _real(lib['root'])
            if real == root or real.startswith(root.rstrip(os.sep) + os.sep):
                if len(root) > best_len:
                    best, best_len = lib, len(root)
        return best

    def book_counts(self) -> Dict[int, int]:
        with container.get_engine().connect() as conn:
            rows = conn.execute(text(
                "SELECT library_id, COUNT(*) FROM books WHERE library_id IS NOT NULL "
                "AND library_status IN ('owned', 'missing') GROUP BY library_id"
            )).fetchall()
        return {r[0]: r[1] for r in rows}

    @staticmethod
    def _row(row) -> Dict[str, Any]:
        lib = dict(zip(COLUMNS, row))
        lib['is_default'] = bool(lib['is_default'])
        lib['enabled'] = bool(lib['enabled'])
        lib['uses_storage_path'] = not lib['root_path']
        lib['root'] = lib['root_path'] or get_path_resolver().get_library_dir()
        return lib

    # --- Validation ---

    def validate_root(self, root: str, *, exclude_id: Optional[int] = None) -> str:
        """Check a new root folder. Returns the normalised path or raises LibraryError."""
        root = os.path.normpath(os.path.expanduser((root or '').strip()))
        if not root or root == '.':
            raise LibraryError('Choose a folder for this library')
        if not os.path.isabs(root):
            raise LibraryError(f'The folder must be an absolute path: {root}')
        if _real(root) in (_real('/'), _real(os.path.expanduser('~'))):
            raise LibraryError("A library can't be the root of the disk or your home folder")
        if not os.path.isdir(root):
            try:
                os.makedirs(root, exist_ok=True)
            except OSError as exc:
                raise LibraryError(f"The folder doesn't exist and couldn't be created: {exc}")
        if not os.access(root, os.R_OK | os.W_OK | os.X_OK):
            raise LibraryError(f"AuralArchive can't read and write this folder: {root}")

        resolver = get_path_resolver()
        for name in ('downloads', 'import', 'work'):
            other = resolver.get_path_info(name)['path']
            if _overlaps(root, other):
                raise LibraryError(f"A library can't be inside, or contain, the {name} folder ({other})")
        data_dir = resolver.get_data_dir()
        if _overlaps(root, data_dir) and not _overlaps(root, resolver.get_media_dir()):
            raise LibraryError("A library can't be inside the app data folder")
        for lib in self.all():
            if exclude_id is not None and lib['id'] == exclude_id:
                continue
            if _overlaps(root, lib['root']):
                raise LibraryError(f"This folder overlaps the {lib['name']} library ({lib['root']})")

        if resolver.is_docker():
            from .storage_status import mount_point_for
            if not mount_point_for(root):
                raise LibraryError("This folder isn't mounted from the host, so its books would be lost "
                                   "when the container is recreated. Add a volume for it first.")
        return root

    # --- Writing ---

    def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        name = (data.get('name') or '').strip()
        if not name:
            raise LibraryError('Give the library a name')
        if any(lib['name'].lower() == name.lower() for lib in self.all()):
            raise LibraryError(f'A library called "{name}" already exists')
        root = self.validate_root(data.get('root_path') or '')
        template = (data.get('naming_template') or '').strip() or None
        with container.get_engine().begin() as conn:
            order = conn.execute(text("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM libraries")).scalar()
            new_id = conn.execute(text(
                "INSERT INTO libraries (name, root_path, naming_template, enabled, sort_order) "
                "VALUES (:n, :r, :t, :e, :o)"
            ), {'n': name, 'r': root, 't': template, 'e': 0 if data.get('enabled') is False else 1,
                'o': order}).lastrowid
            if data.get('is_default'):
                self._set_default(conn, new_id)
        self.logger.info("Library created", extra={"library": name, "root": root})
        return self.get(new_id)

    def update(self, library_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
        lib = self.get(library_id)
        if not lib:
            raise LibraryError('Library not found')
        fields: Dict[str, Any] = {}
        if 'name' in data:
            name = (data.get('name') or '').strip()
            if not name:
                raise LibraryError('Give the library a name')
            if any(o['name'].lower() == name.lower() and o['id'] != lib['id'] for o in self.all()):
                raise LibraryError(f'A library called "{name}" already exists')
            fields['name'] = name
        if 'root_path' in data and not lib['uses_storage_path']:
            root = self.validate_root(data.get('root_path') or '', exclude_id=lib['id'])
            if _real(root) != _real(lib['root']) and self._has_books(lib['id']):
                raise LibraryError('Move or reassign its books before changing the folder of a library that has books')
            fields['root_path'] = root
        if 'naming_template' in data:
            fields['naming_template'] = (data.get('naming_template') or '').strip() or None
        if 'enabled' in data:
            if not data['enabled'] and lib['is_default']:
                raise LibraryError("The default library can't be disabled")
            fields['enabled'] = 1 if data['enabled'] else 0
        with container.get_engine().begin() as conn:
            if fields:
                sets = ', '.join(f"{k} = :{k}" for k in fields)
                conn.execute(text(f"UPDATE libraries SET {sets}, updated_at = CURRENT_TIMESTAMP WHERE id = :id"),
                             {**fields, 'id': lib['id']})
            if data.get('is_default') and not lib['is_default']:
                if not (fields.get('enabled', 1 if lib['enabled'] else 0)):
                    raise LibraryError("A disabled library can't be the default")
                self._set_default(conn, lib['id'])
        return self.get(lib['id'])

    def delete(self, library_id: int, reassign_to: Optional[int] = None) -> None:
        lib = self.get(library_id)
        if not lib:
            raise LibraryError('Library not found')
        if lib['is_default']:
            raise LibraryError('Make another library the default before deleting this one')
        if lib['uses_storage_path']:
            raise LibraryError('This library uses the Storage library folder and can\'t be deleted')
        target = self.get(reassign_to) if reassign_to is not None else None
        if self._has_books(lib['id']) and not target:
            raise LibraryError('This library has books. Choose a library to reassign them to (files are not moved)')
        with container.get_engine().begin() as conn:
            if target:
                for table, column in (('books', 'library_id'), ('library_review', 'library_id'),
                                      ('download_queue', 'target_library_id')):
                    conn.execute(text(f"UPDATE {table} SET {column} = :t WHERE {column} = :id"),
                                 {'t': target['id'], 'id': lib['id']})
            conn.execute(text("DELETE FROM library_rules WHERE library_id = :id"), {'id': lib['id']})
            conn.execute(text("DELETE FROM abs_library_map WHERE library_id = :id"), {'id': lib['id']})
            conn.execute(text("DELETE FROM libraries WHERE id = :id"), {'id': lib['id']})
        self.logger.info("Library deleted", extra={"library": lib['name'], "reassigned_to": target and target['name']})

    def reorder(self, ids: List[int]) -> None:
        with container.get_engine().begin() as conn:
            for order, library_id in enumerate(ids):
                conn.execute(text("UPDATE libraries SET sort_order = :o WHERE id = :id"),
                             {'o': order, 'id': int(library_id)})

    @staticmethod
    def _set_default(conn, library_id: int) -> None:
        conn.execute(text("UPDATE libraries SET is_default = CASE WHEN id = :id THEN 1 ELSE 0 END"),
                     {'id': library_id})

    @staticmethod
    def _has_books(library_id: int) -> bool:
        with container.get_engine().connect() as conn:
            return bool(conn.execute(text(
                "SELECT 1 FROM books WHERE library_id = :id AND library_status IN ('owned', 'missing') LIMIT 1"
            ), {'id': library_id}).fetchone())

    def assign_book(self, book_id: int, library_id: Optional[int], conn=None) -> None:
        stmt = text("UPDATE books SET library_id = :l WHERE id = :id")
        if conn is not None:
            conn.execute(stmt, {'l': library_id, 'id': book_id})
            return
        with container.get_engine().begin() as c:
            c.execute(stmt, {'l': library_id, 'id': book_id})

    def repair_assignments(self) -> int:
        """Set library_id from each book's file path. Returns books changed."""
        changed = 0
        with container.get_engine().connect() as conn:
            rows = conn.execute(text(
                "SELECT id, file_path, library_id FROM books WHERE file_path IS NOT NULL AND TRIM(file_path) != ''"
            )).fetchall()
        updates = []
        for book_id, path, current in rows:
            lib = self.library_for_path(path)
            if lib and lib['id'] != current:
                updates.append((book_id, lib['id']))
        if updates:
            with container.get_engine().begin() as conn:
                for book_id, library_id in updates:
                    self.assign_book(book_id, library_id, conn)
            changed = len(updates)
        return changed

    # --- Rules ---

    def rules(self) -> List[Dict[str, Any]]:
        with container.get_engine().connect() as conn:
            rows = conn.execute(text(
                "SELECT id, library_id, kind, value, priority FROM library_rules ORDER BY priority, id"
            )).fetchall()
        return [dict(zip(('id', 'library_id', 'kind', 'value', 'priority'), r)) for r in rows]

    def replace_rules(self, rules: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        known = {lib['id'] for lib in self.all()}
        clean = []
        for index, rule in enumerate(rules or []):
            kind = (rule.get('kind') or '').strip().lower()
            value = (rule.get('value') or '').strip()
            try:
                library_id = int(rule.get('library_id'))
            except (TypeError, ValueError):
                raise LibraryError(f'Rule {index + 1}: choose a library')
            if kind not in RULE_KINDS:
                raise LibraryError(f'Rule {index + 1}: unknown rule type "{kind}"')
            if not value:
                raise LibraryError(f'Rule {index + 1}: enter a value')
            if library_id not in known:
                raise LibraryError(f'Rule {index + 1}: that library no longer exists')
            clean.append({'library_id': library_id, 'kind': kind, 'value': value, 'priority': index})
        with container.get_engine().begin() as conn:
            conn.execute(text("DELETE FROM library_rules"))
            for rule in clean:
                conn.execute(text(
                    "INSERT INTO library_rules (library_id, kind, value, priority) VALUES (:library_id, :kind, :value, :priority)"
                ), rule)
        return self.rules()
