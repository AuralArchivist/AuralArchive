"""
Module Name: library_import.py
Author: AuralArchivist
Created: July 6, 2026
Last Modified: October 9, 2026
Description:
    REST API endpoints for Library Import: scanning an already-organized
    audiobook library directory, watching per-folder metadata matching
    happen live, reviewing/editing matches, and committing selected books
    into the library in place (no file moves).

Location:
    /api/system/library_import.py

Endpoints:

GET
    /api/library-import/metadata/search                              Search metadata candidates
    /api/library-import/scan/<session_id>                            Poll scan/session state

POST
    /api/library-import/scan                                         Start a scan
    /api/library-import/scan/<session_id>/cancel                     Cancel an in-progress scan
    /api/library-import/scan/<session_id>/folder/<folder_id>/flags   Update selected/monitored
    /api/library-import/scan/<session_id>/folder/<folder_id>/match   Assign a chosen match
    /api/library-import/scan/<session_id>/import                     Commit selected folders in place
"""

from flask import Blueprint, jsonify, request

from services.library.library_import import LibraryImportBusyError
from core.logging import get_module_logger
from services.container import container

logger = get_module_logger("API.LibraryImport")

library_import_api_bp = Blueprint('library_import_api', __name__, url_prefix='/api/library-import')


@library_import_api_bp.route('/scan', methods=['POST'])
def start_scan():
    """Discover unmapped book folders under the library path and begin matching."""
    try:
        payload = request.get_json(silent=True) or {}
        library_path = payload.get('library_path')
        library_id = payload.get('library_id')

        manager = container.get_library_import_manager()
        session = manager.start_scan(library_path, int(library_id) if library_id not in (None, '') else None)
        return jsonify({'success': True, **session})

    except LibraryImportBusyError as exc:
        return jsonify({
            'success': False,
            'error': str(exc),
            'running_session_id': exc.running_session_id
        }), 409
    except ValueError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 400
    except Exception as exc:
        logger.error("Library import scan failed to start: %s", exc, exc_info=True)
        return jsonify({'success': False, 'error': 'Internal error (details in server log)'}), 500


@library_import_api_bp.route('/scan/<session_id>', methods=['GET'])
def get_scan(session_id: str):
    """Poll the current state of a scan session (folders + summary)."""
    try:
        manager = container.get_library_import_manager()
        session = manager.get_session(session_id)
        return jsonify({'success': True, **session})
    except KeyError:
        return jsonify({'success': False, 'error': 'Scan session not found'}), 404
    except Exception as exc:
        logger.error("Failed to fetch scan session %s: %s", session_id, exc, exc_info=True)
        return jsonify({'success': False, 'error': 'Internal error (details in server log)'}), 500


@library_import_api_bp.route('/scan/<session_id>/cancel', methods=['POST'])
def cancel_scan(session_id: str):
    """Stop the background matching worker for a session."""
    try:
        manager = container.get_library_import_manager()
        session = manager.cancel_scan(session_id)
        return jsonify({'success': True, **session})
    except KeyError:
        return jsonify({'success': False, 'error': 'Scan session not found'}), 404
    except Exception as exc:
        logger.error("Failed to cancel scan session %s: %s", session_id, exc, exc_info=True)
        return jsonify({'success': False, 'error': 'Internal error (details in server log)'}), 500


@library_import_api_bp.route('/scan/<session_id>/folder/<folder_id>/match', methods=['POST'])
def assign_folder_match(session_id: str, folder_id: str):
    """Manually assign a chosen metadata candidate to a folder."""
    try:
        payload = request.get_json(silent=True) or {}
        metadata = payload.get('metadata')
        if not metadata or not isinstance(metadata, dict):
            return jsonify({'success': False, 'error': 'Provide the chosen metadata object'}), 400

        manager = container.get_library_import_manager()
        folder = manager.assign_match(session_id, folder_id, metadata)
        return jsonify({'success': True, 'folder': folder})
    except KeyError as exc:
        return jsonify({'success': False, 'error': f'Not found: {exc}'}), 404
    except Exception as exc:
        logger.error("Failed to assign match for %s/%s: %s", session_id, folder_id, exc, exc_info=True)
        return jsonify({'success': False, 'error': 'Internal error (details in server log)'}), 500


@library_import_api_bp.route('/scan/<session_id>/folder/<folder_id>/flags', methods=['POST'])
def update_folder_flags(session_id: str, folder_id: str):
    """Toggle a folder's selected/monitored flags without changing its match."""
    try:
        payload = request.get_json(silent=True) or {}
        selected = payload.get('selected')
        monitored = payload.get('monitored')

        manager = container.get_library_import_manager()
        folder = manager.update_folder_flags(
            session_id, folder_id,
            selected=selected if isinstance(selected, bool) else None,
            monitored=monitored if isinstance(monitored, bool) else None,
        )
        return jsonify({'success': True, 'folder': folder})
    except KeyError as exc:
        return jsonify({'success': False, 'error': f'Not found: {exc}'}), 404
    except Exception as exc:
        logger.error("Failed to update flags for %s/%s: %s", session_id, folder_id, exc, exc_info=True)
        return jsonify({'success': False, 'error': 'Internal error (details in server log)'}), 500


@library_import_api_bp.route('/metadata/search', methods=['GET'])
def search_metadata():
    """Search for candidate matches when the user wants to reassign a folder."""
    try:
        query = request.args.get('query')
        author = request.args.get('author')
        asin = request.args.get('asin')
        limit = request.args.get('limit', default=10, type=int)

        manager = container.get_library_import_manager()
        results = manager.matcher.search_candidates(query=query, author=author, asin=asin, limit=limit)
        return jsonify({'success': True, **results})
    except Exception as exc:
        logger.error("Library import metadata search failed: %s", exc, exc_info=True)
        return jsonify({'success': False, 'error': 'Internal error (details in server log)'}), 500


@library_import_api_bp.route('/scan/<session_id>/import', methods=['POST'])
def import_selected(session_id: str):
    """Catalog the selected book folders into the library in place."""
    try:
        payload = request.get_json(silent=True) or {}
        folder_ids = payload.get('folder_ids')
        if folder_ids is not None and not isinstance(folder_ids, list):
            return jsonify({'success': False, 'error': 'folder_ids must be a list if provided'}), 400

        manager = container.get_library_import_manager()
        outcome = manager.import_selected(session_id, folder_ids)
        return jsonify({'success': True, **outcome})
    except KeyError:
        return jsonify({'success': False, 'error': 'Scan session not found'}), 404
    except Exception as exc:
        logger.error("Library import commit failed for %s: %s", session_id, exc, exc_info=True)
        return jsonify({'success': False, 'error': 'Internal error (details in server log)'}), 500
