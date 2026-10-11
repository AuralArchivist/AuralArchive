"""
Module Name: audiobookshelf.py
Author: AuralArchivist
Created: October 3, 2026
Last Modified: October 9, 2026
Description:
    Audiobookshelf connection API: settings, connection test, library
    mapping, sync, links, metadata backfill and per-book details.

Location:
    /api/system/audiobookshelf.py

Endpoints:

GET
    /api/abs/settings                Settings (key masked), mapping, libraries, status
    /api/abs/status                  Sync state, counts, job queue, unlinked and orphan lists
    /api/abs/metadata/backfill       Backfill progress
    /api/abs/books/<book_id>         Link, progress per user and play link for one book

POST
    /api/abs/settings                Save settings {url, api_key, enabled, ...}
    /api/abs/test                    Test a URL and key without saving; returns libraries and a suggested mapping
    /api/abs/mapping                 Replace the library mapping {entries: [{library_id, abs_library_id, abs_folder_id}]}
    /api/abs/sync                    Run a full sync now
    /api/abs/link                    Link a book by hand {book_id, abs_item_id}
    /api/abs/metadata/preview        Gaps a backfill would fill
    /api/abs/metadata/backfill       Start the backfill
    /api/abs/jobs/retry              Retry failed background jobs

DELETE
    /api/abs/link/<book_id>          Remove a book's link
"""

import threading

from flask import Blueprint, jsonify, request

from core.logging import get_module_logger
from integrations.audiobookshelf import AbsClient, AbsError
from services.sync.audiobookshelf import connection, jobs, linker, mapping, metadata, progress
from services.container import container

abs_api = Blueprint('abs_api', __name__, url_prefix='/api/abs')
logger = get_module_logger("API.System.Audiobookshelf")

LIST_LIMIT = 300


@abs_api.errorhandler(AbsError)
def _abs_error(exc):
    return jsonify({'success': False, 'error': str(exc)}), 502


def _aa_libraries():
    return [{'id': lib['id'], 'name': lib['name'], 'root': lib['root'], 'enabled': lib['enabled']}
            for lib in container.get_library_repository().all()]


def _status():
    state = connection.get_state()
    return {
        'enabled': connection.is_enabled(),
        'version': state.get('version'),
        'capabilities': state.get('capabilities') or {},
        'last_sync': state.get('last_sync'),
        'last_sync_result': state.get('last_sync_result'),
        'last_error': state.get('last_error'),
        'last_error_at': state.get('last_error_at'),
        'syncing': container.get_audiobookshelf_runner().syncing,
        'jobs': jobs.queue_summary(),
        'failed_jobs': jobs.recent_failures(),
        'linked': linker.counts()['linked'],
        'unlinked_count': len(state.get('unlinked') or []),
        'orphans_count': len(state.get('orphans') or []),
        'backfill': metadata.backfill_state(),
        'provider': metadata.provider(),
    }


@abs_api.route('/settings', methods=['GET'])
def get_settings():
    return jsonify({
        'success': True,
        'settings': connection.get_settings(),
        'mapping': mapping.get_map(),
        'libraries': _aa_libraries(),
        'status': _status(),
    })


@abs_api.route('/settings', methods=['POST'])
def save_settings():
    data = request.get_json(silent=True) or {}
    errors = connection.save_settings(data)
    if errors:
        return jsonify({'success': False, 'errors': errors, 'error': next(iter(errors.values()))}), 400
    if isinstance(data.get('mapping'), list) and connection.get_settings()['url']:
        mapping.save_map(connection.get_client(), data['mapping'])
    if connection.is_enabled():
        _spawn_sync()
    return jsonify({'success': True, 'settings': connection.get_settings(), 'mapping': mapping.get_map()})


@abs_api.route('/test', methods=['POST'])
def test():
    data = request.get_json(silent=True) or {}
    result = connection.test_connection(data.get('url'), data.get('api_key'), data.get('verify_tls'))
    if not result.get('success'):
        return jsonify(result), 400
    try:
        saved = connection.get_settings(include_key=True)
        from core.config import secrets_crypto
        key = (data.get('api_key') or '').strip()
        if not key or secrets_crypto.is_masked_value(key):
            key = saved['api_key']
        url = (data.get('url') or saved['url']).strip().rstrip('/')
        client = AbsClient(url, key, verify_tls=saved['verify_tls'] if data.get('verify_tls') is None
                           else bool(data.get('verify_tls')))
        result['suggestions'] = {str(k): v for k, v in mapping.suggest(client).items()}
    except AbsError as exc:
        result['suggestions'] = {}
        result.setdefault('warnings', []).append(f"Couldn't suggest a library mapping: {exc}")
    result['aa_libraries'] = _aa_libraries()
    return jsonify(result)


@abs_api.route('/mapping', methods=['POST'])
def save_mapping():
    entries = (request.get_json(silent=True) or {}).get('entries') or []
    try:
        rows = mapping.save_map(connection.get_client(), entries)
    except ValueError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 400
    _spawn_sync()
    return jsonify({'success': True, 'mapping': rows})


@abs_api.route('/sync', methods=['POST'])
def sync_now():
    if not connection.is_enabled():
        return jsonify({'success': False, 'error': 'Turn on the Audiobookshelf connection first'}), 400
    if container.get_audiobookshelf_runner().syncing:
        return jsonify({'success': True, 'status': 'running'})
    _spawn_sync()
    return jsonify({'success': True, 'status': 'started'})


def _spawn_sync():
    threading.Thread(target=container.get_audiobookshelf_runner().full_sync, name='abs-sync', daemon=True).start()


@abs_api.route('/status', methods=['GET'])
def status():
    state = connection.get_state()
    return jsonify({
        'success': True,
        'status': _status(),
        'unlinked': (state.get('unlinked') or [])[:LIST_LIMIT],
        'orphans': (state.get('orphans') or [])[:LIST_LIMIT],
    })


@abs_api.route('/link', methods=['POST'])
def link():
    data = request.get_json(silent=True) or {}
    try:
        result = linker.link_manually(int(data.get('book_id')), (data.get('abs_item_id') or '').strip(),
                                      connection.get_client())
    except (TypeError, ValueError) as exc:
        return jsonify({'success': False, 'error': str(exc) or 'book_id and abs_item_id are required'}), 400
    return jsonify({'success': True, **result})


@abs_api.route('/link/<int:book_id>', methods=['DELETE'])
def unlink(book_id: int):
    linker.unlink(book_id)
    return jsonify({'success': True})


@abs_api.route('/metadata/preview', methods=['POST'])
def metadata_preview():
    return jsonify({'success': True, **metadata.preview(connection.get_client())})


@abs_api.route('/metadata/backfill', methods=['POST'])
def metadata_backfill():
    try:
        state = metadata.start_backfill()
    except ValueError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 400
    return jsonify({'success': True, 'backfill': state})


@abs_api.route('/metadata/backfill', methods=['GET'])
def metadata_backfill_state():
    return jsonify({'success': True, 'backfill': metadata.backfill_state()})


@abs_api.route('/jobs/retry', methods=['POST'])
def retry_jobs():
    return jsonify({'success': True, 'retried': jobs.retry_failed()})


@abs_api.route('/books/<int:book_id>', methods=['GET'])
def book_detail(book_id: int):
    return jsonify({'success': True, 'abs': progress.book_detail(book_id)})
