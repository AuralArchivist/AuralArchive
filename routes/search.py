"""
Module Name: search.py
Author: AuralArchivist
Created: July 24, 2025
Last Modified: October 9, 2026
Description:
    Catalog search routes and supporting APIs for Audible queries and download readiness.

Location:
    /routes/search.py
"""
from flask import Blueprint, render_template, request, jsonify, flash
from flask_login import login_required
from services.container import container
from services.library.router import choose_library
from typing import List, Dict, Any, Optional, Tuple
from core.logging import get_module_logger
from routes.search_helpers import (
    format_search_result,
    enhance_search_results,
    get_search_analytics,
    determine_library_status,
)

search_bp = Blueprint('search', __name__)
logger = get_module_logger("Routes.Search")


@search_bp.route('/')
@login_required
def search_page():
    """Enhanced search page with MediaVault design and advanced features."""
    query = request.args.get('q', '').strip()
    search_type = request.args.get('type', 'basic')
    results = []
    search_stats = {}
    
    if query:
        try:
            logger.info(f"Performing {search_type} search for: {query}")

            if search_type == 'advanced':
                results, search_stats = perform_advanced_search(query)
            else:
                results = _basic_audible_search(query)

                results = enhance_search_results(results)
                in_library_count = len([r for r in results if r.get('library_status') == 'in_library'])
                wanted_count = len([r for r in results if r.get('library_status') == 'wanted'])
                search_stats = {
                    'source': 'audible',
                    'total_results': len(results),
                    'in_library': in_library_count,
                    'wanted': wanted_count,
                    'downloadable': len([r for r in results if r.get('download_available')])
                }
            
            logger.info(f"Found {len(results)} results for query: {query}")
            
        except Exception as e:
            logger.error(f"Search error for query '{query}': {e}", exc_info=True)
            flash('Search failed. Check the server log for details.', 'error')
            results = []
            search_stats = {'error': 'Search failed'}
    
    analytics = get_search_analytics()
    
    libraries = [{'id': lib['id'], 'name': lib['name']}
                 for lib in container.get_library_repository().all() if lib['enabled']]

    return render_template('search.html', 
                         libraries=libraries,
                         title='Enhanced Search - AuralArchive',
                         query=query,
                         search_type=search_type,
                         results=results,
                         results_count=len(results),
                         search_stats=search_stats,
                         analytics=analytics)

def _basic_audible_search(query: str, num_results: Optional[int] = None) -> List[Dict[str, Any]]:
    """Audible catalog search with each result annotated with its library status.
    Shared by the search page and the /api/search endpoint."""
    audible_service = container.get_audible_catalog_client()
    db_service = container.get_database_service()
    raw_results = (audible_service.search_books(query, num_results=num_results)
                   if num_results else audible_service.search_books(query))

    library_status_cache: Dict[str, Tuple[str, bool]] = {}
    library_book_cache: Dict[str, Optional[Dict[str, Any]]] = {}
    results = []
    for book in raw_results:
        asin = book.get('ASIN')
        library_status, in_library = determine_library_status(
            asin,
            db_service,
            library_status_cache,
            library_book_cache,
        )
        results.append(format_search_result(
            book,
            in_library=in_library,
            library_status=library_status,
            library_book=library_book_cache.get(asin),
        ))
    return results


def perform_advanced_search(query: str) -> Tuple[List[Dict], Dict]:
    """Perform advanced search using multiple sources and enhanced metadata."""
    try:
        audible_service = container.get_audible_catalog_client()
        db_service = container.get_database_service()
        library_status_cache: Dict[str, Tuple[str, bool]] = {}
        library_book_cache: Dict[str, Optional[Dict[str, Any]]] = {}
        
        audible_results = audible_service.search_books(query)
        
        combined_results = []
        seen_asins = set()
        
        for book in audible_results:
            asin = book.get('ASIN')
            if asin and asin not in seen_asins:
                book['source'] = 'audible'
                book['enhanced'] = True
                combined_results.append(book)
                seen_asins.add(asin)
        
        enhanced_results = enhance_search_results(combined_results)

        formatted_results: List[Dict[str, Any]] = []
        for book in enhanced_results:
            asin = book.get('ASIN') or book.get('asin')
            library_status, in_library = determine_library_status(
                asin,
                db_service,
                library_status_cache,
                library_book_cache,
            )
            library_book = library_book_cache.get(asin)
            formatted_results.append(
                format_search_result(
                    book,
                    in_library=in_library,
                    download_available=book.get('download_available', False),
                    library_status=library_status,
                    library_book=library_book,
                )
            )
        
        stats = {
            'source': 'advanced',
            'total_results': len(formatted_results),
            'audible_results': len([r for r in formatted_results if r.get('search_source') == 'audible']),
            'downloadable': len([r for r in formatted_results if r.get('download_available')]),
            'in_library': len([r for r in formatted_results if r.get('library_status') == 'in_library']),
            'wanted': len([r for r in formatted_results if r.get('library_status') == 'wanted'])
        }
        
        return formatted_results, stats
        
    except Exception as e:
        logger.error(f"Advanced search failed: {e}", exc_info=True)
        return [], {'error': 'Internal error (details in server log)'}

# --- API endpoints - enhanced ------------------------------------------------

@search_bp.route('/api/search', methods=['POST'])
def api_search():
    """Enhanced API endpoint for search with multiple options."""
    try:
        data = request.get_json(silent=True) or {}
        query = str(data.get('query') or '').strip()
        search_type = data.get('type', 'basic')
        include_downloads = data.get('include_downloads', False)
        try:
            max_results = max(1, min(int(data.get('max_results', 25)), 100))
        except (TypeError, ValueError):
            max_results = 25
        
        if not query:
            return jsonify({'error': 'Query is required'}), 400
        
        logger.info(f"API search: '{query}' (type: {search_type})")
        
        if search_type == 'advanced':
            results, stats = perform_advanced_search(query)
        else:
            results = _basic_audible_search(query, num_results=max_results)
            
            if include_downloads:
                results = enhance_search_results(results)
            
            stats = {
                'source': 'audible',
                'total_results': len(results),
                'enhanced': include_downloads,
                'in_library': len([r for r in results if r.get('library_status') == 'in_library']),
                'wanted': len([r for r in results if r.get('library_status') == 'wanted'])
            }
        
        return jsonify({
            'success': True,
            'results': results,
            'count': len(results),
            'stats': stats,
            'query': query,
            'search_type': search_type
        })
    
    except Exception as e:
        logger.error(f"API search error: {e}", exc_info=True)
        return jsonify({'error': 'Search failed'}), 500

# --- Book management - enhanced ----------------------------------------------

@search_bp.route('/add-book', methods=['POST'])
def add_book_to_library():
    """Enhanced add book to library with validation and metadata enhancement."""
    logger.info("add_book_to_library endpoint called")
    try:
        logger.info("Getting database service...")
        db_service = container.get_database_service()
        logger.info("Database service obtained successfully")
        
        book_data = request.get_json(silent=True) or {}
        logger.debug(f"Received book data: {book_data}")
        asin = book_data.get('ASIN') or book_data.get('asin')
        logger.info(f"ASIN extracted: {asin}")
        
        if not asin:
            logger.warning("No ASIN provided in request")
            return jsonify({'error': 'ASIN is required'}), 400
        
        logger.info(f"Checking if book exists: {asin}")
        if db_service.books.check_book_exists(asin):
            logger.info(f"Book already exists: {asin}")
            return jsonify({'error': 'Book already exists in library'}), 409
        
        db_book_data = {
            'Title': book_data.get('title', book_data.get('Title', '')),
            'Author': book_data.get('author', book_data.get('Author', '')),
            'Series': book_data.get('series', book_data.get('Series', 'N/A')),
            'Sequence': book_data.get('sequence', book_data.get('Sequence', '')),
            'Narrator': book_data.get('narrator', book_data.get('Narrator', '')),
            'Runtime': book_data.get('runtime', book_data.get('Runtime', '')),
            'Overall Rating': book_data.get('rating', book_data.get('Overall Rating', '')),
            'Release Date': book_data.get('release_date', book_data.get('Release Date', '')),
            'ASIN': asin,
            'Publisher': book_data.get('publisher', book_data.get('Publisher', '')),
            'Language': book_data.get('language', book_data.get('Language', 'English')),
            'Summary': book_data.get('summary', book_data.get('Summary', '')),
            'Cover Image': book_data.get('cover_image', book_data.get('Cover Image', ''))
        }
        
        if not db_book_data['Title'] or not db_book_data['Author']:
            return jsonify({'error': 'Title and Author are required'}), 400
        
        if db_service.books.add_book(db_book_data, library_status="wanted"):
            logger.info(f"Added book to library: {db_book_data['Title']} by {db_book_data['Author']}")
            if book_data.get('library_id') not in (None, ''):
                try:
                    choose_library(asin, book_data['library_id'])
                except ValueError as exc:
                    logger.warning("Book added without its library choice: %s", exc)
            
            return jsonify({
                'success': True, 
                'message': 'Book added to library successfully',
                'book': {
                    'title': db_book_data['Title'],
                    'author': db_book_data['Author'],
                    'asin': asin
                }
            })
        else:
            return jsonify({'error': 'Failed to add book to database'}), 500
    
    except Exception as e:
        logger.error(f"Error adding book: {e}", exc_info=True)
        return jsonify({'error': 'Failed to add book'}), 500
