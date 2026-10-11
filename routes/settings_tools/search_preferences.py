"""
Module Name: search_preferences.py
Author: AuralArchivist
Created: June 26, 2026
Last Modified: October 9, 2026
Description:
    Settings helper for the search_preferences DB table (format/bitrate/
    quality preferences that drive release-quality scoring in
    services/acquisition/search/quality_assessor.py). Distinct from /api/search/config,
    which is config.txt's unrelated auto_search section.

Location:
    /routes/settings_tools/search_preferences.py
"""

from flask import jsonify, request

from services.container import container
from core.logging import get_module_logger

logger = get_module_logger("Routes.Settings.SearchPreferences")


def handle_get_search_preferences():
    """Get current search/quality preferences."""
    try:
        db_service = container.get_database_service()
        prefs = db_service.search_preferences.get_search_preferences()
        return jsonify({'success': True, 'preferences': prefs})
    except Exception as e:
        logger.error(f"Error getting search preferences: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


def handle_update_search_preferences():
    """Update search/quality preferences."""
    try:
        updates = request.get_json(silent=True) or {}
        if not updates:
            return jsonify({'success': False, 'error': 'No preference data provided'}), 400

        db_service = container.get_database_service()
        success = db_service.search_preferences.set_search_preferences(updates)
        return jsonify({
            'success': success,
            'message': 'Search preferences updated' if success else 'Failed to update search preferences'
        }), (200 if success else 500)
    except Exception as e:
        logger.error(f"Error updating search preferences: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500
