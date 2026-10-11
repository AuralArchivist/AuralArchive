"""
Module Name: metadata.py
Author: AuralArchivist
Created: October 3, 2026
Last Modified: October 9, 2026
Description:
    Audiobookshelf metadata: match each linked book by its exact ASIN (in
    AuralArchive's Audible marketplace) so Audiobookshelf fetches details,
    cover, genres and tags itself, then optionally apply AuralArchive's own
    authors and series. Also the backfill preview and run.

Location:
    /services/sync/audiobookshelf/metadata.py
"""

import threading
import time
from typing import Any, Dict, Optional

from sqlalchemy import text

from core.logging import get_module_logger
from integrations.audiobookshelf import AbsClient, AbsError, AbsForbidden

from . import connection
from services.container import container

_LOGGER = get_module_logger("Service.Audiobookshelf.Metadata")

PROVIDER_REGIONS = {'us': '', 'ca': 'ca', 'uk': 'uk', 'gb': 'uk', 'au': 'au', 'fr': 'fr', 'de': 'de',
                    'jp': 'jp', 'it': 'it', 'in': 'in', 'es': 'es'}
BATCH_SIZE = 50
SINGLE_MATCH_INTERVAL_SECONDS = 1.0

_backfill_lock = threading.Lock()
_backfill_state: Dict[str, Any] = {'running': False}


def provider() -> str:
    code = (container.get_config_service().get_config_value('audible', 'country_code', 'us') or 'us')
    region = PROVIDER_REGIONS.get(code.strip().lower(), '')
    return f"audible.{region}" if region else 'audible'


def _overrides() -> Dict[str, bool]:
    replace = connection.get_settings()['metadata_mode'] == 'match_replace'
    return {'override_details': replace, 'override_cover': replace}


def _linked_book(book_id: int) -> Optional[Dict[str, Any]]:
    with container.get_engine().connect() as conn:
        row = conn.execute(text(
            "SELECT b.id, UPPER(COALESCE(b.asin, '')), b.title, b.author, b.series, b.sequence, "
            "l.abs_item_id, l.link_method FROM books b JOIN abs_links l ON l.book_id = b.id WHERE b.id = :id"
        ), {'id': book_id}).fetchone()
    if not row:
        return None
    return dict(zip(('id', 'asin', 'title', 'author', 'series', 'sequence', 'abs_item_id', 'link_method'), row))


def match_book(client: AbsClient, book_id: int) -> Dict[str, Any]:
    """Match one linked book in Audiobookshelf by its ASIN."""
    settings = connection.get_settings()
    if settings['metadata_mode'] == 'off':
        return {'skipped': 'Metadata matching is off'}
    book = _linked_book(book_id)
    if not book or not book['asin']:
        return {'skipped': 'Book is not linked or has no ASIN'}
    result = client.match_item(book['abs_item_id'], provider=provider(), asin=book['asin'], **_overrides())
    if result.get('warning'):
        _LOGGER.info("Audiobookshelf found no match", extra={"book_id": book_id, "warning": result['warning']})
        return {'warning': result['warning']}
    if settings['metadata_aa_authority']:
        apply_authority(client, book, (result.get('libraryItem') or {}))
    return {'updated': bool(result.get('updated'))}


def apply_authority(client: AbsClient, book: Dict[str, Any], item: Dict[str, Any]) -> bool:
    """Send AuralArchive's authors and series when Audiobookshelf's differ."""
    metadata = ((item.get('media') or {}).get('metadata') or {}) if item else {}
    if not metadata:
        metadata = ((client.item(book['abs_item_id']).get('media') or {}).get('metadata') or {})
    ours_authors = [a.strip() for a in (book['author'] or '').split(',') if a.strip()]
    theirs_authors = [a.get('name') for a in metadata.get('authors') or [] if isinstance(a, dict)]
    payload: Dict[str, Any] = {}
    if ours_authors and [a.lower() for a in ours_authors] != [(a or '').lower() for a in theirs_authors]:
        payload['authors'] = [{'name': a} for a in ours_authors]
    if book['series']:
        theirs_series = [(s.get('name') or '', str(s.get('sequence') or '')) for s in metadata.get('series') or []
                         if isinstance(s, dict)]
        ours = (book['series'], str(book['sequence'] or ''))
        if not theirs_series or theirs_series[0][0].lower() != ours[0].lower() or theirs_series[0][1] != ours[1]:
            payload['series'] = [{'name': ours[0], 'sequence': ours[1] or None}]
    if not payload:
        return False
    client.patch_media(book['abs_item_id'], {'metadata': payload})
    return True


# --- Backfill ---------------------------------------------------------------------

def preview(client: AbsClient) -> Dict[str, Any]:
    """What a backfill would touch: linked books, and the gaps Audiobookshelf has now."""
    from . import mapping as abs_mapping
    with container.get_engine().connect() as conn:
        links = {r[0]: r[1] for r in conn.execute(text(
            "SELECT abs_item_id, link_method FROM abs_links l JOIN books b ON b.id = l.book_id "
            "WHERE b.asin IS NOT NULL AND TRIM(b.asin) != ''"))}
    gaps = {'series_number': 0, 'narrator': 0, 'publisher': 0, 'description': 0, 'genres': 0, 'asin': 0}
    for row in abs_mapping.get_map():
        for item in client.all_library_items(row['abs_library_id']):
            if item['id'] not in links:
                continue
            meta = (item.get('media') or {}).get('metadata') or {}
            if meta.get('seriesName') and '#' not in meta['seriesName']:
                gaps['series_number'] += 1
            if not meta.get('narratorName'):
                gaps['narrator'] += 1
            if not meta.get('publisher'):
                gaps['publisher'] += 1
            if not meta.get('description'):
                gaps['description'] += 1
            if not meta.get('genres'):
                gaps['genres'] += 1
            if not meta.get('asin'):
                gaps['asin'] += 1
    caps = connection.capabilities()
    return {'books': len(links), 'gaps': gaps, 'provider': provider(),
            'mode': connection.get_settings()['metadata_mode'], 'batch': bool(caps.get('is_admin')),
            'can_update': bool(caps.get('can_update'))}


def backfill_state() -> Dict[str, Any]:
    with _backfill_lock:
        return dict(_backfill_state)


def start_backfill() -> Dict[str, Any]:
    if connection.get_settings()['metadata_mode'] == 'off':
        raise ValueError('Choose a metadata mode first')
    with _backfill_lock:
        if _backfill_state.get('running'):
            raise ValueError('A backfill is already running')
        _backfill_state.update(running=True, done=0, total=0, errors=0, started=time.time(), finished=None,
                               message='Starting')
    threading.Thread(target=_run_backfill, name='abs-backfill', daemon=True).start()
    return backfill_state()


def _run_backfill() -> None:
    try:
        client = connection.get_client()
        caps = connection.capabilities()
        with container.get_engine().connect() as conn:
            rows = conn.execute(text(
                "SELECT l.book_id, l.abs_item_id, l.link_method, UPPER(b.asin) FROM abs_links l "
                "JOIN books b ON b.id = l.book_id WHERE b.asin IS NOT NULL AND TRIM(b.asin) != ''"
            )).fetchall()
        _update(total=len(rows), message='Matching')
        authority = connection.get_settings()['metadata_aa_authority']
        # Batch quick match only uses the ASIN Audiobookshelf already has, so it
        # is limited to items linked by that ASIN; the rest are matched one by one
        batchable = [r for r in rows if r[2] == 'asin']
        single = [r for r in rows if r[2] != 'asin']
        if caps.get('is_admin') and batchable:
            for start in range(0, len(batchable), BATCH_SIZE):
                chunk = batchable[start:start + BATCH_SIZE]
                try:
                    client.batch_quickmatch([r[1] for r in chunk], provider=provider(), **_overrides())
                except AbsForbidden:
                    single = batchable[start:] + single
                    break
                _update(done=start + len(chunk))
                time.sleep(SINGLE_MATCH_INTERVAL_SECONDS * 2)
        else:
            single = batchable + single
        done = len(rows) - len(single)
        for book_id, item_id, _method, asin in single:
            try:
                client.match_item(item_id, provider=provider(), asin=asin, **_overrides())
            except AbsError as exc:
                _LOGGER.info("Backfill match failed", extra={"book_id": book_id, "error": str(exc)})
                _update(errors=backfill_state().get('errors', 0) + 1)
            done += 1
            _update(done=done)
            time.sleep(SINGLE_MATCH_INTERVAL_SECONDS)
        if authority:
            _update(message='Applying AuralArchive authors and series')
            for book_id, *_rest in rows:
                book = _linked_book(book_id)
                if book:
                    try:
                        apply_authority(client, book, {})
                    except AbsError:
                        _update(errors=backfill_state().get('errors', 0) + 1)
                    time.sleep(SINGLE_MATCH_INTERVAL_SECONDS)
        _update(message='Finished')
    except Exception as exc:
        _LOGGER.warning("Audiobookshelf backfill stopped", exc_info=True)
        _update(message=f'Stopped: {exc}')
    finally:
        _update(running=False, finished=time.time())


def _update(**values) -> None:
    with _backfill_lock:
        _backfill_state.update(values)
