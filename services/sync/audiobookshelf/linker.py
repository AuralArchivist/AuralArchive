"""
Module Name: linker.py
Author: AuralArchivist
Created: October 3, 2026
Last Modified: October 9, 2026
Description:
    Links each AuralArchive book to its Audiobookshelf item, by ASIN or by
    exact path inside the library. There is no title guessing: a wrong link
    would let a metadata match overwrite the wrong book.

Location:
    /services/sync/audiobookshelf/linker.py
"""

import os
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text

from core.logging import get_module_logger
from integrations.audiobookshelf import AbsClient

from . import mapping as abs_mapping
from services.container import container

_LOGGER = get_module_logger("Service.Audiobookshelf.Linker")

DROP_AFTER_MISSED_SYNCS = 2
NEWEST_ITEMS_TO_CHECK = 25

# relPath -> ASIN per mapped library, from the last sync (used for match hints)
_hint_index: Dict[int, Dict[str, str]] = {}
_hint_lock = threading.Lock()


def _item_asin(item: Dict[str, Any]) -> str:
    return (((item.get('media') or {}).get('metadata') or {}).get('asin') or '').strip().upper()


def _book_rel_paths(library_root: str, file_path: str) -> Tuple[Optional[str], Optional[str]]:
    """(folder relPath, file relPath) of a book inside its library."""
    folder = abs_mapping.relative_to_root(library_root, os.path.dirname(file_path))
    file_rel = abs_mapping.relative_to_root(library_root, file_path)
    return folder, file_rel


def _owned_books(library_id: int) -> List[Dict[str, Any]]:
    with container.get_engine().connect() as conn:
        rows = conn.execute(text(
            "SELECT id, UPPER(COALESCE(asin, '')), title, file_path FROM books "
            "WHERE library_id = :l AND library_status = 'owned' AND file_path IS NOT NULL AND TRIM(file_path) != ''"
        ), {'l': library_id}).fetchall()
    return [{'id': r[0], 'asin': r[1], 'title': r[2], 'file_path': r[3]} for r in rows]


def match_item(book: Dict[str, Any], library_root: str, items_by_rel: Dict[str, Dict[str, Any]],
               items_by_asin: Dict[str, Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if book['asin'] and book['asin'] in items_by_asin:
        return items_by_asin[book['asin']], 'asin'
    folder_rel, file_rel = _book_rel_paths(library_root, book['file_path'])
    for rel in (folder_rel, file_rel):
        if rel and rel in items_by_rel:
            return items_by_rel[rel], 'path'
    return None, None


def sync_links(client: AbsClient) -> Dict[str, Any]:
    """Refresh every mapped library's links. Returns counts and the unlinked lists."""
    repo = container.get_library_repository()
    report = {'linked': 0, 'new_links': 0, 'dropped': 0, 'unlinked': [], 'orphans': [], 'libraries': 0,
              'at': datetime.now().isoformat(timespec='seconds')}
    seen_items = set()
    hints: Dict[int, Dict[str, str]] = {}
    for row in abs_mapping.get_map():
        library = repo.get(row['library_id'])
        if not library:
            continue
        report['libraries'] += 1
        items = [i for i in client.all_library_items(row['abs_library_id'])
                 if i.get('folderId') == row['abs_folder_id'] or not i.get('folderId')]
        items_by_rel = {(i.get('relPath') or '').strip('/'): i for i in items if i.get('relPath')}
        items_by_asin: Dict[str, Dict[str, Any]] = {}
        for item in items:
            asin = _item_asin(item)
            if asin:
                items_by_asin.setdefault(asin, item)
        hints[library['id']] = {rel: _item_asin(i) for rel, i in items_by_rel.items() if _item_asin(i)}

        linked_item_ids = set()
        for book in _owned_books(library['id']):
            item, method = match_item(book, library['root'], items_by_rel, items_by_asin)
            if not item:
                report['unlinked'].append({'book_id': book['id'], 'title': book['title'], 'asin': book['asin']})
                continue
            if item['id'] in linked_item_ids:
                report['unlinked'].append({'book_id': book['id'], 'title': book['title'], 'asin': book['asin'],
                                           'reason': 'Another book is already linked to the same item'})
                continue
            linked_item_ids.add(item['id'])
            if _upsert_link(book['id'], item, row['abs_library_id'], method):
                report['new_links'] += 1
            report['linked'] += 1
            seen_items.add(item['id'])
        for item in items:
            if item['id'] not in linked_item_ids:
                meta = (item.get('media') or {}).get('metadata') or {}
                report['orphans'].append({'abs_item_id': item['id'], 'title': meta.get('title'),
                                          'author': meta.get('authorName'), 'rel_path': item.get('relPath'),
                                          'asin': _item_asin(item) or None, 'library_id': library['id']})

    report['dropped'] = _age_links(seen_items)
    with _hint_lock:
        _hint_index.clear()
        _hint_index.update(hints)
    return report


def _upsert_link(book_id: int, item: Dict[str, Any], abs_library_id: str, method: str,
                 conn=None) -> bool:
    """Insert or refresh a link. Returns True when it is new or now points elsewhere."""
    params = {'b': book_id, 'i': item['id'], 'm': (item.get('media') or {}).get('id'), 'l': abs_library_id,
              'k': method, 'r': item.get('relPath')}

    def run(c):
        current = c.execute(text("SELECT abs_item_id FROM abs_links WHERE book_id = :b"), {'b': book_id}).fetchone()
        c.execute(text("DELETE FROM abs_links WHERE abs_item_id = :i AND book_id != :b"), params)
        c.execute(text(
            "INSERT INTO abs_links (book_id, abs_item_id, abs_media_id, abs_library_id, link_method, rel_path, "
            "missed_syncs, last_seen_at) VALUES (:b, :i, :m, :l, :k, :r, 0, CURRENT_TIMESTAMP) "
            "ON CONFLICT(book_id) DO UPDATE SET abs_item_id = excluded.abs_item_id, "
            "abs_media_id = excluded.abs_media_id, abs_library_id = excluded.abs_library_id, "
            "link_method = CASE WHEN abs_links.link_method = 'manual' AND abs_links.abs_item_id = excluded.abs_item_id "
            "THEN 'manual' ELSE excluded.link_method END, rel_path = excluded.rel_path, missed_syncs = 0, "
            "last_seen_at = CURRENT_TIMESTAMP"
        ), params)
        return not current or current[0] != item['id']

    if conn is not None:
        return run(conn)
    with container.get_engine().begin() as c:
        return run(c)


def _age_links(seen_items: set) -> int:
    """Count a missed sync for links not seen; drop them after a few."""
    with container.get_engine().begin() as conn:
        rows = conn.execute(text("SELECT book_id, abs_item_id, link_method FROM abs_links")).fetchall()
        for book_id, item_id, method in rows:
            if item_id in seen_items:
                continue
            conn.execute(text("UPDATE abs_links SET missed_syncs = missed_syncs + 1 WHERE book_id = :b"), {'b': book_id})
        dropped = conn.execute(text(
            "DELETE FROM abs_links WHERE missed_syncs >= :n AND link_method != 'manual'"
        ), {'n': DROP_AFTER_MISSED_SYNCS}).rowcount
    return dropped or 0


def find_new_item(client: AbsClient, book: Dict[str, Any], row: Dict[str, Any],
                  library_root: str) -> Optional[Dict[str, Any]]:
    """Look for a just-imported book among the newest items, exact match only."""
    data = client.library_items(row['abs_library_id'], limit=NEWEST_ITEMS_TO_CHECK, sort='addedAt', desc=True)
    items = data.get('results') or []
    by_rel = {(i.get('relPath') or '').strip('/'): i for i in items if i.get('relPath')}
    by_asin = {}
    for item in items:
        asin = _item_asin(item)
        if asin:
            by_asin.setdefault(asin, item)
    item, method = match_item(book, library_root, by_rel, by_asin)
    if item:
        _upsert_link(book['id'], item, row['abs_library_id'], method)
    return item


def link_manually(book_id: int, abs_item_id: str, client: AbsClient) -> Dict[str, Any]:
    item = client.item(abs_item_id)
    if not item or not item.get('id'):
        raise ValueError('That Audiobookshelf item was not found')
    _upsert_link(book_id, item, item.get('libraryId') or '', 'manual')
    with container.get_engine().begin() as conn:
        conn.execute(text("UPDATE abs_links SET link_method = 'manual' WHERE book_id = :b"), {'b': book_id})
    return {'book_id': book_id, 'abs_item_id': item['id']}


def unlink(book_id: int) -> None:
    with container.get_engine().begin() as conn:
        conn.execute(text("DELETE FROM abs_links WHERE book_id = :b"), {'b': book_id})


def link_for_book(book_id: int) -> Optional[Dict[str, Any]]:
    with container.get_engine().connect() as conn:
        row = conn.execute(text(
            "SELECT abs_item_id, abs_media_id, abs_library_id, link_method, rel_path FROM abs_links WHERE book_id = :b"
        ), {'b': book_id}).fetchone()
    if not row:
        return None
    return dict(zip(('abs_item_id', 'abs_media_id', 'abs_library_id', 'link_method', 'rel_path'), row))


def hint_for(library_id: Optional[int], rel_path: str) -> Optional[str]:
    """ASIN Audiobookshelf has for a folder at rel_path, from the last sync."""
    with _hint_lock:
        return (_hint_index.get(library_id) or {}).get((rel_path or '').strip('/'))


def counts() -> Dict[str, int]:
    with container.get_engine().connect() as conn:
        linked = conn.execute(text("SELECT COUNT(*) FROM abs_links")).scalar() or 0
    return {'linked': linked}
