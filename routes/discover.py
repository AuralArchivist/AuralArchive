"""
Module Name: discover.py
Author: AuralArchivist
Created: July 19, 2025
Last Modified: October 9, 2026
Description:
    Discovery dashboard routes and supporting APIs for library trends and recommendations.

Location:
    /routes/discover.py
"""

import math
import re
from collections import Counter
from typing import List, Dict, Any

from flask import Blueprint, render_template, request, jsonify, url_for
from flask_login import login_required

from services.metadata.covers import get_cached_book_cover_url
from services.container import container

from core.logging import get_module_logger
from core.text import runtime_to_minutes

discover_bp = Blueprint('discover', __name__)
logger = get_module_logger("Routes.Discover")

_AUTHOR_SPLIT_PATTERN = re.compile(r"\s*(?:,|&|;|\band\b|\bwith\b)\s*", re.IGNORECASE)


def _split_author_field(author_value: str) -> List[str]:
    if not author_value:
        return []
    parts = _AUTHOR_SPLIT_PATTERN.split(str(author_value))
    return [" ".join(part.strip().split()) for part in parts if part and part.strip()]


def _format_recent_books(books: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    formatted: List[Dict[str, Any]] = []
    for book in books:
        cover_url = get_cached_book_cover_url(book) or book.get('Cover Image') or url_for(
            'static', filename='images/auralarchive_logo.png'
        )
        summary = book.get('Summary') or ''
        summary_snippet = summary[:180].rstrip() + ('...' if len(summary) > 180 else '') if summary else 'No summary available yet.'

        library_status = book.get('library_status') or 'wanted'

        formatted.append({
            'title': book.get('Title', 'Unknown Title'),
            'author': book.get('Author', 'Unknown Author'),
            'cover_url': cover_url,
            'status': library_status.lower(),
            'asin': book.get('ASIN', ''),
            'summary': summary_snippet,
            'language': book.get('Language', 'Unknown'),
            'runtime': book.get('Runtime', 'Unknown'),
            'series': book.get('Series', 'N/A'),
        })

    return formatted


@discover_bp.route('/discover')
@login_required
def discover_page():
    """Render the discover dashboard with library insights and discovery tools."""
    try:
        db_service = container.get_database_service()

        books = db_service.books.get_all_books()
        all_authors = db_service.authors.get_all_authors()

        total_books = len(books)
        total_authors = len(all_authors)

        total_hours = 0.0
        series_counter: Counter = Counter()
        language_counter: Counter = Counter()
        author_counter: Counter = Counter()

        for book in books:
            total_hours += runtime_to_minutes(book.get('Runtime')) / 60

            series_value = str(book.get('Series') or '').strip()
            if series_value and series_value not in {'N/A', 'Unknown', ''}:
                series_counter[series_value] += 1

            language_value = str(book.get('Language') or '').strip()
            if language_value:
                language_counter[language_value] += 1

            for author_name in _split_author_field(book.get('Author')):
                if author_name != 'Unknown Author':
                    author_counter[author_name] += 1

        recent_books = sorted(books, key=lambda item: item.get('Created At', ''), reverse=True)[:6]
        formatted_recent = _format_recent_books(recent_books)

        top_authors = [
            {'name': name, 'count': count}
            for name, count in author_counter.most_common(6)
        ]

        trending_series = [
            {'name': name, 'count': count}
            for name, count in series_counter.most_common(6)
        ]

        top_languages = [
            {'name': name, 'count': count}
            for name, count in language_counter.most_common(6)
        ]

        avg_listen_length = 0
        if total_books:
            avg_listen_length = math.ceil((total_hours * 60) / total_books)

        recommendations_configured = False
        try:
            recommendations_service = container.get_audible_recommendations_service()
            recommendations_configured = recommendations_service.is_configured()
        except Exception as config_error:
            logger.debug("Recommendation service status unavailable: %s", config_error)

        return render_template(
            'discover.html',
            title='Discover - AuralArchive',
            total_books=total_books,
            total_authors=total_authors,
            total_hours=int(total_hours),
            avg_listen_length=avg_listen_length,
            recent_books=formatted_recent,
            top_authors=top_authors,
            trending_series=trending_series,
            top_languages=top_languages,
            recommendations_configured=recommendations_configured,
        )

    except Exception as exc:
        logger.error("Error loading discover page: %s", exc, exc_info=True)
        return render_template(
            'discover.html',
            title='Discover - AuralArchive',
            total_books=0,
            total_authors=0,
            total_hours=0,
            avg_listen_length=0,
            recent_books=[],
            top_authors=[],
            trending_series=[],
            top_languages=[],
            recommendations_configured=False,
        )


@discover_bp.route('/discover/api/recommendations')
def get_recommendations():
    """API endpoint to fetch library-based recommendations."""
    try:
        num_results = request.args.get('num_results', 20, type=int)
        recommendations = _get_library_based_recommendations(num_results)

        return jsonify({
            'success': True,
            'recommendations': recommendations,
            'count': len(recommendations),
            'configured': True,
        })

    except Exception as e:
        logger.error("Error getting library-based recommendations: %s", e, exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e),
            'recommendations': [],
        }), 500

def _get_library_based_recommendations(num_results: int = 20) -> List[Dict[str, Any]]:
    """Get recommendations based on library analysis only."""
    try:
        recommendations_service = container.get_audible_recommendations_service()
        
        if recommendations_service.is_configured():
            return recommendations_service.get_library_based_recommendations(num_results)
        else:
            logger.info("Audible not configured, returning empty recommendations")
            return []
            
    except Exception as e:
        logger.error("Library-based recommendations failed: %s", e, exc_info=True)
        return []
