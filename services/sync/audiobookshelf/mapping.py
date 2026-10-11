"""
Module Name: mapping.py
Author: AuralArchivist
Created: October 3, 2026
Last Modified: October 9, 2026
Description:
    Which Audiobookshelf library and folder each AuralArchive library maps
    to. Paths are matched by the part after the library root (Audiobookshelf
    calls it relPath) because the two apps usually see different mounts.

Location:
    /services/sync/audiobookshelf/mapping.py
"""

import os
from collections import Counter
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from core.logging import get_module_logger
from integrations.audiobookshelf import AbsClient
from services.container import container

_LOGGER = get_module_logger("Service.Audiobookshelf.Mapping")

COLUMNS = ('library_id', 'abs_library_id', 'abs_folder_id', 'abs_folder_path', 'watcher_enabled')


def get_map() -> List[Dict[str, Any]]:
    with container.get_engine().connect() as conn:
        rows = conn.execute(text(f"SELECT {', '.join(COLUMNS)} FROM abs_library_map")).fetchall()
    return [dict(zip(COLUMNS, r)) for r in rows]


def mapping_for_library(library_id: Optional[int]) -> Optional[Dict[str, Any]]:
    for row in get_map():
        if row['library_id'] == library_id:
            return row
    return None


def save_map(client: AbsClient, entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Replace the mapping. Entries without an abs_library_id unmap that library."""
    known = {lib['id'] for lib in container.get_library_repository().all()}
    abs_libraries = {lib['id']: lib for lib in client.libraries()}
    server_watcher_off = False
    rows = []
    for entry in entries or []:
        library_id = int(entry.get('library_id'))
        abs_library_id = (entry.get('abs_library_id') or '').strip()
        if library_id not in known or not abs_library_id:
            continue
        lib = abs_libraries.get(abs_library_id)
        if not lib:
            raise ValueError('That Audiobookshelf library no longer exists')
        folders = lib.get('folders') or []
        folder = next((f for f in folders if f.get('id') == entry.get('abs_folder_id')), folders[0] if folders else None)
        if not folder:
            raise ValueError(f"The Audiobookshelf library {lib.get('name')} has no folders")
        rows.append({
            'library_id': library_id, 'abs_library_id': abs_library_id, 'abs_folder_id': folder.get('id'),
            'abs_folder_path': (folder.get('fullPath') or folder.get('path') or '').rstrip('/'),
            'watcher_enabled': 0 if (server_watcher_off or (lib.get('settings') or {}).get('disableWatcher')) else 1,
        })
    with container.get_engine().begin() as conn:
        conn.execute(text("DELETE FROM abs_library_map"))
        for row in rows:
            conn.execute(text(
                "INSERT INTO abs_library_map (library_id, abs_library_id, abs_folder_id, abs_folder_path, watcher_enabled) "
                "VALUES (:library_id, :abs_library_id, :abs_folder_id, :abs_folder_path, :watcher_enabled)"
            ), row)
    return get_map()


def refresh_watcher_state(client: AbsClient) -> None:
    """Re-read each mapped library's watcher setting (it decides watcher/update vs scan)."""
    rows = get_map()
    if not rows:
        return
    libraries = {lib['id']: lib for lib in client.libraries()}
    with container.get_engine().begin() as conn:
        for row in rows:
            lib = libraries.get(row['abs_library_id'])
            if not lib:
                continue
            enabled = 0 if (lib.get('settings') or {}).get('disableWatcher') else 1
            if enabled != row['watcher_enabled']:
                conn.execute(text("UPDATE abs_library_map SET watcher_enabled = :w WHERE library_id = :id"),
                             {'w': enabled, 'id': row['library_id']})


def suggest(client: AbsClient) -> Dict[int, Dict[str, Any]]:
    """For each AuralArchive library, the Audiobookshelf library and folder
    sharing the most ASINs with it."""
    with container.get_engine().connect() as conn:
        rows = conn.execute(text(
            "SELECT library_id, UPPER(asin) FROM books WHERE library_id IS NOT NULL "
            "AND asin IS NOT NULL AND library_status IN ('owned', 'missing')"
        )).fetchall()
    asins_by_library: Dict[int, set] = {}
    for library_id, asin in rows:
        asins_by_library.setdefault(library_id, set()).add(asin)

    suggestions: Dict[int, Dict[str, Any]] = {}
    abs_index: Dict[str, Counter] = {}
    for lib in client.libraries():
        if lib.get('mediaType', 'book') != 'book':
            continue
        counter: Counter = Counter()
        for item in client.all_library_items(lib['id']):
            asin = ((item.get('media') or {}).get('metadata') or {}).get('asin')
            if asin:
                counter[(asin.upper(), item.get('folderId'))] += 1
        abs_index[lib['id']] = counter

    for library in container.get_library_repository().all():
        wanted = asins_by_library.get(library['id']) or set()
        best = None
        for abs_library_id, counter in abs_index.items():
            per_folder: Counter = Counter()
            for (asin, folder_id), n in counter.items():
                if asin in wanted:
                    per_folder[folder_id] += n
            if per_folder:
                folder_id, matches = per_folder.most_common(1)[0]
                if not best or matches > best['matches']:
                    best = {'abs_library_id': abs_library_id, 'abs_folder_id': folder_id, 'matches': matches}
        if best:
            best['of'] = len(wanted)
            suggestions[library['id']] = best
    return suggestions


def to_abs_path(mapping: Dict[str, Any], library_root: str, local_path: str) -> Optional[str]:
    """The path Audiobookshelf uses for a file or folder AuralArchive sees."""
    rel = relative_to_root(library_root, local_path)
    if rel is None:
        return None
    return f"{mapping['abs_folder_path']}/{rel}" if rel else mapping['abs_folder_path']


def relative_to_root(library_root: str, local_path: str) -> Optional[str]:
    real_root = os.path.realpath(library_root)
    real = os.path.realpath(local_path)
    try:
        if os.path.commonpath([real, real_root]) != real_root:
            return None
    except ValueError:
        return None
    rel = os.path.relpath(real, real_root)
    return '' if rel == '.' else rel.replace(os.sep, '/')
