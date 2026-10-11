"""
Module Name: categories.py
Author: AuralArchivist
Created: July 7, 2026
Last Modified: October 9, 2026
Description:
    Audible category/genre discovery API. Serves the cached category tree,
    proxies live per-category book queries to Audible, and manages the user's
    favorited categories.

Location:
    /api/discovery/categories.py

Endpoints:

GET
    /api/discovery/categories                               Cached category tree
    /api/discovery/categories/<audible_category_id>/books   Live books for a category
    /api/discovery/categories/search                        Search cached categories
    /api/discovery/preferences                              List favorited categories

POST
    /api/discovery/categories/sync                          Manually trigger a category sync
    /api/discovery/preferences                              Toggle a favorited category
"""

from datetime import datetime
from typing import Any, Dict

from flask import Blueprint, jsonify, request

from core.logging import get_module_logger
from services.container import container
from services.metadata.categories import (
    AudibleCategorySyncService,
    DEFAULT_SORT_BY,
)

discovery_api = Blueprint('discovery_api', __name__, url_prefix='/api/discovery')


logger = get_module_logger("API.Discovery")


def run_category_sync() -> Dict[str, Any]:
    """Sync the Audible category tree. Safe to call from the background loop or a request handler."""
    db_service = container.get_database_service()
    config_service = container.get_config_service()

    sync_service = AudibleCategorySyncService(db_service)
    result = sync_service.sync_categories()

    if result.get("success"):
        config_service.update_config('discovery', 'last_synced_at', datetime.now().isoformat())

    return result


@discovery_api.route('/categories', methods=['GET'])
def get_categories():
    """Return the cached Audible category tree."""
    db_service = container.get_database_service()
    tree = db_service.categories.get_category_tree()
    return jsonify({'success': True, 'categories': tree})


@discovery_api.route('/categories/search', methods=['GET'])
def search_categories():
    """Search cached categories by name or path."""
    query = request.args.get('q', '').strip()
    db_service = container.get_database_service()
    results = db_service.categories.search_categories(query)
    return jsonify({'success': True, 'results': results})


@discovery_api.route('/categories/<audible_category_id>/books', methods=['GET'])
def get_books_for_category(audible_category_id: str):
    """Live-query Audible for books in a category (not cached)."""
    sort_by = request.args.get('sort', DEFAULT_SORT_BY)
    num_results = max(1, min(request.args.get('num_results', 50, type=int) or 50, 200))

    db_service = container.get_database_service()
    sync_service = AudibleCategorySyncService(db_service)

    try:
        books = sync_service.get_books_by_category(audible_category_id, sort_by=sort_by, num_results=num_results)
        return jsonify({'success': True, 'books': books, 'count': len(books)})
    except Exception as exc:
        logger.error(
            "Error fetching books for category",
            extra={"audible_category_id": audible_category_id, "error": str(exc)}, exc_info=True,
        )
        return jsonify({'success': False, 'error': str(exc), 'books': []}), 500


@discovery_api.route('/categories/sync', methods=['POST'])
def sync_categories_route():
    """Manually trigger a category tree sync (same code path as the background loop)."""
    result = run_category_sync()
    status = 200 if result.get("success") else 500
    return jsonify(result), status


@discovery_api.route('/preferences', methods=['GET'])
def list_preferences():
    """List the user's favorited categories."""
    enabled_only = request.args.get('enabled_only', 'false').lower() == 'true'
    db_service = container.get_database_service()
    favorites = db_service.categories.list_user_categories(enabled_only=enabled_only)
    return jsonify({'success': True, 'favorites': favorites})


@discovery_api.route('/preferences', methods=['POST'])
def set_preference():
    """Favorite or unfavorite a category."""
    payload = request.get_json(silent=True) or {}
    audible_category_id = payload.get('audible_category_id')
    enabled = bool(payload.get('enabled', True))

    if not audible_category_id:
        return jsonify({'success': False, 'error': 'audible_category_id is required'}), 400

    db_service = container.get_database_service()
    category = db_service.categories.get_category_by_audible_id(audible_category_id)
    success = db_service.categories.set_favorite(audible_category_id, enabled)

    if success and enabled:
        category_name = category.get('name') if category else audible_category_id
        logger.info("User selected category", extra={"category": category_name})

    status = 200 if success else 500
    return jsonify({'success': success}), status


@discovery_api.errorhandler(404)
def handle_not_found(e):
    """Handle 404 errors for this blueprint."""
    return jsonify({
        'success': False,
        'error': 'Endpoint not found',
        'message': 'The requested discovery API endpoint was not found'
    }), 404


@discovery_api.errorhandler(405)
def handle_method_not_allowed(e):
    """Handle 405 errors for this blueprint."""
    return jsonify({
        'success': False,
        'error': 'Method not allowed',
        'message': 'The HTTP method is not allowed for this endpoint'
    }), 405
