"""
Module Name: authors.py
Author: AuralArchivist
Created: October 9, 2026
Last Modified: October 9, 2026
Description:
    Author metadata: looking authors up on Audnexus/Audible, keeping their
    details fresh in the database, and gathering their catalog for import.

Location:
    /services/metadata/authors.py
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set
from urllib.parse import quote

from core.language_matching import language_allowed
from core.logging import get_module_logger
from services.metadata.covers import cache_image
from services.container import container
from integrations.audible.availability import is_buyable

logger = get_module_logger("Service.Metadata.Authors")

_AUTHOR_METADATA_MAX_AGE_HOURS = 24


def _audible_author_url(name: str, author_asin: str) -> str:
    """Audible author page URL with the name slug and ASIN properly URL-encoded."""
    slug = quote((name or '').replace(' ', '-'), safe='-')
    return f"https://www.audible.com/author/{slug}/{quote(str(author_asin or ''), safe='')}"


def gather_import_candidates(
    author_name: str,
    preferred_languages: Optional[Set[str]],
    series_name: Optional[str] = None,
    standalone_only: bool = False,
    asin_filter: Optional[str] = None,
) -> Dict[str, Any]:
    """Collect catalog entries suitable for import, applying filters and tracking skips."""

    response: Dict[str, Any] = {
        'subset_raw': [],
        'subset_formatted': [],
        'total_candidates': 0,
        'language_skipped': 0,
        'missing_raw': 0,
        'already_in_library': 0,
        'error': None,
    }

    try:
        hybrid_service = container.get_hybrid_audiobook_service()
        raw_books, formatted_books = hybrid_service.fetch_author_catalog(author_name)
    except Exception as exc:
        response['error'] = f"Failed to retrieve catalog for {author_name}: {exc}"
        logger.error(response['error'], exc_info=True)
        return response

    if not formatted_books:
        response['error'] = f"No catalog data available for {author_name}."
        return response

    raw_lookup = {
        product.get('asin'): product
        for product in raw_books
        if product.get('asin')
    }

    db_service = container.get_database_service()
    existing_library_books = db_service.authors.get_books_by_author(author_name) if db_service else []
    library_asins = {
        book.get('ASIN') for book in existing_library_books if book.get('ASIN')
    }

    if series_name:
        series_name = series_name.strip()
        standalone_only = False

    if asin_filter:
        asin_filter = asin_filter.strip()

    for book in formatted_books:
        series_value = book.get('Series') or book.get('series')
        series_value_clean = (series_value or '').strip()
        asin_value = book.get('ASIN') or book.get('asin')

        if series_name and series_value_clean != series_name:
            continue

        if standalone_only and series_value_clean not in ('', 'N/A'):
            continue

        if asin_filter and asin_value != asin_filter:
            continue

        response['total_candidates'] += 1

        if asin_value and asin_value in library_asins:
            response['already_in_library'] += 1
            continue

        language_value = book.get('Language') or book.get('language')
        if not language_allowed(language_value, preferred_languages):
            response['language_skipped'] += 1
            continue

        if not asin_value:
            response['missing_raw'] += 1
            continue

        raw_product = raw_lookup.get(asin_value)
        if not raw_product:
            response['missing_raw'] += 1
            continue

        response['subset_formatted'].append(book)
        response['subset_raw'].append(raw_product)

    return response


def update_author_data_with_asin(author_name: str, author_asin: str = None) -> Dict[str, Any]:
    """Update author data in database using author ASIN with Audnexus lookup."""
    try:
        audnexus_client = container.get_audnexus_client()
        db_service = container.get_database_service()
        
        if not author_asin:
            logger.info(f"Looking up author ASIN for: {author_name}")
            authors = audnexus_client.search_authors(author_name, num_results=5)
            
            if not authors:
                return {
                    'success': False,
                    'error': f'No authors found for "{author_name}" on Audnexus',
                    'source': 'audnexus_search'
                }
            
            exact_match = None
            for author in authors:
                if author.get('name', '').lower() == author_name.lower():
                    exact_match = author
                    break
            
            if exact_match:
                author_asin = exact_match.get('asin')
                logger.info(f"Found exact match for {author_name}: {author_asin}")
            else:
                author_asin = authors[0].get('asin')
                logger.info(f"Using best match for {author_name}: {author_asin}")
        
        if not author_asin:
            return {
                'success': False,
                'error': f'Could not determine author ASIN for "{author_name}"',
                'source': 'asin_lookup'
            }
        
        logger.info(f"Fetching detailed author info for ASIN: {author_asin}")
        author_details = audnexus_client.get_author_details(author_asin)
        
        if not author_details:
            return {
                'success': False,
                'error': f'Could not fetch author details for ASIN "{author_asin}"',
                'source': 'audnexus_details'
            }
        
        author_image_url = author_details.get('image')
        
        if author_image_url:
            cached_image_url = cache_image(author_image_url)
            author_image_url = cached_image_url or author_image_url
            logger.debug(f"Cached author image for {author_name}: {author_image_url} -> {cached_image_url}")
        
        author_metadata = {
            'name': author_name,
            'audible_author_id': author_asin,
            'author_image_url': author_image_url,
            'author_bio': author_details.get('description'),
            'author_page_url': _audible_author_url(author_details.get('name', ''), author_asin),
            'total_books_count': len(author_details.get('titles', [])),
            'audible_books_count': len(author_details.get('titles', []))
        }
        
        success = db_service.authors.upsert_author_metadata(author_metadata)
        
        if success:
            logger.info(f"Successfully updated author data for {author_name} (ASIN: {author_asin})")
            return {
                'success': True,
                'author_name': author_name,
                'author_asin': author_asin,
                'author_data': author_metadata,
                'source': 'audnexus_asin'
            }
        else:
            return {
                'success': False,
                'error': 'Failed to save author metadata to database',
                'source': 'database_save'
            }
    
    except Exception as e:
        logger.error(f"Error updating author data for {author_name}: {e}", exc_info=True)
        return {
            'success': False,
            'error': f'Exception during author update: {str(e)}',
            'source': 'exception'
        }


def _parse_timestamp(value: Any) -> Optional[datetime]:
    """Safely parse SQLite timestamp strings into datetime objects."""

    if not value:
        return None

    for parse in (datetime.fromisoformat, lambda v: datetime.strptime(v, "%Y-%m-%d %H:%M:%S")):
        try:
            return parse(str(value))
        except ValueError:
            continue
    return None


def enhance_author_with_external_data(author_name: str, author_data: Dict) -> Dict[str, Any]:
    """Enhance author data with external sources using hybrid service."""
    try:
        hybrid_service = container.get_hybrid_audiobook_service()
        enhanced_data = author_data.copy()
        
        try:
            author_info = hybrid_service.search_author_page(author_name)
            
            if author_info:
                enhanced_data.update({
                    'author_image': author_info.get('author_image') or enhanced_data.get('author_image'),
                    'author_bio': author_info.get('author_bio') or enhanced_data.get('author_bio'),
                    'audible_author_id': author_info.get('audible_author_id') or enhanced_data.get('audible_author_id'),
                    'author_page_url': author_info.get('author_page_url') or enhanced_data.get('author_page_url'),
                    'data_source': author_info.get('source', 'hybrid')
                })
            
            audible_results = hybrid_service.get_author_books_from_audible(author_name, limit=30)
            
            db_service = container.get_database_service()
            library_books = db_service.authors.get_books_by_author(author_name)
            library_asins = set(book.get('ASIN', '') for book in library_books if book.get('ASIN'))
            
            missing_books = []
            for book in audible_results:
                if book.get('ASIN') and book.get('ASIN') not in library_asins:
                    missing_books.append({
                        'title': book.get('Title', ''),
                        'asin': book.get('ASIN', ''),
                        'series': book.get('Series', 'N/A'),
                        'release_date': book.get('Release Date', ''),
                        'runtime': book.get('Runtime', ''),
                        'rating': book.get('Overall Rating', ''),
                        'enhanced_by': book.get('enhanced_by', 'unknown')
                    })
            
            enhanced_data['external_discovery'] = {
                'missing_books_count': len(missing_books),
                'missing_books': missing_books[:10],
                'total_audible_books': len(audible_results),
                'library_coverage': round((len(library_asins) / len(audible_results)) * 100, 1) if audible_results else 100,
                'data_quality': 'hybrid_enhanced'
            }
            
        except Exception as e:
            logger.debug(f"Hybrid discovery failed for {author_name}: {e}")
            try:
                audible_service = container.get_audible_catalog_client()
                audible_results = audible_service.search_books(f"author:{author_name}", num_results=20)
                
                db_service = container.get_database_service()
                library_books = db_service.authors.get_books_by_author(author_name)
                library_asins = set(book.get('ASIN', '') for book in library_books if book.get('ASIN'))
                
                missing_books = []
                for book in audible_results:
                    if book.get('ASIN') and book.get('ASIN') not in library_asins:
                        missing_books.append({
                            'title': book.get('Title', ''),
                            'asin': book.get('ASIN', ''),
                            'series': book.get('Series', 'N/A'),
                            'release_date': book.get('Release Date', ''),
                            'runtime': book.get('Runtime', ''),
                            'rating': book.get('Overall Rating', '')
                        })
                
                enhanced_data['external_discovery'] = {
                    'missing_books_count': len(missing_books),
                    'missing_books': missing_books[:10],
                    'total_audible_books': len(audible_results),
                    'library_coverage': round((len(library_asins) / len(audible_results)) * 100, 1) if audible_results else 100,
                    'data_quality': 'audible_fallback'
                }
                
            except Exception as fallback_error:
                logger.error(f"Both hybrid and fallback discovery failed for {author_name}: {fallback_error}", exc_info=True)
                enhanced_data['external_discovery'] = {'error': str(e)}
        
        enhanced_data['enhancement_successful'] = True
        return enhanced_data
        
    except Exception as e:
        logger.error(f"Author enhancement failed for {author_name}: {e}", exc_info=True)
        author_data['enhancement_error'] = str(e)
        return author_data


def update_author_from_asin(author_name: str, author_asin: str) -> Dict[str, Any]:
    """
    Update author metadata using author ASIN from Audnexus.
    This is the preferred method for accurate author data updates.
    """
    try:
        from services.container import container
        
        logger.info(f"Updating author {author_name} using ASIN: {author_asin}")
        
        audnexus_client = container.get_audnexus_client()
        db_service = container.get_database_service()
        
        author_details = audnexus_client.get_author_details(author_asin)
        
        if not author_details:
            logger.warning(f"No author details found for ASIN {author_asin}")
            return {
                'success': False,
                'error': 'Author not found in Audnexus',
                'asin': author_asin
            }
        
        author_metadata = {
            'name': author_name,
            'audible_author_id': author_asin,
            'author_image_url': author_details.get('image'),
            'author_bio': author_details.get('description'),
            'author_page_url': _audible_author_url(author_details.get('name', ''), author_asin),
            'total_books_count': 0,
            'audible_books_count': len(author_details.get('titles', [])) if author_details.get('titles') else 0
        }
        
        success = db_service.authors.upsert_author_metadata(author_metadata)
        
        if success:
            logger.info(f"Successfully updated author {author_name} with ASIN {author_asin}")
            return {
                'success': True,
                'author_name': author_name,
                'asin': author_asin,
                'has_image': bool(author_metadata.get('author_image_url')),
                'has_bio': bool(author_metadata.get('author_bio')),
                'source': 'audnexus_asin'
            }
        else:
            return {
                'success': False,
                'error': 'Failed to save author metadata to database',
                'asin': author_asin
            }
    
    except Exception as e:
        logger.error(f"Error updating author {author_name} from ASIN {author_asin}: {e}", exc_info=True)
        return {
            'success': False,
            'error': str(e),
            'asin': author_asin
        }


def ensure_author_metadata_cached(author_name: str, *, refresh_if_stale: bool = True,
                                  max_age_hours: Optional[float] = None) -> Dict[str, Any]:
    """Ensure author metadata exists and stays fresh; cache image locally.

    max_age_hours overrides the default staleness window (used by the
    scheduled metadata refresh, whose interval is user-configurable).
    """

    db_service = container.get_database_service()
    metadata = db_service.authors.get_author_metadata(author_name) or {}
    max_age = _AUTHOR_METADATA_MAX_AGE_HOURS if max_age_hours is None else max_age_hours

    needs_refresh = not metadata
    if refresh_if_stale and metadata:
        last_fetched = _parse_timestamp(metadata.get('last_fetched_at')) or _parse_timestamp(metadata.get('updated_at'))
        if not last_fetched:
            needs_refresh = True
        else:
            age = datetime.utcnow() - last_fetched
            if age > timedelta(hours=max_age):
                needs_refresh = True

    if needs_refresh:
        if metadata.get('audible_author_id'):
            refresh_result = update_author_from_asin(author_name, metadata['audible_author_id'])
        else:
            refresh_result = update_author_data_with_asin(author_name)

        if not refresh_result.get('success'):
            logger.debug(
                "Author metadata refresh skipped",
                extra={
                    "author": author_name,
                    "reason": refresh_result.get('error')
                }
            )
        else:
            metadata = db_service.authors.get_author_metadata(author_name) or {}

    image_url = metadata.get('author_image_url')
    if image_url:
        cached_url = cache_image(image_url)
        if cached_url and cached_url != image_url:
            metadata['author_image_url'] = cached_url
            db_service.authors.upsert_author_metadata(metadata)

    return metadata


def process_book_contributors_for_authors(book_data: Dict) -> List[Dict]:
    """
    Extract author ASINs from book contributors data and update author metadata.
    Call this function when a new book is added to automatically update author data.
    """
    results = []
    
    try:
        contributors = book_data.get('Contributors', [])
        if not isinstance(contributors, list):
            return results
        
        author_contributors = [
            c for c in contributors 
            if (isinstance(c, dict) and 
                c.get('Role') == 'Author' and 
                c.get('Name') and 
                c.get('ASIN'))
        ]
        
        for contributor in author_contributors:
            author_name = contributor.get('Name')
            author_asin = contributor.get('ASIN')
            
            if author_name and author_asin:
                logger.info(f"Processing author {author_name} with ASIN {author_asin} from book contributors")
                
                result = update_author_from_asin(author_name, author_asin)
                results.append({
                    'author_name': author_name,
                    'author_asin': author_asin,
                    'update_result': result
                })
        
        return results
        
    except Exception as e:
        logger.error(f"Error processing book contributors for authors: {e}", exc_info=True)
        return []


def persist_author_catalog(
    author_name: str,
    raw_books: List[Dict[str, Any]],
    formatted_books: List[Dict[str, Any]]
) -> Dict[str, int]:
    """Persist author catalog results into the database for books and series tables."""
    db_service = container.get_database_service()
    if not db_service:
        logger.debug("DatabaseService not available; skipping persistence")
        return {'books_successful': 0, 'books_failed': 0, 'series_successful': 0, 'series_failed': 0}

    books_for_db = _prepare_books_for_database(raw_books, formatted_books)
    books_successful = 0
    books_failed = 0
    if books_for_db:
        try:
            books_successful, books_failed = db_service.books.bulk_insert_or_update_books(books_for_db)
            logger.info("Persisted author catalog entries", extra={"author": author_name, "success": books_successful, "failed": books_failed})
        except Exception as db_error:
            books_failed = len(books_for_db)
            logger.warning("Failed to persist author catalog", extra={"author": author_name, "error": str(db_error)}, exc_info=True)

    series_entries = _prepare_series_entries(raw_books, formatted_books)
    series_successful = 0
    series_failed = 0
    if series_entries:
        for entry in series_entries:
            try:
                db_service.series.upsert_series_book(entry)
                series_successful += 1
            except Exception:
                series_failed += 1
                logger.debug("Failed to upsert series mapping for %s", entry.get('book_asin'), exc_info=True)
        if series_failed:
            logger.warning("%s of %s series mappings for %s failed to save", series_failed, len(series_entries), author_name)

    return {
        'books_successful': books_successful,
        'books_failed': books_failed,
        'series_successful': series_successful,
        'series_failed': series_failed,
    }


def _prepare_books_for_database(
    raw_books: List[Dict[str, Any]],
    formatted_books: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Translate Audible catalog results into the normalized format expected by the books table"""

    formatted_lookup = {
        book.get('ASIN'): book
        for book in formatted_books
        if book.get('ASIN')
    }

    prepared: List[Dict[str, Any]] = []
    seen_asins: Set[str] = set()

    for product in raw_books:
        asin = product.get('asin')
        if not asin or asin in seen_asins:
            continue

        if not is_buyable(product):
            logger.debug(f"Skipping persistence for unbuyable catalog entry: {product.get('title')} ({asin})")
            continue

        seen_asins.add(asin)
        formatted = formatted_lookup.get(asin, {})

        author_value = formatted.get('Author') or _format_contributor_names(product.get('authors'))
        if not author_value:
            author_value = 'Unknown Author'

        narrator_value = formatted.get('Narrator') or _format_contributor_names(product.get('narrators'))
        if not narrator_value:
            narrator_value = 'Unknown Narrator'

        prepared.append({
            'asin': asin,
            'title': product.get('title') or formatted.get('Title') or 'Unknown Title',
            'author': author_value,
            'narrator': narrator_value,
            'runtime_length_min': product.get('runtime_length_min') or 0,
            'release_date': product.get('release_date') or formatted.get('Release Date'),
            'language': product.get('language') or formatted.get('Language'),
            'publisher': product.get('publisher_name') or formatted.get('Publisher'),
            'rating': _extract_rating_value(product),
            'num_ratings': _extract_num_ratings_from_product(product),
            'summary': formatted.get('Summary') or '',
            'cover_image_url': formatted.get('Cover Image') or '',
            'series_title': formatted.get('Series') or 'N/A',
            'series_sequence': formatted.get('Sequence') or 'N/A',
            'series_asin': formatted.get('series_asin'),
            'library_status': 'wanted',
            'source': 'audible_catalog',
        })

    return prepared


def _prepare_series_entries(
    raw_books: List[Dict[str, Any]],
    formatted_books: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Prepare series linkage data for the series_books table"""

    formatted_lookup = {
        book.get('ASIN'): book
        for book in formatted_books
        if book.get('ASIN') and book.get('series_asin')
    }

    raw_lookup = {
        book.get('asin'): book
        for book in raw_books
        if book.get('asin')
    }

    entries: List[Dict[str, Any]] = []

    for asin, formatted in formatted_lookup.items():
        series_asin = formatted.get('series_asin')
        if not series_asin:
            continue

        raw_product = raw_lookup.get(asin, {})
        sequence = formatted.get('Sequence', 'N/A')

        entries.append({
            'series_asin': series_asin,
            'book_asin': asin,
            'book_title': formatted.get('Title') or raw_product.get('title'),
            'sequence': sequence,
            'sort_order': _normalize_sequence(sequence) or sequence,
            'relationship_type': 'child',
            'author': formatted.get('Author'),
            'narrator': formatted.get('Narrator') or _format_contributor_names(raw_product.get('narrators')),
            'publisher': formatted.get('Publisher') or raw_product.get('publisher_name'),
            'release_date': formatted.get('Release Date') or raw_product.get('release_date'),
            'runtime': raw_product.get('runtime_length_min') or 0,
            'rating': formatted.get('Overall Rating'),
            'num_ratings': formatted.get('num_ratings'),
            'summary': formatted.get('Summary'),
            'cover_image': formatted.get('Cover Image'),
            'language': formatted.get('Language') or raw_product.get('language'),
        })

    return entries


def _extract_rating_value(product: Dict[str, Any]) -> Optional[float]:
    """Extract numeric rating value from Audible product data"""
    rating_info = product.get('rating') or {}

    if not isinstance(rating_info, dict):
        return None

    overall_dist = rating_info.get('overall_distribution') or {}
    if isinstance(overall_dist, dict):
        value = overall_dist.get('display_average_rating') or overall_dist.get('average_rating')
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            pass

    fallback_value = rating_info.get('overall_rating') or rating_info.get('rating')
    try:
        return float(fallback_value) if fallback_value is not None else None
    except (TypeError, ValueError):
        return None


def _extract_num_ratings_from_product(product: Dict[str, Any]) -> int:
    """Extract number of ratings from Audible product data"""
    rating_info = product.get('rating') or {}

    if not isinstance(rating_info, dict):
        return 0

    overall_dist = rating_info.get('overall_distribution') or {}
    if isinstance(overall_dist, dict):
        try:
            ratings = overall_dist.get('num_ratings', 0)
            return int(ratings) if ratings is not None else 0
        except (TypeError, ValueError):
            return 0

    return 0


def _format_contributor_names(contributors: Any) -> str:
    """Utility to convert contributor objects into a comma-separated name string"""
    if not contributors:
        return ''

    names = []
    for contributor in contributors:
        if isinstance(contributor, dict):
            name = contributor.get('name')
            if name:
                names.append(name)
        elif isinstance(contributor, str):
            names.append(contributor)

    return ", ".join(names)


def _normalize_sequence(sequence: Any) -> Optional[float]:
    """Attempt to normalize a sequence identifier into a sortable float"""
    if sequence is None:
        return None

    if isinstance(sequence, (int, float)):
        return float(sequence)

    try:
        cleaned = str(sequence).strip().lstrip('#').replace('Book', '').strip()
        return float(cleaned)
    except (ValueError, TypeError):
        return None
