"""
Module Name: authors.py
Author: AuralArchivist
Created: July 15, 2025
Last Modified: October 9, 2026
Description:
    Route handlers for author dashboards, metadata enrichment, and catalog imports.

Location:
    /routes/authors.py
"""
from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required
from services.container import container
from services.metadata.covers import get_cached_author_image_url
from typing import List, Dict, Any
from datetime import datetime
from collections import defaultdict

from core.logging import get_module_logger
from core.status_labels import library_status_to_ui
from core.language_matching import language_allowed, format_language_labels
from core.text import runtime_to_minutes
from services.settings.languages import get_preferred_languages as get_preferred_author_languages
from services.metadata.authors import (
    enhance_author_with_external_data,
    ensure_author_metadata_cached,
    gather_import_candidates,
    persist_author_catalog,
    update_author_from_asin,
)

authors_bp = Blueprint('authors', __name__)


logger = get_module_logger("Routes.Authors")



# --- Utility functions - enhanced --------------------------------------------

def format_author_for_template(author_name: str, books: List[Dict], enhanced: bool = False) -> Dict[str, Any]:
    """Enhanced format author data for MediaVault template with advanced analytics."""
    total_books = len(books)
    
    series_books = defaultdict(list)
    standalone_books = []
    
    for book in books:
        series = book.get('Series', 'N/A')
        if series == 'N/A':
            standalone_books.append(book)
        else:
            series_books[series].append(book)
    
    total_hours = 0
    runtimes = []
    for book in books:
        minutes = runtime_to_minutes(book.get('Runtime'))
        if minutes:
            total_hours += minutes / 60
            runtimes.append(minutes / 60)
    
    status_counts = {}
    owned_count = 0
    missing_count = 0
    
    for book in books:
        status = book.get('library_status', 'unknown')
        status_counts[status] = status_counts.get(status, 0) + 1

        if status == 'owned':
            owned_count += 1
        else:
            missing_count += 1
    
    books_with_dates = [b for b in books if b.get('Created At')]
    recent_book = max(books_with_dates, key=lambda x: x.get('Created At', ''), default=None)
    
    narrators = set()
    publishers = set()
    for book in books:
        if book.get('Narrator') and book.get('Narrator') != 'Unknown':
            narrators.add(book.get('Narrator'))
        if book.get('Publisher') and book.get('Publisher') != 'Unknown':
            publishers.add(book.get('Publisher'))
    
    db_service = container.get_database_service()
    author_metadata = db_service.authors.get_author_metadata(author_name)
    
    completion_rate = (owned_count / total_books * 100) if total_books > 0 else 0
    
    publisher_counts = {}
    for book in books:
        publisher = book.get('Publisher', 'Unknown')
        if publisher != 'Unknown':
            publisher_counts[publisher] = publisher_counts.get(publisher, 0) + 1
    primary_publisher = max(publisher_counts, key=publisher_counts.get, default='Unknown') if publisher_counts else 'Unknown'
    
    author_data = {
        'name': author_name,
        'book_count': total_books,
        'series_count': len(series_books),
        'standalone_count': len(standalone_books),
        'total_hours': int(total_hours),
        'avg_runtime': round(sum(runtimes) / len(runtimes), 1) if runtimes else 0,
        'status_counts': status_counts,
        'owned_count': owned_count,
        'missing_count': missing_count,
        'completion_rate': round(completion_rate, 1),
        'primary_publisher': primary_publisher,
        'recent_book': recent_book.get('Title', 'Unknown') if recent_book else 'No books',
        'cover_image': recent_book.get('Cover Image') if recent_book else None,
        'most_common_narrator': max(narrators, key=lambda n: sum(1 for b in books if b.get('Narrator') == n), default='Unknown') if narrators else 'Unknown',
        
        'author_image': get_cached_author_image_url(author_metadata) or author_metadata.get('author_image_url'),
        'author_bio': author_metadata.get('author_bio'),
        'audible_author_id': author_metadata.get('audible_author_id'),
        'author_page_url': author_metadata.get('author_page_url'),
        'last_fetched_at': author_metadata.get('last_fetched_at'),
        
        'series_info': {
            'series_names': list(series_books.keys()),
            'series_count': len(series_books),
            'series_books': dict(series_books)
        }
    }
    
    if enhanced:
        author_data['enhanced'] = True
        author_data['analytics_timestamp'] = datetime.now().isoformat()
    
    return author_data

def calculate_author_similarity(author1_data: Dict, author2_data: Dict) -> float:
    """Calculate similarity score between two authors based on various factors."""
    score = 0.0
    
    series1 = set(author1_data.get('series_info', {}).get('series_names', []))
    series2 = set(author2_data.get('series_info', {}).get('series_names', []))
    if series1 and series2:
        series_overlap = len(series1 & series2) / len(series1 | series2)
        score += series_overlap * 0.3
    
    pub1 = author1_data.get('primary_publisher', '')
    pub2 = author2_data.get('primary_publisher', '')
    if pub1 and pub2 and pub1 == pub2 and pub1 != 'Unknown':
        score += 0.2
    
    count1 = author1_data.get('book_count', 0)
    count2 = author2_data.get('book_count', 0)
    if count1 > 0 and count2 > 0:
        count_ratio = min(count1, count2) / max(count1, count2)
        score += count_ratio * 0.1
    
    span1 = author1_data.get('publication_span', {})
    span2 = author2_data.get('publication_span', {})
    if (span1.get('earliest') and span1.get('latest') and 
        span2.get('earliest') and span2.get('latest')):
        
        overlap_start = max(span1['earliest'], span2['earliest'])
        overlap_end = min(span1['latest'], span2['latest'])
        
        if overlap_end >= overlap_start:
            overlap_years = overlap_end - overlap_start + 1
            total_span = max(span1['latest'], span2['latest']) - min(span1['earliest'], span2['earliest']) + 1
            timeline_similarity = overlap_years / total_span if total_span > 0 else 0
            score += timeline_similarity * 0.2
    
    return score

# --- Main author routes - enhanced -------------------------------------------

@authors_bp.route('/')
@login_required
def authors_page():
    """Enhanced authors page with MediaVault design and advanced analytics."""
    try:
        db_service = container.get_database_service()
        all_authors = db_service.authors.get_all_authors()
        
        sort_by = request.args.get('sort', 'book_count')
        order = request.args.get('order', 'desc')
        filter_by = request.args.get('filter', '')
        enhance = request.args.get('enhance', 'false').lower() == 'true'
        
        logger.debug(f"Loading authors page: {len(all_authors)} authors, sort={sort_by}, enhance={enhance}")
        
        authors_data = []
        for author in all_authors:
            books = db_service.authors.get_books_by_author(author)
            
            if filter_by:
                books = [book for book in books if (book.get('library_status') or '').lower() == filter_by.lower()]
                if not books:
                    continue
            
            formatted_author = format_author_for_template(author, books, enhanced=enhance)
            
            if enhance:
                formatted_author = enhance_author_with_external_data(author, formatted_author)
            
            authors_data.append(formatted_author)
        
        reverse_order = order == 'desc'
        if sort_by == 'name':
            authors_data.sort(key=lambda x: x['name'].lower(), reverse=reverse_order)
        elif sort_by == 'hours':
            authors_data.sort(key=lambda x: x['total_hours'], reverse=reverse_order)
        elif sort_by == 'completion':
            authors_data.sort(key=lambda x: x['completion_rate'], reverse=reverse_order)
        else:
            authors_data.sort(key=lambda x: x['book_count'], reverse=reverse_order)
        
        summary_stats = {
            'total_authors': len(authors_data),
            'total_books': sum(author['book_count'] for author in authors_data),
            'total_hours': sum(author['total_hours'] for author in authors_data),
            'avg_books_per_author': round(sum(author['book_count'] for author in authors_data) / len(authors_data), 1) if authors_data else 0,
            'top_author': max(authors_data, key=lambda x: x['book_count'], default={'name': 'None', 'book_count': 0})
        }
        
        return render_template('authors.html',
                             title='Authors - Enhanced Analytics',
                             authors=authors_data,
                             summary_stats=summary_stats,
                             sort_by=sort_by,
                             order=order,
                             filter_by=filter_by,
                             enhanced=enhance)
    
    except Exception as e:
        logger.error(f"Error loading authors page: {e}", exc_info=True)
        return render_template('authors.html',
                             title='Authors',
                             authors=[],
                             summary_stats={'total_authors': 0},
                             error=str(e))

@authors_bp.route('/<author_name>')
@login_required
def author_detail_page(author_name):
    """Detailed author page with analytics."""
    if author_name in ('favicon.ico', 'robots.txt', 'sitemap.xml'):
        from flask import abort
        abort(404)
    
    try:
        db_service = container.get_database_service()
        books = db_service.authors.get_books_by_author(author_name)
        
        if not books:
            return render_template('author_detail.html',
                                 title=f'Author: {author_name}',
                                 author_name=author_name,
                                 error='Author not found'), 404

        ensure_author_metadata_cached(author_name, refresh_if_stale=True)

        library_book_map = {}
        library_asins = set()
        for book in books:
            asin = book.get('ASIN')
            if asin:
                library_asins.add(asin)
                library_book_map[asin] = book

        hidden_asins = {entry.get('asin') for entry in db_service.title_visibility.list_hidden_titles() if entry.get('asin')}

        def _resolve_monitored(asin: str, in_library: bool, library_info: dict) -> bool:
            if in_library:
                monitored_value = library_info.get('monitored')
                return True if monitored_value is None else bool(monitored_value)
            return asin not in hidden_asins
        
        author_data = format_author_for_template(author_name, books, enhanced=True)
        author_data = enhance_author_with_external_data(author_name, author_data)
        
        hybrid_catalog = []
        catalog_error = None
        try:
            hybrid_service = container.get_hybrid_audiobook_service()
            hybrid_catalog = hybrid_service.get_author_books_from_audible(
                author_name
            )
        except Exception as catalog_exc:
            catalog_error = str(catalog_exc)
            logger.warning(f"Hybrid catalogue lookup failed for {author_name}: {catalog_exc}")
        
        catalog_books = []
        missing_books = []
        series_catalog = defaultdict(list)
        catalog_standalones = []

        preferred_languages = get_preferred_author_languages()
        language_filtered_count = 0
        if preferred_languages:
            logger.debug(
                "Applying author language filter for %s (allowed: %s)",
                author_name,
                ', '.join(sorted(preferred_languages))
            )
        
        def _to_number(value: str) -> float:
            try:
                return float(str(value).strip())
            except ValueError:
                return 9999
        
        if hybrid_catalog:
            for catalog_book in hybrid_catalog:
                asin = catalog_book.get('ASIN') or ''
                in_library = asin in library_asins if asin else False
                library_info = library_book_map.get(asin, {}) if asin else {}
                raw_status = (library_info.get('library_status') or '').strip().lower()
                status = library_status_to_ui(raw_status) if raw_status else ('Owned' if in_library else 'Wanted')
                language_value = (
                    catalog_book.get('Language')
                    or catalog_book.get('language')
                    or library_info.get('Language')
                )
                if isinstance(language_value, str):
                    language_value = language_value.strip()
                entry = {
                    'title': catalog_book.get('Title') or library_info.get('Title', 'Unknown Title'),
                    'asin': asin,
                    'series': catalog_book.get('Series', library_info.get('Series', 'N/A')) or 'N/A',
                    'sequence': catalog_book.get('Sequence', library_info.get('Sequence', '')),
                    'runtime': catalog_book.get('Runtime', library_info.get('Runtime', '')),
                    'rating': catalog_book.get('Overall Rating', library_info.get('Overall Rating', '')),
                    'release_date': catalog_book.get('Release Date', library_info.get('Release Date', 'Unknown')),
                    'summary': catalog_book.get('Summary', library_info.get('Summary')),
                    'cover_image': catalog_book.get('Cover Image', library_info.get('Cover Image')),
                    'status': status,
                    'in_library': in_library,
                    'source': catalog_book.get('enhanced_by', 'audible'),
                    'language': language_value,
                    'monitored': _resolve_monitored(asin, in_library, library_info)
                }
                if not language_allowed(entry.get('language'), preferred_languages):
                    language_filtered_count += 1
                    continue
                catalog_books.append(entry)
                if not in_library:
                    missing_books.append(entry)
                if entry['series'] and entry['series'] != 'N/A':
                    series_catalog[entry['series']].append(entry)
                else:
                    catalog_standalones.append(entry)
        else:
            for library_book in books:
                asin = library_book.get('ASIN', '')
                language_value = library_book.get('Language', '')
                if isinstance(language_value, str):
                    language_value = language_value.strip()
                entry = {
                    'title': library_book.get('Title', 'Unknown Title'),
                    'asin': asin,
                    'series': library_book.get('Series', 'N/A'),
                    'sequence': library_book.get('Sequence', ''),
                    'runtime': library_book.get('Runtime', ''),
                    'rating': library_book.get('Overall Rating', ''),
                    'release_date': library_book.get('Release Date', 'Unknown'),
                    'summary': library_book.get('Summary'),
                    'cover_image': library_book.get('Cover Image'),
                    'status': (library_book.get('library_status') or 'owned').title(),
                    'in_library': True,
                    'source': 'library',
                    'language': language_value,
                    'monitored': _resolve_monitored(asin, True, library_book)
                }
                if not language_allowed(entry.get('language'), preferred_languages):
                    language_filtered_count += 1
                    continue
                catalog_books.append(entry)
                if entry['series'] and entry['series'] != 'N/A':
                    series_catalog[entry['series']].append(entry)
                else:
                    catalog_standalones.append(entry)

        catalog_asins = {entry.get('asin') for entry in catalog_books if entry.get('asin')}
        for library_book in books:
            asin = library_book.get('ASIN')
            if not asin or asin in catalog_asins:
                continue

            language_value = library_book.get('Language', '')
            if isinstance(language_value, str):
                language_value = language_value.strip()

            entry = {
                'title': library_book.get('Title', 'Unknown Title'),
                'asin': asin,
                'series': library_book.get('Series', 'N/A'),
                'sequence': library_book.get('Sequence', ''),
                'runtime': library_book.get('Runtime', ''),
                'rating': library_book.get('Overall Rating', ''),
                'release_date': library_book.get('Release Date', 'Unknown'),
                'summary': library_book.get('Summary'),
                'cover_image': library_book.get('Cover Image'),
                'status': (library_book.get('library_status') or 'owned').title(),
                'in_library': True,
                'source': 'library',
                'language': language_value,
                'monitored': _resolve_monitored(asin, True, library_book)
            }

            if not language_allowed(entry.get('language'), preferred_languages):
                language_filtered_count += 1
                continue

            catalog_books.append(entry)
            catalog_asins.add(asin)
            if entry['series'] and entry['series'] != 'N/A':
                series_catalog[entry['series']].append(entry)
            else:
                catalog_standalones.append(entry)
        
        for series_name in series_catalog:
            series_catalog[series_name].sort(key=lambda x: (_to_number(x.get('sequence')), x.get('title')))
        ordered_series_catalog = dict(sorted(series_catalog.items(), key=lambda item: item[0].lower()))
        catalog_standalones.sort(key=lambda x: x.get('title', '').lower())
        catalog_books.sort(key=lambda x: (x.get('series', 'ZZZ').lower(), _to_number(x.get('sequence')), x.get('title', '').lower()))
        missing_books.sort(key=lambda x: (x.get('series', 'ZZZ').lower(), _to_number(x.get('sequence')), x.get('title', '').lower()))

        for index, book in enumerate(catalog_books, start=1):
            book['catalogue_index'] = index
        
        if catalog_books:
            owned_catalog = sum(1 for book in catalog_books if book.get('in_library'))
            coverage_percentage = round((owned_catalog / len(catalog_books)) * 100, 1)
        elif hybrid_catalog:
            coverage_percentage = 0
        else:
            coverage_percentage = 100

        display_language_values = format_language_labels(preferred_languages)
        
        catalogue_context = {
            'books': catalog_books,
            'missing_books': missing_books,
            'series_catalog': ordered_series_catalog,
            'standalone_catalog': catalog_standalones,
            'coverage_percentage': coverage_percentage,
            'catalogue_error': catalog_error,
            'catalogue_size': len(catalog_books),
            'language_filter_applied': bool(preferred_languages),
            'language_filter_values': display_language_values,
            'language_filtered_count': language_filtered_count
        }
        
        return render_template('author_detail.html',
                             title=f'Author: {author_name}',
                             author_data=author_data,
                             catalogue=catalogue_context)
    
    except Exception as e:
        logger.error(f"Error loading author detail for {author_name}: {e}", exc_info=True)
        return render_template('author_detail.html',
                             title=f'Author: {author_name}',
                             author_name=author_name,
                             error=str(e)), 500


_IMPORT_COUNT_FIELDS = ('language_skipped', 'missing_raw', 'already_in_library', 'total_candidates')


def _request_value(payload: Dict[str, Any], name: str) -> str:
    return (payload.get(name) or request.form.get(name) or '').strip()


def _plural(count: int) -> str:
    return 's' if count != 1 else ''


def _owned_note(candidates: Dict[str, Any]) -> str:
    owned = candidates['already_in_library']
    return f" ({owned} already owned, skipped)" if owned else ""


def _group_empty_message(default: str, all_owned: str, language: str):
    """'Nothing to import' wording for series, catalog and standalone imports."""
    def message(candidates: Dict[str, Any]) -> str:
        if candidates['already_in_library'] and not candidates['language_skipped']:
            return all_owned
        if candidates['language_skipped']:
            return language
        return default
    return message


def _import_from_catalog(author_name: str, empty_message, success_message, *,
                         require_import: bool = False, **filters):
    """Gather an author's catalog candidates (with filters), persist them and
    build the JSON response shared by every import endpoint."""
    candidates = gather_import_candidates(author_name, get_preferred_author_languages(), **filters)
    if candidates.get('error'):
        return jsonify({'success': False, 'error': candidates['error']}), 500

    counts = {field: candidates[field] for field in _IMPORT_COUNT_FIELDS}
    if not candidates['subset_formatted']:
        return jsonify({'success': False, 'error': empty_message(candidates), **counts}), 200

    result = persist_author_catalog(author_name, candidates['subset_raw'], candidates['subset_formatted'])
    imported = result.get('books_successful', 0)
    return jsonify({
        'success': imported > 0 if require_import else True,
        'imported': imported,
        'failed': result.get('books_failed', 0),
        'series_imported': result.get('series_successful', 0),
        'series_failed': result.get('series_failed', 0),
        **counts,
        'message': success_message(imported, candidates),
    })


@authors_bp.post('/api/import-series')
def import_author_series():
    """Persist all books for a specific series into the library database."""
    payload = request.get_json(silent=True) or {}
    author_name = _request_value(payload, 'author_name')
    series_name = _request_value(payload, 'series_name')
    if not author_name:
        return jsonify({'success': False, 'error': 'Author name is required.'}), 400
    if not series_name:
        return jsonify({'success': False, 'error': 'Series name is required.'}), 400
    return _import_from_catalog(
        author_name,
        _group_empty_message('No matching titles found for import.',
                             f"All titles in series '{series_name}' are already in your library.",
                             f"No titles matched your language filters for series '{series_name}'."),
        lambda n, c: f"Imported {n} title{_plural(n)} from series '{series_name}'." + _owned_note(c),
        series_name=series_name,
    )


@authors_bp.post('/api/import-author')
def import_author_catalog():
    """Persist the entire filtered author catalog into the library database."""
    payload = request.get_json(silent=True) or {}
    author_name = _request_value(payload, 'author_name')
    if not author_name:
        return jsonify({'success': False, 'error': 'Author name is required.'}), 400
    return _import_from_catalog(
        author_name,
        _group_empty_message('No catalog titles available for import.',
                             f"All catalog titles for {author_name} are already in your library.",
                             'No catalog titles matched your language filters.'),
        lambda n, c: f"Imported {n} catalog title{_plural(n)} for {author_name}." + _owned_note(c),
    )


@authors_bp.post('/api/import-standalone')
def import_author_standalones():
    """Persist all standalone catalog titles for an author into the library."""
    payload = request.get_json(silent=True) or {}
    author_name = _request_value(payload, 'author_name')
    if not author_name:
        return jsonify({'success': False, 'error': 'Author name is required.'}), 400
    return _import_from_catalog(
        author_name,
        _group_empty_message('No standalone titles available for import.',
                             f"All standalone titles for {author_name} are already in your library.",
                             'No standalone titles matched your language filters.'),
        lambda n, c: f"Imported {n} standalone title{_plural(n)} for {author_name}." + _owned_note(c),
        standalone_only=True,
    )


def _single_book_empty_message(candidates: Dict[str, Any]) -> str:
    if candidates['already_in_library']:
        return 'This title is already in your library.'
    if candidates['language_skipped']:
        return 'This title does not match your language filters.'
    if candidates['missing_raw']:
        return 'Full metadata for this title was not available.'
    return 'Catalog title not available for import.'


@authors_bp.post('/api/import-book')
def import_single_catalog_book():
    """Persist a single catalog title for an author into the library."""
    payload = request.get_json(silent=True) or {}
    author_name = _request_value(payload, 'author_name')
    asin = _request_value(payload, 'asin')
    if not author_name:
        return jsonify({'success': False, 'error': 'Author name is required.'}), 400
    if not asin:
        return jsonify({'success': False, 'error': 'ASIN is required.'}), 400
    return _import_from_catalog(
        author_name,
        _single_book_empty_message,
        lambda n, c: 'Title imported successfully.' if n else 'No updates were applied.',
        require_import=True,
        asin_filter=asin,
    )


def get_author_recommendations(target_author: str, target_data: Dict) -> List[Dict]:
    """Get author recommendations based on similarity analysis."""
    try:
        db_service = container.get_database_service()
        all_authors = db_service.authors.get_all_authors()
        
        recommendations = []
        
        for author in all_authors:
            if author == target_author:
                continue
            
            books = db_service.authors.get_books_by_author(author)
            if len(books) < 2:
                continue
            
            author_data = format_author_for_template(author, books)
            similarity_score = calculate_author_similarity(target_data, author_data)
            
            if similarity_score > 0.3:
                recommendations.append({
                    'author': author,
                    'similarity_score': round(similarity_score, 3),
                    'book_count': author_data['book_count'],
                    'primary_publisher': author_data['primary_publisher'],
                    'reason': get_similarity_reason(target_data, author_data)
                })
        
        recommendations.sort(key=lambda x: x['similarity_score'], reverse=True)
        return recommendations[:10]
        
    except Exception as e:
        logger.error(f"Error getting recommendations for {target_author}: {e}", exc_info=True)
        return []

def get_similarity_reason(author1_data: Dict, author2_data: Dict) -> str:
    """Generate human-readable reason for author similarity."""
    reasons = []
    
    if (author1_data.get('primary_publisher') == author2_data.get('primary_publisher') and 
        author1_data.get('primary_publisher') != 'Unknown'):
        reasons.append(f"Same publisher ({author1_data['primary_publisher']})")
    
    count1 = author1_data.get('book_count', 0)
    count2 = author2_data.get('book_count', 0)
    if count1 > 0 and count2 > 0:
        ratio = min(count1, count2) / max(count1, count2)
        if ratio > 0.7:
            reasons.append(f"Similar productivity ({min(count1, count2)}-{max(count1, count2)} books)")
    
    return "; ".join(reasons) if reasons else "Similar publishing patterns"

# --- API endpoints - enhanced ------------------------------------------------

@authors_bp.route('/api/list')
def api_get_authors():
    """Enhanced API endpoint for getting authors with analytics."""
    try:
        db_service = container.get_database_service()
        
        include_stats = request.args.get('include_stats', 'false').lower() == 'true'
        sort_by = request.args.get('sort', 'book_count')
        limit = request.args.get('limit', type=int)
        search = request.args.get('search', '').strip()
        
        all_authors = db_service.authors.get_all_authors()
        authors_data = []
        
        for author in all_authors:
            if search and search.lower() not in author.lower():
                continue
            
            books = db_service.authors.get_books_by_author(author)
            
            if include_stats:
                author_info = format_author_for_template(author, books)
            else:
                author_info = {
                    'name': author,
                    'book_count': len(books)
                }
            
            authors_data.append(author_info)
        
        if sort_by == 'name':
            authors_data.sort(key=lambda x: x['name'].lower())
        elif sort_by == 'hours' and include_stats:
            authors_data.sort(key=lambda x: x.get('total_hours', 0), reverse=True)
        else:
            authors_data.sort(key=lambda x: x['book_count'], reverse=True)
        
        if limit:
            authors_data = authors_data[:limit]

        missing_metadata = include_stats and any(not a.get('audible_author_id') for a in authors_data)
        if missing_metadata:
            try:
                container.get_metadata_refresh_scheduler().request_missing_authors()
            except Exception as exc:
                logger.debug(f"Could not queue missing author metadata fetch: {exc}")

        return jsonify({
            'success': True,
            'metadata_pending': bool(missing_metadata),
            'authors': authors_data,
            'count': len(authors_data),
            'total_authors': len(all_authors),
            'include_stats': include_stats
        })
    
    except Exception as e:
        logger.error(f"Error getting authors list: {e}", exc_info=True)
        return jsonify({'error': f'Failed to get authors: {str(e)}'}), 500

# --- Author management and metadata ------------------------------------------

@authors_bp.route('/api/<author_name>/enhance', methods=['POST'])
def api_enhance_author_metadata(author_name):
    """API endpoint to enhance author metadata using external services."""
    try:
        db_service = container.get_database_service()
        books = db_service.authors.get_books_by_author(author_name)
        
        if not books:
            return jsonify({'error': 'Author not found'}), 404
        
        logger.info(f"Enhancing metadata for author: {author_name}")
        
        enhancement_results = {
            'author': author_name,
            'original_book_count': len(books),
            'enhancements': {},
            'errors': []
        }
        
        author_data = format_author_for_template(author_name, books)
        
        try:
            enhanced_data = enhance_author_with_external_data(author_name, author_data)
            enhancement_results['enhancements']['external_discovery'] = enhanced_data.get('external_discovery', {})
        except Exception as e:
            logger.warning("External discovery failed for %s", author_name, exc_info=True)
            enhancement_results['errors'].append(f"External discovery failed: {str(e)}")
        
        update_existing = (request.get_json(silent=True) or {}).get('update_existing_books', False)
        if update_existing:
            try:
                metadata_service = container.get_metadata_update_service()
                book_ids = [book.get('ID') for book in books if book.get('ID')]
                
                if book_ids:
                    update_results = metadata_service.update_multiple_books(book_ids[:5])
                    enhancement_results['enhancements']['metadata_updates'] = {
                        'attempted': min(5, len(book_ids)),
                        'successful': update_results.get('successful', 0),
                        'failed': update_results.get('failed', 0)
                    }
            except Exception as e:
                logger.warning("Metadata updates failed for %s", author_name, exc_info=True)
                enhancement_results['errors'].append(f"Metadata updates failed: {str(e)}")
        
        return jsonify({
            'success': True,
            'results': enhancement_results,
            'timestamp': datetime.now().isoformat()
        })
    
    except Exception as e:
        logger.error(f"Error enhancing author {author_name}: {e}", exc_info=True)
        return jsonify({'error': f'Enhancement failed: {str(e)}'}), 500

@authors_bp.route('/api/<author_name>/missing-books/add', methods=['POST'])
def api_add_missing_books(author_name):
    """API endpoint to add discovered missing books to the library."""
    try:
        db_service = container.get_database_service()
        
        data = request.get_json(silent=True) or {}
        book_asins = data.get('asins', [])

        if not isinstance(book_asins, list) or not all(isinstance(a, str) and a.strip() for a in book_asins):
            return jsonify({'error': 'asins must be a list of ASIN strings'}), 400
        if not book_asins:
            return jsonify({'error': 'No book ASINs provided'}), 400
        
        logger.info(f"Adding {len(book_asins)} missing books for author: {author_name}")
        
        hybrid_service = container.get_hybrid_audiobook_service()
        results = {
            'author': author_name,
            'requested_asins': book_asins,
            'successful': 0,
            'failed': 0,
            'skipped': 0,
            'details': []
        }
        
        for asin in book_asins:
            try:
                if db_service.books.check_book_exists(asin):
                    results['skipped'] += 1
                    results['details'].append({
                        'asin': asin,
                        'status': 'skipped',
                        'reason': 'Already in library'
                    })
                    continue
                
                book_results = hybrid_service.search_books(asin, num_results=1)
                if not book_results:
                    results['failed'] += 1
                    results['details'].append({
                        'asin': asin,
                        'status': 'failed',
                        'reason': 'Book not found on Audible'
                    })
                    continue
                
                book_data = book_results[0]
                
                book_author = book_data.get('Author', '')
                if author_name.lower() not in book_author.lower():
                    results['failed'] += 1
                    results['details'].append({
                        'asin': asin,
                        'status': 'failed',
                        'reason': f'Author mismatch: {book_author}'
                    })
                    continue
                
                if db_service.books.add_book(book_data, library_status="wanted"):
                    results['successful'] += 1
                    results['details'].append({
                        'asin': asin,
                        'title': book_data.get('Title', ''),
                        'status': 'added',
                        'reason': 'Successfully added to library'
                    })
                else:
                    results['failed'] += 1
                    results['details'].append({
                        'asin': asin,
                        'status': 'failed',
                        'reason': 'Database error'
                    })
                    
            except Exception as e:
                logger.warning("Could not add %s", asin, exc_info=True)
                results['failed'] += 1
                results['details'].append({
                    'asin': asin,
                    'status': 'failed',
                    'reason': str(e)
                })
        
        logger.info(f"Completed adding books for {author_name}: {results['successful']} successful, {results['failed']} failed, {results['skipped']} skipped")
        
        return jsonify({
            'success': True,
            'message': f"Added {results['successful']} books, skipped {results['skipped']}, failed {results['failed']}",
            'results': results
        })
    
    except Exception as e:
        logger.error(f"Error adding missing books for {author_name}: {e}", exc_info=True)
        return jsonify({'error': f'Failed to add books: {str(e)}'}), 500

# --- Analytics and reporting -------------------------------------------------

@authors_bp.route('/api/export')
def api_export_authors():
    """Export authors data in various formats."""
    try:
        db_service = container.get_database_service()
        
        format_type = request.args.get('format', 'json')
        include_books = request.args.get('include_books', 'false').lower() == 'true'
        include_stats = request.args.get('include_stats', 'true').lower() == 'true'
        
        all_authors = db_service.authors.get_all_authors()
        export_data = []
        
        for author in all_authors:
            books = db_service.authors.get_books_by_author(author)
            
            if include_stats:
                author_data = format_author_for_template(author, books)
            else:
                author_data = {
                    'name': author,
                    'book_count': len(books)
                }
            
            if include_books:
                author_data['books'] = books
            
            export_data.append(author_data)
        
        if format_type == 'csv':
            import io
            import csv
            
            output = io.StringIO()
            if export_data and not include_books:
                fieldnames = ['name', 'book_count']
                if include_stats:
                    fieldnames.extend(['series_count', 'total_hours', 'completion_rate'])
                
                writer = csv.DictWriter(output, fieldnames=fieldnames)
                writer.writeheader()
                
                for author_data in export_data:
                    row = {field: author_data.get(field, '') for field in fieldnames}
                    writer.writerow(row)
            
            return jsonify({
                'success': True,
                'export_data': output.getvalue(),
                'format': 'csv',
                'filename': f"authors_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            })
        
        else:
            export_info = {
                'export_timestamp': datetime.now().isoformat(),
                'total_authors': len(export_data),
                'include_books': include_books,
                'include_stats': include_stats,
                'authors': export_data
            }
            
            return jsonify({
                'success': True,
                'export_data': export_info,
                'format': 'json',
                'filename': f"authors_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            })
    
    except Exception as e:
        logger.error(f"Error exporting authors: {e}", exc_info=True)
        return jsonify({'error': f'Export failed: {str(e)}'}), 500

# --- Utility endpoints -------------------------------------------------------

@authors_bp.route('/api/<author_name>/books')
def api_get_author_books(author_name):
    """Enhanced API endpoint for getting author's books with organization."""
    try:
        db_service = container.get_database_service()
        books = db_service.authors.get_books_by_author(author_name)
        
        if not books:
            return jsonify({'error': 'Author not found'}), 404
        
        series_groups = defaultdict(list)
        standalone_books = []
        
        for book in books:
            series = book.get('Series', 'N/A')
            if series == 'N/A':
                standalone_books.append(book)
            else:
                series_groups[series].append(book)
        
        for series in series_groups:
            series_groups[series].sort(key=lambda x: float(x.get('Sequence', '0')) if str(x.get('Sequence', '0')).replace('.', '').isdigit() else 999)
        
        return jsonify({
            'success': True,
            'author': author_name,
            'series_groups': dict(series_groups),
            'standalone_books': standalone_books,
            'total_books': len(books),
            'series_count': len(series_groups)
        })
    
    except Exception as e:
        logger.error(f"Error fetching books for author {author_name}: {e}", exc_info=True)
        return jsonify({'error': 'Failed to fetch author books'}), 500

@authors_bp.route('/api/<author_name>/stats')
def api_get_author_stats(author_name):
    """API endpoint for author statistics and analytics."""
    try:
        db_service = container.get_database_service()
        books = db_service.authors.get_books_by_author(author_name)
        
        if not books:
            return jsonify({'error': 'Author not found'}), 404
        
        author_data = format_author_for_template(author_name, books, enhanced=True)
        
        enhance = request.args.get('enhance', 'false').lower() == 'true'
        if enhance:
            author_data = enhance_author_with_external_data(author_name, author_data)
        
        return jsonify({
            'success': True,
            'author': author_name,
            'stats': author_data,
            'enhanced': enhance
        })
    
    except Exception as e:
        logger.error(f"Error getting author stats for {author_name}: {e}", exc_info=True)
        return jsonify({'error': 'Failed to get author stats'}), 500

@authors_bp.route('/api/<author_name>/recommendations')
def api_get_author_recommendations(author_name):
    """API endpoint for getting author recommendations."""
    try:
        db_service = container.get_database_service()
        books = db_service.authors.get_books_by_author(author_name)
        
        if not books:
            return jsonify({'error': 'Author not found'}), 404
        
        author_data = format_author_for_template(author_name, books)
        recommendations = get_author_recommendations(author_name, author_data)
        
        return jsonify({
            'success': True,
            'author': author_name,
            'recommendations': recommendations,
            'count': len(recommendations)
        })
    
    except Exception as e:
        logger.error(f"Error getting recommendations for {author_name}: {e}", exc_info=True)
        return jsonify({'error': 'Failed to get recommendations'}), 500

@authors_bp.route('/api/<author_name>/discover')
def api_discover_missing_books(author_name):
    """API endpoint for discovering missing books by an author."""
    try:
        db_service = container.get_database_service()
        hybrid_service = container.get_hybrid_audiobook_service()
        
        library_books = db_service.authors.get_books_by_author(author_name)
        if not library_books:
            return jsonify({'error': 'Author not found in library'}), 404
        
        library_asins = set(book.get('ASIN', '') for book in library_books if book.get('ASIN'))
        
        audible_results = hybrid_service.get_author_books_from_audible(author_name)
        
        missing_books = []
        for book in audible_results:
            if book.get('ASIN') and book.get('ASIN') not in library_asins:
                missing_books.append({
                    'title': book.get('Title', ''),
                    'asin': book.get('ASIN', ''),
                    'series': book.get('Series', 'N/A'),
                    'sequence': book.get('Sequence', ''),
                    'release_date': book.get('Release Date', ''),
                    'runtime': book.get('Runtime', ''),
                    'rating': book.get('Overall Rating', ''),
                    'summary': book.get('Summary', ''),
                    'cover_image': book.get('Cover Image', ''),
                    'narrator': book.get('Narrator', ''),
                    'publisher': book.get('Publisher', '')
                })
        
        return jsonify({
            'success': True,
            'author': author_name,
            'library_books': len(library_books),
            'audible_total': len(audible_results),
            'missing_books': missing_books,
            'missing_count': len(missing_books),
            'coverage_percentage': round((len(library_asins) / len(audible_results)) * 100, 1) if audible_results else 100
        })
    
    except Exception as e:
        logger.error(f"Error discovering books for {author_name}: {e}", exc_info=True)
        return jsonify({'error': f'Discovery failed: {str(e)}'}), 500

# --- Search and discovery ----------------------------------------------------

@authors_bp.route('/api/search')
def api_search_authors():
    """Enhanced API endpoint for searching authors with fuzzy matching."""
    try:
        query = request.args.get('q', '').strip()
        if not query:
            return jsonify({'error': 'Search query required'}), 400
        
        include_stats = request.args.get('include_stats', 'false').lower() == 'true'
        limit = request.args.get('limit', 20, type=int)
        
        db_service = container.get_database_service()
        all_authors = db_service.authors.get_all_authors()
        
        matching_authors = []
        query_lower = query.lower()
        
        for author in all_authors:
            author_lower = author.lower()
            
            score = 0
            if author_lower == query_lower:
                score = 100
            elif author_lower.startswith(query_lower):
                score = 90
            elif query_lower in author_lower:
                score = 70
            elif any(word in author_lower for word in query_lower.split()):
                score = 50
            else:
                continue
            
            books = db_service.authors.get_books_by_author(author)
            if include_stats:
                author_info = format_author_for_template(author, books)
            else:
                author_info = {
                    'name': author,
                    'book_count': len(books)
                }
            
            author_info['relevance_score'] = score
            matching_authors.append(author_info)
        
        matching_authors.sort(key=lambda x: (x['relevance_score'], x.get('book_count', 0)), reverse=True)
        
        matching_authors = matching_authors[:limit]
        
        return jsonify({
            'success': True,
            'query': query,
            'authors': matching_authors,
            'count': len(matching_authors),
            'include_stats': include_stats
        })
    
    except Exception as e:
        logger.error(f"Error searching authors: {e}", exc_info=True)
        return jsonify({'error': 'Failed to search authors'}), 500

@authors_bp.route('/api/refresh/<author_name>', methods=['POST'])
def api_refresh_author(author_name):
    """Refresh author data using ASIN-based Audnexus lookup."""
    try:
        logger.info(f"Refreshing author data for: {author_name}")
        
        db_service = container.get_database_service()
        books = db_service.authors.get_books_by_author(author_name)
        
        author_asin = None
        for book in books:
            contributors = book.get('Contributors', [])
            if isinstance(contributors, list):
                for contributor in contributors:
                    if (isinstance(contributor, dict) and 
                        contributor.get('Role') == 'Author' and 
                        contributor.get('ASIN')):
                        author_asin = contributor.get('ASIN')
                        break
            if author_asin:
                break
        
        if not author_asin:
            hybrid_service = container.get_hybrid_audiobook_service()
            author_info = hybrid_service.search_author_page(author_name)
            if author_info and author_info.get('audible_author_id'):
                author_asin = author_info.get('audible_author_id')
        
        if not author_asin:
            return jsonify({
                'success': False,
                'error': f'Could not find author ASIN for {author_name}. Try adding books first to populate contributor data.',
                'suggestion': 'Add books by this author to get their ASIN from contributor data'
            }), 404
        
        result = update_author_from_asin(author_name, author_asin)
        
        if not result['success']:
            return jsonify({
                'success': False,
                'error': result['error'],
                'asin': author_asin
            }), 500
        
        return jsonify({
            'success': True,
            'author': author_name,
            'author_asin': author_asin,
            'library_books_count': len(books),
            'has_image': result['has_image'],
            'has_bio': result['has_bio'],
            'data_source': 'audnexus_asin',
            'message': f'Successfully updated author data using ASIN: {author_asin}'
        })
    
    except Exception as e:
        logger.error(f"Error refreshing author {author_name}: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': f'Failed to refresh author data: {str(e)}'
        }), 500
