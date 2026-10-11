"""
Module Name: series.py
Author: AuralArchivist
Created: July 25, 2025
Last Modified: October 9, 2026
Description:
    Series routes for views and APIs that sync Audible metadata and missing books.

Location:
    /routes/series.py
"""
from collections import Counter
from flask import Blueprint, render_template, jsonify
from flask_login import login_required
from services.container import container
from core.logging import get_module_logger
from services.metadata.series import get_series_service

logger = get_module_logger("Routes.Series")

series_bp = Blueprint('series', __name__)


def _analyze_series_authors(books):
    """Determine primary author and ranked author candidates for a series."""
    author_counter = Counter()
    for book in books or []:
        author = (book.get('author') or '').strip()
        if author and author.lower() != 'unknown author':
            author_counter[author] += 1

    if not author_counter:
        return None, []

    ranked = sorted(author_counter.items(), key=lambda item: (-item[1], item[0].lower()))
    return ranked[0][0], [author for author, _ in ranked]

@series_bp.route('/')
@login_required
def series_list():
    """Display all series in the library"""
    try:
        logger.info("Series list page accessed")
        return render_template('series.html')
    except Exception as e:
        logger.error("Error loading series list page: %s", e, exc_info=True)
        return render_template('error.html', error=str(e)), 500


@series_bp.route('/<series_asin>')
@login_required
def series_detail(series_asin):
    """Display details of a specific series"""
    try:
        logger.info("Series detail page accessed: %s", series_asin)
        return render_template('series_detail.html', series_asin=series_asin)
    except Exception as e:
        logger.error("Error loading series detail page: %s", e, exc_info=True)
        return render_template('error.html', error=str(e)), 500


@series_bp.route('/api/list')
def api_series_list():
    """API endpoint to get all series with statistics"""
    try:
        db_service = container.get_database_service()
        
        try:
            updated = db_service.series.sync_library_status()
            logger.debug("Synced series library status: %s records updated", updated)
        except Exception as e:
            logger.warning("Failed to sync series library status: %s", e)
        
        series_list = db_service.series.get_all_series()
        
        return jsonify({
            'success': True,
            'series': series_list,
            'total_series': len(series_list)
        })
        
    except Exception as e:
        logger.error("Error fetching series list: %s", e, exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@series_bp.route('/api/<series_asin>/books')
def api_series_books(series_asin):
    """API endpoint to get all books in a specific series"""
    try:
        db_service = container.get_database_service()
        
        logger.info("Fetching series books for: %s", series_asin)
        
        try:
            updated = db_service.series.sync_library_status()
            logger.debug("Synced series library status: %s records updated", updated)
        except Exception as e:
            logger.warning("Failed to sync series library status: %s", e)
        
        series_metadata = db_service.series.get_series_by_asin(series_asin)
        logger.debug("Series metadata loaded", extra={
            "series_asin": series_asin,
            "has_cover": bool(series_metadata and series_metadata.get('cover_url'))
        })
        
        if not series_metadata:
            return jsonify({
                'success': False,
                'error': 'Series not found'
            }), 404
        
        books = db_service.series.get_series_books(series_asin)
        logger.debug("Retrieved series books", extra={
            "series_asin": series_asin,
            "book_count": len(books)
        })
        
        total_books = len(books)
        owned_books = sum(1 for book in books if book['in_library'])
        missing_books = total_books - owned_books

        primary_author, author_candidates = _analyze_series_authors(books)

        series_description = (series_metadata.get('description') or '').strip()

        book_one = next(
            (b for b in books if str(b.get('sequence', '')).strip() == '1'),
            books[0] if books else None
        )
        if not series_description and book_one:
            series_description = (book_one.get('summary') or '').strip()

        series_cover_url = (series_metadata.get('cover_url') or '').strip()
        if not series_cover_url and book_one:
            series_cover_url = (book_one.get('cover_image') or '').strip()

        return jsonify({
            'success': True,
            'series_asin': series_asin,
            'series_title': series_metadata['series_title'],
            'series_cover_url': series_cover_url,
            'series_description': series_description,
            'books': books,
            'statistics': {
                'total_books': total_books,
                'owned_books': owned_books,
                'missing_books': missing_books,
                'completion_percentage': round((owned_books / total_books * 100), 1) if total_books > 0 else 0
            },
            'primary_author': primary_author,
            'author_candidates': author_candidates,
            'import_context_available': bool(primary_author)
        })
        
    except Exception as e:
        logger.error("Error fetching series books: %s", e, exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@series_bp.route('/api/refresh-series/<series_asin>', methods=['POST'])
def api_refresh_series(series_asin):
    """API endpoint to refresh data for a specific series from Audible"""
    try:
        if not series_asin:
            return jsonify({
                'success': False,
                'error': 'Series ASIN is required'
            }), 400
        
        logger.info("Series refresh requested: %s", series_asin)
        
        result = get_series_service(reinitialize=True).refresh_series(series_asin)
        
        if result.get('success'):
            return jsonify(result)
        else:
            return jsonify(result), 500
        
    except Exception as e:
        logger.error("Error refreshing series: %s", e, exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

