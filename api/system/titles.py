"""
Module Name: titles.py
Author: AuralArchivist
Created: July 6, 2026
Last Modified: October 9, 2026
Description:
    Hide or unmonitor titles (books by ASIN), owned or not.

Location:
    /api/system/titles.py

Endpoints:

GET
    /api/titles/hidden             List every hidden title, owned or not

PUT
    /api/titles/<asin>/monitored   Hide/unhide a title (owned or not)
"""

from flask import Blueprint, jsonify, request

from services.container import container
from core.logging import get_module_logger

logger = get_module_logger("API.System.Titles")

titles_api_bp = Blueprint('titles_api', __name__)


@titles_api_bp.route('/<asin>/monitored', methods=['PUT'])
def update_title_monitored(asin):
    """Hide or unhide a title by ASIN, regardless of ownership."""
    try:
        payload = request.get_json(silent=True) or {}
        if 'monitored' not in payload:
            return jsonify({'error': 'monitored is required'}), 400

        monitored = bool(payload.get('monitored'))
        db_service = container.get_database_service()

        book = db_service.books.get_book_by_asin(asin)
        if book:
            if db_service.books.set_book_monitored(book['ID'], monitored):
                return jsonify({'success': True, 'monitored': monitored, 'owned': True})
            return jsonify({'error': 'Failed to update monitored'}), 500

        if monitored:
            db_service.title_visibility.unhide_title(asin)
        else:
            db_service.title_visibility.hide_title(
                asin,
                title=payload.get('title'),
                author=payload.get('author'),
                series_asin=payload.get('series_asin'),
                series_title=payload.get('series_title'),
                source_context=payload.get('source_context'),
            )
        return jsonify({'success': True, 'monitored': monitored, 'owned': False})

    except Exception as e:
        logger.error("Error updating title monitored flag for %s: %s", asin, e, exc_info=True)
        return jsonify({'error': 'Failed to update monitored'}), 500


@titles_api_bp.route('/hidden', methods=['GET'])
def list_hidden_titles():
    """List every hidden title across the library, owned or not."""
    try:
        db_service = container.get_database_service()

        owned = [
            {
                'asin': book.get('ASIN'),
                'title': book.get('Title'),
                'author': book.get('Author'),
                'series': book.get('Series'),
                'series_asin': book.get('series_asin'),
                'cover_image': book.get('Cover Image'),
                'owned': True,
            }
            for book in db_service.books.get_unmonitored_books()
        ]

        not_owned = [
            {
                'asin': entry.get('asin'),
                'title': entry.get('title'),
                'author': entry.get('author'),
                'series': entry.get('series_title'),
                'series_asin': entry.get('series_asin'),
                'source_context': entry.get('source_context'),
                'hidden_at': entry.get('hidden_at'),
                'owned': False,
            }
            for entry in db_service.title_visibility.list_hidden_titles()
        ]

        return jsonify({'success': True, 'titles': owned + not_owned})

    except Exception as e:
        logger.error("Error listing hidden titles: %s", e, exc_info=True)
        return jsonify({'error': 'Failed to list hidden titles'}), 500
