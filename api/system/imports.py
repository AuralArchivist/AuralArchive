"""
Module Name: imports.py
Author: AuralArchivist
Created: August 1, 2025
Last Modified: October 9, 2026
Description:
    REST API endpoints for manual audiobook imports. Supports previews, batch
    staging, metadata refresh, imports, metadata search, and direct job-based
    imports for audiobook files.

Location:
    /api/system/imports.py

Endpoints:

GET
    /api/import/batch/<batch_id>                          Get batch preview
    /api/import/metadata/search                           Search metadata candidates
    /api/import/review                                    Pending library folders that need the user's decision
    /api/import/staging                                   List folders and files in the import staging directory
    /api/import/staging/scan                              Flat list of files under the staging directory

POST
    /api/import/batch/<batch_id>/card/<card_id>/refresh   Refresh card metadata
    /api/import/batch/<batch_id>/import                   Import selected batch cards
    /api/import/batch/preview                             Stage batch preview
    /api/import/preview                                   Preview files for import
    /api/import/review/<item_id>/accept                   Adopt the folder in place using its suggested match
    /api/import/review/<item_id>/ignore                   Stop flagging a library folder
    /api/import/upload                                    Upload files via the browser's native file picker

DELETE
    /api/import/batch/<batch_id>                          Delete batch preview
"""

import os
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from flask import Blueprint, jsonify, request
from werkzeug.utils import secure_filename

from services.library.manual_import import LocalFileImportCoordinator
from services.library.matcher import AUDIO_EXTENSIONS, BookFileMatcher
from core.logging import get_module_logger
from core.paths import ensure_within_directory, get_path_resolver
from services.container import container

logger = get_module_logger("API.Import")

import_api_bp = Blueprint('import_api', __name__, url_prefix='/api/import')

BATCH_CACHE: Dict[str, Dict[str, Any]] = {}
BATCH_CACHE_TTL_SECONDS = 60 * 60


def _cleanup_batch_cache() -> None:
    now = time.time()
    expired: List[str] = []
    for batch_id, batch in BATCH_CACHE.items():
        last_touch = batch.get('updated_at') or batch.get('created_at') or now
        if now - last_touch > BATCH_CACHE_TTL_SECONDS:
            expired.append(batch_id)
    for batch_id in expired:
        BATCH_CACHE.pop(batch_id, None)


def _get_batch_record(batch_id: str) -> Dict[str, Any]:
    _cleanup_batch_cache()
    batch = BATCH_CACHE.get(batch_id)
    if not batch:
        raise KeyError(batch_id)
    batch['accessed_at'] = time.time()
    return batch


def _ordered_cards(batch: Dict[str, Any]) -> List[Dict[str, Any]]:
    cards = batch.get('cards', {})
    order = batch.get('order') or list(cards.keys())
    return [cards[card_id] for card_id in order if card_id in cards]


def _compute_card_summary(cards: List[Dict[str, Any]]) -> Dict[str, int]:
    summary: Dict[str, int] = {
        'total': len(cards),
        'ready': 0,
        'pending': 0,
        'invalid': 0,
        'missing': 0,
        'error': 0,
        'imported': 0
    }
    for card in cards:
        status = card.get('status', 'pending')
        if status not in summary:
            summary[status] = 0
        summary[status] += 1
    return summary


def _serialize_batch(batch: Dict[str, Any]) -> Dict[str, Any]:
    cards = _ordered_cards(batch)
    summary = _compute_card_summary(cards)
    batch['summary'] = summary
    return {
        'batch_id': batch['batch_id'],
        'template': batch.get('template'),
        'library_path': batch.get('library_path'),
        'created_at': batch.get('created_at'),
        'updated_at': batch.get('updated_at'),
        'summary': summary,
        'cards': cards
    }


def _store_batch_record(preview: Dict[str, Any]) -> Dict[str, Any]:
    batch_id = str(uuid.uuid4())
    cards = preview.get('cards') or []
    cards_by_id: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for card in cards:
        card_id = card.get('card_id') or str(uuid.uuid4())
        card['card_id'] = card_id
        cards_by_id[card_id] = card
        order.append(card_id)

    record = {
        'batch_id': batch_id,
        'template': preview.get('template'),
        'library_path': preview.get('library_path'),
        'cards': cards_by_id,
        'order': order,
        'created_at': time.time(),
        'updated_at': time.time(),
        'summary': preview.get('summary') or {}
    }
    _cleanup_batch_cache()
    BATCH_CACHE[batch_id] = record
    return record


def _select_card_ids(batch: Dict[str, Any], requested_ids: Optional[List[str]]) -> List[str]:
    cards = batch.get('cards', {})
    order = batch.get('order') or list(cards.keys())
    if requested_ids:
        requested_set = {card_id for card_id in requested_ids if card_id in cards}
        return [card_id for card_id in order if card_id in requested_set]
    return [card_id for card_id in order if cards[card_id].get('status') == 'ready']


def _resolve_paths(payload: Dict[str, Any]) -> List[str]:
    paths = payload.get('paths') or payload.get('files') or payload.get('entries') or []
    if isinstance(paths, list):
        return [str(item) for item in paths if item]
    return []


def _get_import_directory() -> str:
    return get_path_resolver().get_import_dir()


def _get_library_directory() -> str:
    """The configured library root - the only place import jobs may write to.
    Same resolver lookup as ImportService.library_base_path, so both sides of
    the containment check agree on the same directory."""
    return get_path_resolver().get_library_dir()


def _safe_join(base_path: str, relative_path: str) -> str:
    sanitized = relative_path.strip().lstrip('/') if relative_path else ''
    try:
        return ensure_within_directory(sanitized, base_path)
    except ValueError:
        raise ValueError('Requested path escapes the configured import directory') from None


def _ensure_within_directory(path: str, allowed_root: str, *, what: str = 'path') -> str:
    """Contain an already-absolute (or relative) path to a configured root.

    Unlike _safe_join (which joins a relative fragment onto a base), this
    validates a path a client supplied wholesale - e.g. a source file path
    to import, or a destination library_path override. Delegates to the
    shared core.paths helper, which resolves symlinks too, so a
    symlink planted inside the root can't point the import outside it.
    """
    return ensure_within_directory(path, allowed_root, what=what)


def _validate_import_paths(paths: List[str]) -> List[str]:
    """Validate a list of source file paths against the configured import
    directory, raising ValueError (caller returns 400) on the first escape."""
    import_dir = _get_import_directory()
    return [_ensure_within_directory(p, import_dir, what='Source path') for p in paths]


def _validate_library_path(library_path: Optional[str]) -> Optional[str]:
    """Validate an optional destination library_path override. It must sit in
    one of the configured libraries. None/empty passes through unchanged - the
    importer then uses its own default."""
    if not library_path:
        return library_path
    library = container.get_library_repository().library_for_path(library_path)
    library_dir = library['root'] if library else _get_library_directory()
    return _ensure_within_directory(library_path, library_dir, what='Destination library_path')


def _destination(payload: Dict[str, Any], batch: Optional[Dict[str, Any]] = None):
    """(template_name, library_path) for a request: a library_id picks the
    library's folder and naming template; otherwise a validated library_path."""
    repo = container.get_library_repository()
    batch = batch or {}
    template_name = payload.get('template_name') or batch.get('template')
    library_id = payload.get('library_id')
    if library_id not in (None, ''):
        library = repo.get(int(library_id))
        if not library:
            raise ValueError('That library no longer exists')
        return template_name or library.get('naming_template'), library['root']
    library_path = _validate_library_path(payload.get('library_path') or batch.get('library_path'))
    library = repo.library_for_path(library_path) if library_path else None
    if library and not payload.get('template_name') and library.get('naming_template'):
        template_name = library['naming_template']
    return template_name, library_path


def _relative_path(target_path: str, base_path: str) -> str:
    rel = os.path.relpath(target_path, base_path)
    return '' if rel in ('.', './') else rel


def _describe_path(path: str, base_path: str) -> Dict[str, Any]:
    try:
        stat_result = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise FileNotFoundError(f'Unable to stat path: {exc}') from exc

    is_dir = os.path.isdir(path)
    extension = os.path.splitext(path)[1].lower() if not is_dir else ''
    rel_path = _relative_path(path, base_path)

    return {
        'name': os.path.basename(path),
        'path': rel_path,
        'absolute_path': path,
        'is_dir': is_dir,
        'size_bytes': None if is_dir else stat_result.st_size,
        'modified': datetime.fromtimestamp(stat_result.st_mtime).isoformat(),
        'extension': extension,
        'can_import': (not is_dir) and (extension in AUDIO_EXTENSIONS)
    }


def _list_directory(path: str, base_path: str) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    with os.scandir(path) as iterator:
        for entry in iterator:
            try:
                stat_result = entry.stat(follow_symlinks=False)
            except OSError:
                continue

            entry_path = entry.path
            is_dir = entry.is_dir(follow_symlinks=False)
            extension = os.path.splitext(entry.name)[1].lower() if not is_dir else ''
            rel_path = _relative_path(entry_path, base_path)
            entries.append({
                'name': entry.name,
                'path': rel_path,
                'absolute_path': entry_path,
                'is_dir': is_dir,
                'size_bytes': None if is_dir else stat_result.st_size,
                'modified': datetime.fromtimestamp(stat_result.st_mtime).isoformat(),
                'extension': extension,
                'can_import': (not is_dir) and (extension in AUDIO_EXTENSIONS)
            })

    entries.sort(key=lambda item: (not item['is_dir'], item['name'].lower()))
    return entries


def _parse_extensions(raw_extensions: Optional[str]) -> Optional[Set[str]]:
    if not raw_extensions:
        return None
    tokens = {token.strip().lower() for token in raw_extensions.split(',') if token.strip()}
    if not tokens:
        return None
    return {token if token.startswith('.') else f'.{token}' for token in tokens}


@import_api_bp.route('/preview', methods=['POST'])
def preview_import_files():
    """Return metadata preview for the provided file paths."""
    try:
        payload = request.get_json(silent=True) or {}
        paths = _resolve_paths(payload)
        if not paths:
            return jsonify({'success': False, 'error': 'Provide a list of file paths to preview'}), 400
        paths = _validate_import_paths(paths)

        coordinator = LocalFileImportCoordinator()
        preview = coordinator.preview_files(paths)
        return jsonify({'success': True, **preview})

    except ValueError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 400
    except Exception as exc:
        logger.error("Import preview failed: %s", exc, exc_info=True)
        return jsonify({'success': False, 'error': 'Internal error (details in server log)'}), 500


@import_api_bp.route('/batch/preview', methods=['POST'])
def create_batch_preview():
    """Generate staged import cards for the requested files."""
    try:
        payload = request.get_json(silent=True) or {}
        paths = _resolve_paths(payload)
        if not paths:
            return jsonify({'success': False, 'error': 'Provide at least one file path to stage'}), 400
        paths = _validate_import_paths(paths)

        template_name, library_path = _destination(payload)
        coordinator = LocalFileImportCoordinator()
        preview = coordinator.build_batch_preview(paths, template_name, library_path)
        record = _store_batch_record(preview)
        response = _serialize_batch(record)
        response['success'] = True
        return jsonify(response)

    except ValueError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 400
    except Exception as exc:
        logger.error("Batch preview creation failed: %s", exc, exc_info=True)
        return jsonify({'success': False, 'error': 'Internal error (details in server log)'}), 500


@import_api_bp.route('/batch/<batch_id>', methods=['GET'])
def get_batch_preview(batch_id: str):
    try:
        batch = _get_batch_record(batch_id)
        response = _serialize_batch(batch)
        response['success'] = True
        return jsonify(response)
    except KeyError:
        return jsonify({'success': False, 'error': 'Batch not found'}), 404
    except Exception as exc:
        logger.error("Failed to fetch batch %s: %s", batch_id, exc, exc_info=True)
        return jsonify({'success': False, 'error': 'Internal error (details in server log)'}), 500


@import_api_bp.route('/batch/<batch_id>', methods=['DELETE'])
def delete_batch_preview(batch_id: str):
    try:
        removed = BATCH_CACHE.pop(batch_id, None)
        if not removed:
            return jsonify({'success': False, 'error': 'Batch not found'}), 404
        return jsonify({'success': True, 'batch_id': batch_id})
    except Exception as exc:
        logger.error("Failed to delete batch %s: %s", batch_id, exc, exc_info=True)
        return jsonify({'success': False, 'error': 'Internal error (details in server log)'}), 500


@import_api_bp.route('/batch/<batch_id>/card/<card_id>/refresh', methods=['POST'])
def refresh_batch_card(batch_id: str, card_id: str):
    try:
        batch = _get_batch_record(batch_id)
        card = batch.get('cards', {}).get(card_id)
        if not card:
            return jsonify({'success': False, 'error': 'Card not found in batch'}), 404

        payload = request.get_json(silent=True) or {}
        metadata_override = payload.get('metadata') if isinstance(payload.get('metadata'), dict) else None
        asin_override = payload.get('asin')
        template_name, library_path = _destination(payload) if (payload.get('library_id') or payload.get('library_path')) \
            else (payload.get('template_name'), None)

        coordinator = LocalFileImportCoordinator()
        updated_card = coordinator.refresh_card_metadata(
            card,
            asin=asin_override,
            metadata_override=metadata_override,
            template_name=template_name,
            library_path=library_path
        )
        batch['cards'][card_id] = updated_card
        batch['updated_at'] = time.time()
        summary = _compute_card_summary(_ordered_cards(batch))
        batch['summary'] = summary

        return jsonify({'success': True, 'card': updated_card, 'summary': summary})

    except KeyError:
        return jsonify({'success': False, 'error': 'Batch not found'}), 404
    except ValueError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 400
    except Exception as exc:
        logger.error("Failed to refresh card %s/%s: %s", batch_id, card_id, exc, exc_info=True)
        return jsonify({'success': False, 'error': 'Internal error (details in server log)'}), 500


@import_api_bp.route('/batch/<batch_id>/import', methods=['POST'])
def import_batch_cards(batch_id: str):
    try:
        batch = _get_batch_record(batch_id)
        payload = request.get_json(silent=True) or {}
        requested_ids = payload.get('card_ids')
        if requested_ids is not None and not isinstance(requested_ids, list):
            return jsonify({'success': False, 'error': 'card_ids must be a list of IDs'}), 400

        selected_ids = _select_card_ids(batch, requested_ids)
        if not selected_ids:
            return jsonify({'success': False, 'error': 'No cards available for import'}), 400

        cards = [batch['cards'][card_id] for card_id in selected_ids]
        template_name, library_path = _destination(payload, batch)
        options = {
            'template_name': template_name,
            'library_path': library_path,
            'move': payload.get('move', True)
        }

        coordinator = LocalFileImportCoordinator()
        result = coordinator.import_prepared_cards(cards, options)

        for index, outcome in enumerate(result['results']):
            card_id = selected_ids[index] if index < len(selected_ids) else None
            if not card_id or card_id not in batch['cards']:
                continue
            card = batch['cards'][card_id]
            card['status'] = 'imported' if outcome.get('success') else 'error'
            card.setdefault('messages', [])
            outcome_message = outcome.get('message')
            if outcome_message:
                card['messages'].append(outcome_message)
            destination = card.setdefault('destination', {})
            if outcome.get('destination_path'):
                destination['final_path'] = outcome.get('destination_path')
            outcome['card_id'] = card_id

        batch['updated_at'] = time.time()
        summary = _compute_card_summary(_ordered_cards(batch))
        batch['summary'] = summary

        return jsonify({'success': True, 'result': result, 'summary': summary})

    except KeyError:
        return jsonify({'success': False, 'error': 'Batch not found'}), 404
    except ValueError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 400
    except Exception as exc:
        logger.error("Batch import failed for %s: %s", batch_id, exc, exc_info=True)
        return jsonify({'success': False, 'error': 'Internal error (details in server log)'}), 500


@import_api_bp.route('/metadata/search', methods=['GET'])
def search_import_metadata():
    try:
        query = request.args.get('q') or request.args.get('query') or ''
        author = request.args.get('author')
        asin = request.args.get('asin')
        try:
            limit = int(request.args.get('limit', '10'))
        except ValueError:
            limit = 10

        result = BookFileMatcher().search_candidates(query=query, author=author, asin=asin, limit=limit)
        return jsonify({'success': True, **result})

    except Exception as exc:
        logger.error("Metadata search failed: %s", exc, exc_info=True)
        return jsonify({'success': False, 'error': 'Internal error (details in server log)'}), 500


@import_api_bp.route('/upload', methods=['POST'])
def upload_import_files():
    """Accept one or more files picked via the browser's native file dialog
    and save them into the staging directory's uploads/ subfolder, so they
    can be added to the selection the same way a staging-browsed file is -
    this is purely an alternate way to get files onto the server; the rest
    of the import pipeline treats them identically either way."""
    try:
        uploaded_files = request.files.getlist('files')
        if not uploaded_files:
            return jsonify({'success': False, 'error': 'No files were provided'}), 400

        base_path = _get_import_directory()
        upload_dir = os.path.join(base_path, 'uploads')
        os.makedirs(upload_dir, exist_ok=True)

        saved: List[Dict[str, Any]] = []
        skipped: List[Dict[str, str]] = []

        for file_storage in uploaded_files:
            original_name = file_storage.filename or ''
            if not original_name:
                continue

            extension = os.path.splitext(original_name)[1].lower()
            if extension not in AUDIO_EXTENSIONS:
                skipped.append({'name': original_name, 'reason': f'Unsupported file type: {extension or "unknown"}'})
                continue

            safe_name = secure_filename(original_name) or f'upload{extension}'
            destination = os.path.join(upload_dir, safe_name)

            if os.path.exists(destination):
                stem, ext = os.path.splitext(safe_name)
                destination = os.path.join(upload_dir, f'{stem}_{uuid.uuid4().hex[:8]}{ext}')

            try:
                file_storage.save(destination)
            except OSError as save_exc:
                logger.warning("Could not save uploaded file %s", original_name, exc_info=True)
                skipped.append({'name': original_name, 'reason': f'Failed to save: {save_exc}'})
                continue

            saved.append(_describe_path(destination, base_path))

        if not saved and skipped:
            return jsonify({'success': False, 'error': 'No files were saved', 'skipped': skipped}), 400

        return jsonify({'success': True, 'files': saved, 'skipped': skipped})

    except Exception as exc:
        logger.error("File upload failed: %s", exc, exc_info=True)
        return jsonify({'success': False, 'error': 'Internal error (details in server log)'}), 500


@import_api_bp.route('/staging', methods=['GET'])
def list_import_staging_directory():
    """List subdirectories and files inside the configured import staging directory."""
    try:
        base_path = _get_import_directory()
        if not os.path.exists(base_path):
            return jsonify({'success': False, 'error': f'Import directory not found: {base_path}'}), 404
        if not os.path.isdir(base_path):
            return jsonify({'success': False, 'error': f'Import directory is not a folder: {base_path}'}), 400

        relative_path = request.args.get('path', '').strip()
        target_path = _safe_join(base_path, relative_path)
        if not os.path.exists(target_path):
            return jsonify({'success': False, 'error': f'Path not found: {relative_path or "."}'}), 404
        if not os.path.isdir(target_path):
            return jsonify({'success': False, 'error': 'Provide a directory path to list.'}), 400

        entries = _list_directory(target_path, base_path)
        breadcrumbs = _build_breadcrumbs(base_path, target_path)

        return jsonify({
            'success': True,
            'root': base_path,
            'path': _relative_path(target_path, base_path),
            'breadcrumbs': breadcrumbs,
            'entries': entries,
            'summary': {
                'directories': len([entry for entry in entries if entry['is_dir']]),
                'files': len([entry for entry in entries if not entry['is_dir']])
            }
        })
    except ValueError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 400
    except Exception as exc:
        logger.error("Failed to list import staging directory: %s", exc, exc_info=True)
        return jsonify({'success': False, 'error': 'Internal error (details in server log)'}), 500


def _build_breadcrumbs(base_path: str, target_path: str) -> List[Dict[str, str]]:
    breadcrumbs: List[Dict[str, str]] = [
        {'label': 'Import Root', 'path': ''}
    ]
    rel_path = _relative_path(target_path, base_path)
    if not rel_path:
        return breadcrumbs

    running_path = []
    for segment in rel_path.split(os.sep):
        running_path.append(segment)
        breadcrumbs.append({'label': segment, 'path': '/'.join(running_path)})
    return breadcrumbs


@import_api_bp.route('/staging/scan', methods=['GET'])
def scan_import_staging_directory():
    """Return a flattened list of files under the staging directory (optionally recursive)."""
    try:
        base_path = _get_import_directory()
        if not os.path.exists(base_path):
            return jsonify({'success': False, 'error': f'Import directory not found: {base_path}'}), 404
        if not os.path.isdir(base_path):
            return jsonify({'success': False, 'error': f'Import directory is not a folder: {base_path}'}), 400

        relative_path = request.args.get('path', '').strip()
        target_path = _safe_join(base_path, relative_path)
        if not os.path.exists(target_path):
            return jsonify({'success': False, 'error': f'Path not found: {relative_path or "."}'}), 404
        if not os.path.isdir(target_path):
            return jsonify({'success': False, 'error': 'Provide a directory path to scan.'}), 400
        extensions = _parse_extensions(request.args.get('extensions')) or AUDIO_EXTENSIONS
        recursive = request.args.get('recursive', 'true').lower() in {'true', '1', 'yes'}
        limit_param = request.args.get('limit')
        try:
            max_entries = int(limit_param) if limit_param else 500
        except ValueError:
            max_entries = 500
        max_entries = max(1, min(max_entries, 5000))

        files: List[Dict[str, Any]] = []
        for root, dirs, filenames in os.walk(target_path):
            filenames.sort()
            for filename in filenames:
                ext = os.path.splitext(filename)[1].lower()
                if extensions and ext not in extensions:
                    continue
                file_path = os.path.join(root, filename)
                try:
                    files.append(_describe_path(file_path, base_path))
                except FileNotFoundError:
                    continue

                if len(files) >= max_entries:
                    break
            if len(files) >= max_entries or not recursive:
                break

        return jsonify({
            'success': True,
            'root': base_path,
            'path': _relative_path(target_path, base_path),
            'count': len(files),
            'limit': max_entries,
            'extensions': sorted(list(extensions)) if extensions else None,
            'files': files
        })
    except ValueError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 400
    except Exception as exc:
        logger.error("Failed to scan import staging directory: %s", exc, exc_info=True)
        return jsonify({'success': False, 'error': 'Internal error (details in server log)'}), 500


# --- Library review list -----------------------------------------------------

@import_api_bp.route('/review', methods=['GET'])
def list_library_review():
    """Pending library folders that need the user's decision."""
    try:
        from services.library.library_check import list_review_items
        items = list_review_items('pending')
        libraries = container.get_library_repository().all()
        if len(libraries) > 1:
            names = {lib['id']: lib['name'] for lib in libraries}
            for item in items:
                item['library_name'] = names.get(item.get('library_id'))
        return jsonify({'success': True, 'items': items, 'count': len(items)})
    except Exception as exc:
        logger.error("Failed to list library review items: %s", exc, exc_info=True)
        return jsonify({'success': False, 'error': 'Internal error (details in server log)'}), 500


@import_api_bp.route('/review/<int:item_id>/accept', methods=['POST'])
def accept_library_review(item_id: int):
    """Adopt the folder in place using its suggested match."""
    try:
        from services.library.library_check import accept_review_item
        result = accept_review_item(item_id)
        status = 200 if result.get('success') else 400
        return jsonify({'success': bool(result.get('success')), 'message': result.get('message'),
                        'title': result.get('title')}), status
    except Exception as exc:
        logger.error("Failed to accept library review item %s: %s", item_id, exc, exc_info=True)
        return jsonify({'success': False, 'error': 'Internal error (details in server log)'}), 500


@import_api_bp.route('/review/<int:item_id>/ignore', methods=['POST'])
def ignore_library_review(item_id: int):
    """Stop flagging this folder (e.g. it isn't an audiobook)."""
    try:
        from services.library.library_check import ignore_review_item
        if not ignore_review_item(item_id):
            return jsonify({'success': False, 'message': 'Review item not found'}), 404
        return jsonify({'success': True})
    except Exception as exc:
        logger.error("Failed to ignore library review item %s: %s", item_id, exc, exc_info=True)
        return jsonify({'success': False, 'error': 'Internal error (details in server log)'}), 500
