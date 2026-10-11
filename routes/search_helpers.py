"""
Module Name: search_helpers.py
Author: AuralArchivist
Created: June 25, 2026
Last Modified: October 9, 2026
Description:
    Shared search helper functions used by both the search page render
    (routes/search.py) and the search JSON API handlers.

Location:
    /routes/search_helpers.py
"""
from typing import List, Dict, Any, Optional, Tuple
from core.logging import get_module_logger
from services.container import container

logger = get_module_logger("Routes.Search")


def format_search_result(
    book: Dict,
    in_library: bool = False,
    download_available: bool = False,
    library_status: str = 'not_in_library',
    library_book: Optional[Dict[str, Any]] = None,
) -> Dict:
    """Enhanced format search result for MediaVault template with download info."""
    library_record = library_book or {}
    record_id = (
        library_record.get('ID')
        or library_record.get('id')
        or book.get('ID')
        or book.get('id')
    )
    return {
        'id': record_id,
        'title': book.get('Title', book.get('title', 'Unknown Title')),
        'author': book.get('Author', book.get('author', 'Unknown Author')),
        'cover_image': book.get('Cover Image', book.get('cover_image')),
        'series': book.get('Series', book.get('series', 'N/A')),
        'sequence': book.get('Sequence', book.get('sequence', '')),
        'narrator': book.get('Narrator', book.get('narrator', 'Unknown')),
        'runtime': book.get('Runtime', book.get('runtime', '0 hrs 0 mins')),
        'rating': book.get('Overall Rating', book.get('rating', 'N/A')),
        'num_ratings': book.get('num_ratings', book.get('rating_count', 0)),
        'release_date': book.get('Release Date', book.get('release_date', 'Unknown')),
        'asin': book.get('ASIN', book.get('asin', '')),
        'publisher': book.get('Publisher', book.get('publisher', 'Unknown')),
        'language': book.get('Language', book.get('language', 'English')),
        'summary': book.get('Summary', book.get('summary', '')),
        'in_library': in_library,
        'library_status': library_status,
        'download_available': download_available,
        'book_library_status': (
            (library_record.get('library_status') if library_record else None)
            or book.get('library_status')
        ),
        'search_source': book.get('source', book.get('search_source', 'audible')),
        'enhanced_metadata': book.get('enhanced', False)
    }


def _is_audible_owned(library_status_value: Optional[str]) -> bool:
    """Return True when the book's library_status indicates it's owned."""
    return str(library_status_value or '').strip().lower() == 'owned'


def enhance_search_results(results: List[Dict]) -> List[Dict]:
    """Enhance search results with additional metadata and download availability."""
    try:
        enhanced_results = []

        for book in results:
            download_available = _is_audible_owned(book.get('book_library_status'))
            if not download_available and str(book.get('library_status', '')).lower() == 'in_library':
                download_available = True
            if (
                not download_available
                and str(book.get('search_source') or book.get('source') or '').lower() == 'audible'
                and str(book.get('library_status', '')).lower() in {'in_library', 'wanted'}
            ):
                download_available = True

            enhanced_book = book.copy()
            enhanced_book['download_available'] = download_available
            enhanced_book['enhanced'] = True
            enhanced_results.append(enhanced_book)

        return enhanced_results

    except Exception as e:
        logger.warning(f"Failed to enhance search results: {e}")
        return results


def get_search_analytics() -> Dict[str, Any]:
    """Get search analytics and performance metrics."""
    try:
        config_service = container.get_config_service()
        audible_service = container.get_audible_catalog_client()

        api_status = audible_service.search.get_api_status() if hasattr(audible_service.search, 'get_api_status') else {}
        config_data = config_service.list_config()
        audible_config = config_data.get('audible', {})

        return {
            'api_status': api_status,
            'max_results': int(audible_config.get('max_results', 25)),
            'default_region': audible_config.get('default_region', 'us'),
            'search_features': {
                'download_integration': False,
                'metadata_enhancement': True,
                'multi_source': True
            }
        }

    except Exception as e:
        logger.error(f"Failed to get search analytics: {e}", exc_info=True)
        return {}


def resolve_status_from_book_record(book: Optional[Dict]) -> Tuple[str, bool]:
    """Determine library status based on a full book record."""
    if not book:
        return 'not_in_library', False
    file_path = str(book.get('file_path') or '').strip()
    if file_path:
        return 'in_library', True
    return 'wanted', False


def determine_library_status(
    asin: Optional[str],
    db_service,
    cache: Optional[Dict[str, Tuple[str, bool]]] = None,
    record_cache: Optional[Dict[str, Optional[Dict[str, Any]]]] = None,
) -> Tuple[str, bool]:
    """Lookup library status for an ASIN with simple caching support."""
    if not asin:
        return 'not_in_library', False

    cache_store: Dict[str, Tuple[str, bool]] = cache if cache is not None else {}
    if asin in cache_store:
        return cache_store[asin]

    book_record = None
    if record_cache is not None and asin in record_cache:
        book_record = record_cache[asin]

    if book_record is None:
        try:
            book_record = db_service.books.get_book_by_asin(asin)
        except Exception:
            logger.warning("Failed to fetch book for ASIN %s", asin, exc_info=True)
            book_record = None
        if record_cache is not None:
            record_cache[asin] = book_record

    status = resolve_status_from_book_record(book_record)
    cache_store[asin] = status
    return status
