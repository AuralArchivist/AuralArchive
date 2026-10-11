"""
Module Name: libraries.py
Author: AuralArchivist
Created: October 3, 2026
Last Modified: October 9, 2026
Description:
    Libraries API: the library list, routing rules, moving books between
    libraries and the library a queued download will go to.

Location:
    /api/system/libraries.py

Endpoints:

GET
    /api/libraries                          Libraries with health, book counts and settings
    /api/libraries/rules                    Routing rules
    /api/libraries/move-preview             ?asin=&library_id= destination of a move

POST
    /api/libraries                          Create {name, root_path, naming_template, enabled, is_default}
    /api/libraries/order                    Reorder {ids: [...]}
    /api/libraries/settings                 {keep_series_together}
    /api/libraries/route-test               {asin} or {title, author, series, language, narrator}
    /api/libraries/repair                   Set every book's library from its file path
    /api/libraries/move                     {asin, library_id} move a book's folder
    /api/libraries/downloads/<id>/target    {library_id} change a queued download's library
    /api/libraries/book-target              {asin, library_id} library for a wanted book (null = rules)

PUT
    /api/libraries/rules                    Replace the rule list {rules: [{kind, value, library_id}]}

PATCH
    /api/libraries/<id>                     Update

DELETE
    /api/libraries/<id>                     Delete, ?reassign_to=<id> when it has books
"""

from flask import Blueprint, jsonify, request
from sqlalchemy import text

from core.logging import get_module_logger
from services.library import LibraryError
from services.library.router import IMPORT_STARTED, choose_library
from services.container import container

libraries_api = Blueprint('libraries_api', __name__, url_prefix='/api/libraries')
logger = get_module_logger("API.System.Libraries")


@libraries_api.errorhandler(LibraryError)
def _library_error(exc):
    return jsonify({'success': False, 'error': str(exc)}), 400


def _settings():
    from services.library.router import keep_series_together
    return {'keep_series_together': keep_series_together()}


@libraries_api.route('', methods=['GET'])
def list_libraries():
    from services.library.storage_status import get_storage_report
    report = get_storage_report()
    templates = container.get_file_naming_service().get_available_templates()
    return jsonify({
        'success': True,
        'libraries': report.get('libraries') or [],
        'settings': _settings(),
        'templates': templates,
        'docker': report.get('docker'),
    })


@libraries_api.route('', methods=['POST'])
def create_library():
    library = container.get_library_repository().create(request.get_json(silent=True) or {})
    _refresh_storage()
    return jsonify({'success': True, 'library': library})


@libraries_api.route('/<int:library_id>', methods=['PATCH'])
def update_library(library_id: int):
    library = container.get_library_repository().update(library_id, request.get_json(silent=True) or {})
    _refresh_storage()
    return jsonify({'success': True, 'library': library})


@libraries_api.route('/<int:library_id>', methods=['DELETE'])
def delete_library(library_id: int):
    reassign = request.args.get('reassign_to', type=int)
    container.get_library_repository().delete(library_id, reassign)
    _refresh_storage()
    return jsonify({'success': True})


@libraries_api.route('/order', methods=['POST'])
def reorder_libraries():
    ids = (request.get_json(silent=True) or {}).get('ids') or []
    container.get_library_repository().reorder([int(i) for i in ids])
    return jsonify({'success': True})


@libraries_api.route('/settings', methods=['POST'])
def save_settings():
    data = request.get_json(silent=True) or {}
    value = 'true' if data.get('keep_series_together', True) else 'false'
    if not container.get_config_service().update_multiple_config({'libraries.keep_series_together': value}):
        return jsonify({'success': False, 'error': 'Failed to save configuration'}), 500
    return jsonify({'success': True, 'settings': _settings()})


@libraries_api.route('/rules', methods=['GET'])
def get_rules():
    return jsonify({'success': True, 'rules': container.get_library_repository().rules()})


@libraries_api.route('/rules', methods=['PUT'])
def put_rules():
    rules = (request.get_json(silent=True) or {}).get('rules') or []
    return jsonify({'success': True, 'rules': container.get_library_repository().replace_rules(rules)})


@libraries_api.route('/route-test', methods=['POST'])
def route_test():
    data = request.get_json(silent=True) or {}
    router = container.get_library_router()
    asin = (data.get('asin') or '').strip()
    if asin:
        from services.library.router import load_book
        book = load_book(asin)
        if not book:
            return jsonify({'success': False, 'error': 'No book with that ASIN in AuralArchive'}), 404
    else:
        book = {key: data.get(key) for key in ('title', 'author', 'series', 'language', 'narrator')}
    decision = router.resolve(book)
    return jsonify({'success': True, 'library': decision['library'], 'reason': decision['reason'],
                    'fallback': decision['fallback'], 'book': {'title': book.get('title'), 'author': book.get('author')}})


@libraries_api.route('/repair', methods=['POST'])
def repair():
    changed = container.get_library_repository().repair_assignments()
    return jsonify({'success': True, 'changed': changed})


@libraries_api.route('/move-preview', methods=['GET'])
def move_preview():
    from services.library.mover import plan_move
    plan = plan_move(request.args.get('asin', ''), request.args.get('library_id', type=int))
    return jsonify({'success': True, 'destination': plan['dest_file'], 'library': plan['target']['name']})


@libraries_api.route('/move', methods=['POST'])
def move():
    from services.library.mover import move_book
    data = request.get_json(silent=True) or {}
    try:
        result = move_book((data.get('asin') or '').strip(), int(data.get('library_id')))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'Choose a library'}), 400
    except OSError as exc:
        logger.error("Moving a book failed", extra={"asin": data.get('asin'), "error": str(exc)}, exc_info=True)
        return jsonify({'success': False, 'error': f'Moving the files failed: {exc}'}), 500
    return jsonify(result)


@libraries_api.route('/downloads/<int:download_id>/target', methods=['POST'])
def set_download_target(download_id: int):
    data = request.get_json(silent=True) or {}
    library_id = data.get('library_id')
    library = container.get_library_repository().get(library_id) if library_id not in (None, '') else None
    if library_id not in (None, '') and not library:
        return jsonify({'success': False, 'error': 'That library no longer exists'}), 400
    placeholders = ', '.join(f"'{status}'" for status in IMPORT_STARTED)
    with container.get_engine().begin() as conn:
        updated = conn.execute(text(
            f"UPDATE download_queue SET target_library_id = :l WHERE id = :id AND status NOT IN ({placeholders})"
        ), {'l': library and library['id'], 'id': download_id}).rowcount
    if not updated:
        return jsonify({'success': False, 'error': 'This download has already been imported'}), 409
    return jsonify({'success': True, 'library': library})


@libraries_api.route('/book-target', methods=['POST'])
def set_book_target():
    data = request.get_json(silent=True) or {}
    if not (data.get('asin') or '').strip():
        return jsonify({'success': False, 'error': 'No book given'}), 400
    try:
        library = choose_library(data['asin'], data.get('library_id'))
    except ValueError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 400
    return jsonify({'success': True, 'library': library})


def _refresh_storage():
    from services.library.storage_status import invalidate_banner_cache
    invalidate_banner_cache()
