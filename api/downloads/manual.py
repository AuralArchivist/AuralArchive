"""
Module Name: manual.py
Author: AuralArchivist
Created: June 22, 2025
Last Modified: October 9, 2026
Description:
    Manual Download and Search REST API. Provides manual and automatic search
    controls, queue operations, ownership checks, and metadata normalization to
    drive the download queue.

Location:
    /api/downloads/manual.py

Endpoints:

GET
    /api/search/automatic/config            Get automatic search config
    /api/search/automatic/queue             List automatic search queue
    /api/search/automatic/status            Automatic search status
    /api/search/books/wanted                Wanted books list
    /api/search/test                        Test endpoint

POST
    /api/search/automatic/config            Update automatic search config
    /api/search/automatic/force/<book_id>   Force search for a book
    /api/search/automatic/pause             Pause automatic search
    /api/search/automatic/resume            Resume automatic search
    /api/search/automatic/start             Start automatic search
    /api/search/automatic/stop              Stop automatic search
    /api/search/manual/book/<book_id>       Interactive search for a book
    /api/search/manual/download             Queue a manual download
    /api/search/manual/preview              Preview a download result
    /api/search/manual/search               Manual search

PUT
    /api/search/books/<book_id>/status      Update book status
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Dict, List

from flask import Blueprint, request, jsonify

from core.logging import get_module_logger
from core.text import normalize_search_terms
from services.container import container
from integrations.audible.ownership_validator import assess_audible_ownership, fetch_audible_library_entry
from services.acquisition.search.book_matcher import BookMatcher
from services.acquisition.search.query_variants import (
    clean_series_name as _clean_series_name,
    build_series_book_queries as _build_series_book_queries,
    derive_series_queries_from_title as _derive_series_queries_from_title,
)
from services.acquisition.search.result_identity import result_key

logger = get_module_logger("API.Manual.Download")

manual_search_api_bp = Blueprint('manual_search_api', __name__)


automatic_search_service = None
manual_search_service = None
database_service = None


# Download URLs this server handed out in interactive search results.
# /manual/download only queues a URL that appears here, so a client can't
# make the server fetch an arbitrary URL (SSRF) by editing the result JSON.
_ISSUED_URL_TTL_SECONDS = 6 * 3600
_ISSUED_URL_MAX_ENTRIES = 5000
_issued_download_urls: Dict[str, float] = {}
_issued_download_urls_lock = threading.Lock()


def _register_issued_urls(results: List[Dict[str, Any]]) -> None:
    now = time.time()
    with _issued_download_urls_lock:
        for key in [k for k, exp in _issued_download_urls.items() if exp < now]:
            del _issued_download_urls[key]
        for result in results or []:
            url = result.get('download_url') if isinstance(result, dict) else None
            if url:
                _issued_download_urls[url] = now + _ISSUED_URL_TTL_SECONDS
        overflow = len(_issued_download_urls) - _ISSUED_URL_MAX_ENTRIES
        if overflow > 0:
            for key in sorted(_issued_download_urls, key=_issued_download_urls.get)[:overflow]:
                del _issued_download_urls[key]


def _was_issued(url: str) -> bool:
    with _issued_download_urls_lock:
        expires = _issued_download_urls.get(url)
    return bool(expires and expires >= time.time())


def init_search_services(auto_search_svc, manual_search_svc, db_service):
    """Initialize search services for the API"""
    global automatic_search_service, manual_search_service, database_service
    automatic_search_service = auto_search_svc
    manual_search_service = manual_search_svc
    database_service = db_service


@manual_search_api_bp.route('/automatic/status', methods=['GET'])
def get_automatic_status():
    """Get automatic search service status"""
    try:
        if not automatic_search_service:
            return jsonify({'error': 'Automatic search service not available'}), 503
        
        status = automatic_search_service.get_status()
        return jsonify(status)
        
    except Exception as e:
        logger.error(f"Error getting automatic search status: {e}", exc_info=True)
        return jsonify({'error': 'Internal error (details in server log)'}), 500

@manual_search_api_bp.route('/automatic/start', methods=['POST'])
def start_automatic_search():
    """Start the automatic search service"""
    try:
        if not automatic_search_service:
            return jsonify({'error': 'Automatic search service not available'}), 503
        
        success = automatic_search_service.start()
        if success:
            return jsonify({'message': 'Automatic search service started', 'success': True})
        else:
            return jsonify({'message': 'Failed to start automatic search service', 'success': False}), 500
            
    except Exception as e:
        logger.error(f"Error starting automatic search: {e}", exc_info=True)
        return jsonify({'error': 'Internal error (details in server log)'}), 500

@manual_search_api_bp.route('/automatic/stop', methods=['POST'])
def stop_automatic_search():
    """Stop the automatic search service"""
    try:
        if not automatic_search_service:
            return jsonify({'error': 'Automatic search service not available'}), 503
        
        success = automatic_search_service.stop()
        if success:
            return jsonify({'message': 'Automatic search service stopped', 'success': True})
        else:
            return jsonify({'message': 'Failed to stop automatic search service', 'success': False}), 500
            
    except Exception as e:
        logger.error(f"Error stopping automatic search: {e}", exc_info=True)
        return jsonify({'error': 'Internal error (details in server log)'}), 500

@manual_search_api_bp.route('/automatic/pause', methods=['POST'])
def pause_automatic_search():
    """Pause the automatic search service"""
    try:
        if not automatic_search_service:
            return jsonify({'error': 'Automatic search service not available'}), 503
        
        automatic_search_service.pause()
        return jsonify({'message': 'Automatic search service paused', 'success': True})
        
    except Exception as e:
        logger.error(f"Error pausing automatic search: {e}", exc_info=True)
        return jsonify({'error': 'Internal error (details in server log)'}), 500

@manual_search_api_bp.route('/automatic/resume', methods=['POST'])
def resume_automatic_search():
    """Resume the automatic search service"""
    try:
        if not automatic_search_service:
            return jsonify({'error': 'Automatic search service not available'}), 503
        
        automatic_search_service.resume()
        return jsonify({'message': 'Automatic search service resumed', 'success': True})
        
    except Exception as e:
        logger.error(f"Error resuming automatic search: {e}", exc_info=True)
        return jsonify({'error': 'Internal error (details in server log)'}), 500

@manual_search_api_bp.route('/automatic/queue', methods=['GET'])
def get_search_queue():
    """Get current automatic search queue"""
    try:
        if not automatic_search_service:
            return jsonify({'error': 'Automatic search service not available'}), 503
        
        queue = automatic_search_service.get_search_queue()
        return jsonify({'queue': queue, 'count': len(queue)})
        
    except Exception as e:
        logger.error(f"Error getting search queue: {e}", exc_info=True)
        return jsonify({'error': 'Internal error (details in server log)'}), 500

@manual_search_api_bp.route('/automatic/force/<int:book_id>', methods=['POST'])
def force_search_book(book_id):
    """Force immediate search for a specific book"""
    try:
        if not automatic_search_service:
            return jsonify({'error': 'Automatic search service not available'}), 503
        
        result = automatic_search_service.force_search_book(book_id)
        if result.get('success'):
            return jsonify({'message': f'Forced search queued for book {book_id}', 'success': True, 'details': result})
        else:
            return jsonify({'message': result.get('error', f'Failed to queue search for book {book_id}'), 'success': False}), 400
            
    except Exception as e:
        logger.error(f"Error forcing search for book {book_id}: {e}", exc_info=True)
        return jsonify({'error': 'Internal error (details in server log)'}), 500

@manual_search_api_bp.route('/automatic/config', methods=['GET'])
def get_automatic_config():
    """Get automatic search configuration"""
    try:
        if not automatic_search_service:
            return jsonify({'error': 'Automatic search service not available'}), 503
        
        config_service = container.get_config_service()
        config = config_service.get_section('auto_search') or {}
        return jsonify(config)
        
    except Exception as e:
        logger.error(f"Error getting automatic search config: {e}", exc_info=True)
        return jsonify({'error': 'Internal error (details in server log)'}), 500

@manual_search_api_bp.route('/automatic/config', methods=['POST'])
def update_automatic_search_config():
    """Update automatic search configuration"""
    try:
        if not automatic_search_service:
            return jsonify({'error': 'Automatic search service not available'}), 503
        
        config_data = request.get_json()
        if not config_data:
            return jsonify({'error': 'No configuration data provided'}), 400
        
        success = automatic_search_service.update_configuration(config_data)
        if success:
            return jsonify({'message': 'Configuration updated', 'success': True})
        else:
            return jsonify({'message': 'Failed to update configuration', 'success': False}), 500
            
    except Exception as e:
        logger.error(f"Error updating automatic search config: {e}", exc_info=True)
        return jsonify({'error': 'Internal error (details in server log)'}), 500


@manual_search_api_bp.route('/manual/search', methods=['POST'])
def manual_search():
    """Perform manual search"""
    try:
        if not manual_search_service:
            return jsonify({'error': 'Manual search service not available'}), 503
        
        data = request.get_json(silent=True) or {}
        query = str(data.get('query') or '').strip()
        if not query:
            return jsonify({'error': 'Query parameter required'}), 400
        author = str(data.get('author') or '').strip()

        raw_results = manual_search_service.search_all_indexers(
            title=query,
            author=author,
            manual_search=True,
        )
        results = manual_search_service.process_manual_search_results(
            raw_results=raw_results,
            title=query,
            author=author,
        )
        _register_issued_urls(results)
        return jsonify({'success': True, 'results': results, 'count': len(results)})

    except Exception as e:
        logger.error(f"Error performing manual search: {e}", exc_info=True)
        return jsonify({'error': 'Internal error (details in server log)'}), 500

@manual_search_api_bp.route('/manual/book/<int:book_id>', methods=['POST'])
def interactive_search_book(book_id):
    """Perform an interactive search for a specific book and return ranked results for user selection."""
    try:
        if not manual_search_service:
            return jsonify({'error': 'Manual search service not available'}), 503
        
        if not database_service:
            return jsonify({'error': 'Database service not available'}), 503
        
        book = database_service.books.get_book_by_id(book_id)

        if not book:
            return jsonify({'error': f'Book with ID {book_id} not found'}), 404
        
        def _safe_str(value) -> str:
            if value is None:
                return ''
            if isinstance(value, str):
                return value.strip()
            return str(value).strip()

        title = _safe_str(book.get('Title', ''))
        author = _safe_str(book.get('Author', ''))
        
        if not title:
            return jsonify({'error': 'Book title is required for search'}), 400

        series = _safe_str(book.get('Series', ''))
        sequence_value = _safe_str(book.get('Sequence', ''))

        _, normalized_title, normalized_author = normalize_search_terms(
            title,
            title,
            author,
        )
        fallback_title = normalized_title or title
        fallback_author = normalized_author or author

        def _add_attempt(attempts_list, seen, attempt_title, attempt_author):
            title_candidate = _safe_str(attempt_title)
            author_candidate = _safe_str(attempt_author)
            if not title_candidate and not author_candidate:
                return
            key = (title_candidate.lower(), author_candidate.lower())
            if key in seen:
                return
            seen.add(key)
            attempts_list.append((title_candidate, author_candidate))

        asin = book.get('ASIN')
        audible_library_entry = fetch_audible_library_entry(database_service, asin)
        owned_via_audible, ownership_details = assess_audible_ownership(audible_library_entry)
        
        logger.info(f"Interactive search for book {book_id}: title='{title}', author='{author}'")

        search_attempts = []
        seen_attempts = set()

        series_titles = _build_series_book_queries(series, sequence_value)
        if not series_titles:
            series_titles = _derive_series_queries_from_title(fallback_title) or _derive_series_queries_from_title(title)

        for candidate in series_titles:
            _add_attempt(search_attempts, seen_attempts, candidate, author or fallback_author)

        clean_series = _clean_series_name(series)
        if clean_series:
            _add_attempt(search_attempts, seen_attempts, clean_series, author or fallback_author)

        _add_attempt(search_attempts, seen_attempts, fallback_title, fallback_author)
        _add_attempt(search_attempts, seen_attempts, title, author)

        all_results: List[Dict[str, Any]] = []
        seen_keys: set = set()
        results_lock = threading.Lock()

        def _run_attempt(attempt_title: str, attempt_author: str) -> List[Dict[str, Any]]:
            logger.info("Search attempt | title='%s' author='%s'", attempt_title, attempt_author)
            results = manual_search_service.search_all_indexers(
                title=attempt_title,
                author=attempt_author,
                manual_search=True,
            )
            logger.info(
                "Search attempt result | title='%s' author='%s' results=%d",
                attempt_title,
                attempt_author,
                len(results),
            )
            return results

        with ThreadPoolExecutor(max_workers=len(search_attempts)) as executor:
            future_to_attempt = {
                executor.submit(_run_attempt, t, a): (t, a)
                for t, a in search_attempts
            }
            for future in as_completed(future_to_attempt):
                attempt_title, attempt_author = future_to_attempt[future]
                try:
                    attempt_results = future.result()
                except Exception as exc:
                    logger.warning("Search attempt failed | title='%s' error='%s'", attempt_title, exc)
                    continue
                with results_lock:
                    for r in attempt_results:
                        key = result_key(r)
                        if key not in seen_keys:
                            seen_keys.add(key)
                            r['_search_query_used'] = attempt_title
                            all_results.append(r)

        raw_results = all_results
        search_title_for_scoring = fallback_title
        search_author_for_scoring = fallback_author

        query_components = BookMatcher.from_known_components(series, sequence_value)

        from services.acquisition.search.quality_assessor import QualityAssessor
        quality_assessor = QualityAssessor()
        scored_results = quality_assessor.rank_results_by_quality(
            raw_results,
            search_title=search_title_for_scoring,
            search_author=search_author_for_scoring,
            query_components=query_components,
        )
        
        processed_results = manual_search_service.process_manual_search_results(
            raw_results=scored_results,
            title=search_title_for_scoring,
            author=search_author_for_scoring
        )

        audible_result = None
        try:
            if asin and owned_via_audible:
                audible_manager = container.get_audible_account()
                library_client = getattr(audible_manager, 'library_client', None) if audible_manager else None

                if library_client:
                    auth_status = library_client.check_authentication_status()
                    if auth_status.get('authenticated'):
                        audible_format = (audible_library_entry or {}).get('format', 'AAXC')
                        file_size = (audible_library_entry or {}).get('file_size') or 0
                        audible_result = {
                            'id': f'audible-{asin}',
                            'title': f"{title} [Audible Library]",
                            'format': (audible_format or 'AAXC').upper(),
                            'size_bytes': file_size,
                            'quality_assessment': {
                                'total_score': 10.0,
                                'confidence': 100.0,
                                'match_confidence': 100.0,
                                'release_quality': 100.0,
                                'notes': 'Direct download from owned Audible library'
                            },
                            'confidence_score': 100.0,
                            'release_quality_score': 100.0,
                            'seeders': None,
                            'peers': None,
                            'indexer': 'Audible Library',
                            'source': 'Audible',
                            'download_type': 'audible',
                            'asin': asin,
                            'library_status': book.get('library_status'),
                            'download_url': None,
                            'info_url': None,
                            'tags': (audible_library_entry or {}).get('tags'),
                            'description': 'Download directly from your Audible account',
                            'ownership_reason': ownership_details.get('reason')
                        }
                        if audible_library_entry and audible_library_entry.get('summary'):
                            audible_result['description'] = audible_library_entry['summary']
        except Exception as exc:
            logger.warning(f"Failed to prepare Audible download option for ASIN {asin}: {exc}")
            audible_result = None

        if audible_result:
            processed_results = processed_results or []
            processed_results.insert(0, audible_result)
        
        _register_issued_urls(processed_results)

        response = {
            'success': True,
            'results': processed_results,
            'book_info': {
                'id': book_id,
                'title': title,
                'author': author,
                'series': series
            },
            'stats': manual_search_service.get_processing_stats(processed_results) if processed_results else {},
            'audible_download_available': bool(audible_result),
            'audible_ownership_details': ownership_details if owned_via_audible else None
        }
        
        return jsonify(response)
        
    except Exception as e:
        logger.error(f"Error performing interactive search for book {book_id}: {e}", exc_info=True)
        return jsonify({'error': 'Internal error (details in server log)'}), 500

@manual_search_api_bp.route('/manual/preview', methods=['POST'])
def preview_download():
    """Preview download for a search result"""
    try:
        if not manual_search_service:
            return jsonify({'error': 'Manual search service not available'}), 503
        
        result_data = request.get_json()
        if not result_data:
            return jsonify({'error': 'Result data required'}), 400
        
        preview = manual_search_service.preview_download(result_data)
        return jsonify(preview)
        
    except Exception as e:
        logger.error(f"Error previewing download: {e}", exc_info=True)
        return jsonify({'error': 'Internal error (details in server log)'}), 500

@manual_search_api_bp.route('/manual/download', methods=['POST'])
def initiate_manual_download():
    """Initiate download for a selected result with book metadata support"""
    try:
        data = request.get_json(silent=True) or {}
        result = data.get('result')
        if not isinstance(result, dict):
            return jsonify({'error': 'Result data required'}), 400

        book_id = data.get('book_id')
        
        database_service = container.get_database_service()
        
        if not book_id or not database_service:
            return jsonify({'error': 'Book ID and database service required'}), 400
        
        book = database_service.books.get_book_by_id(book_id)
        if not book:
            return jsonify({'error': 'Book not found'}), 404
        
        book_asin = book.get('ASIN')
        if not book_asin:
            return jsonify({'error': 'Book ASIN not found'}), 400

        audible_library_entry = fetch_audible_library_entry(database_service, book_asin)
        owned_via_audible, ownership_details = assess_audible_ownership(audible_library_entry)

        download_type = (result.get('download_type') or result.get('source') or '').lower()

        dm_service = container.get_download_management_service()

        if not dm_service:
            return jsonify({'error': 'Download management service not available'}), 503

        priority = data.get('priority')
        if priority is None:
            priority = getattr(dm_service, 'queue_priority_default', 5)
        try:
            priority = int(priority)
        except (TypeError, ValueError):
            priority = getattr(dm_service, 'queue_priority_default', 5)
        priority = max(1, min(10, priority))

        if 'audible' in download_type:
            audible_manager = container.get_audible_account()
            library_client = getattr(audible_manager, 'library_client', None) if audible_manager else None

            if not library_client:
                return jsonify({'error': 'Audible library service not available'}), 503

            auth_status = library_client.check_authentication_status()
            if not auth_status.get('authenticated'):
                return jsonify({
                    'success': False,
                    'error': 'Not authenticated with Audible',
                    'message': 'Authenticate via Settings > Audible before downloading owned titles.'
                }), 403

            if not owned_via_audible:
                reason = ownership_details.get('reason') if ownership_details else 'Ownership not verified.'
                logger.warning(
                    "Blocked Audible download for ASIN %s: %s",
                    book_asin,
                    reason
                )
                return jsonify({
                    'success': False,
                    'error': 'Audible ownership verification failed',
                    'message': reason or 'Audible library verification is required before downloading.'
                }), 403

            config_service = container.get_config_service()
            audible_config = config_service.get_section('audible') if config_service else {}
            asin_override = result.get('asin') or book_asin
            if not asin_override:
                return jsonify({'error': 'ASIN required for Audible download'}), 400

            format_pref = (result.get('format_preference') or result.get('format') or audible_config.get('download_format') or 'aaxc').lower()
            quality_pref = (result.get('quality') or audible_config.get('download_quality') or 'best').lower()

            valid_formats = {'aax', 'aaxc', 'aax-fallback'}
            if format_pref not in valid_formats:
                format_pref = audible_config.get('download_format', 'aaxc').lower()
            if format_pref not in valid_formats:
                format_pref = 'aaxc'

            valid_qualities = {'best', 'high', 'normal'}
            if quality_pref not in valid_qualities:
                quality_pref = audible_config.get('download_quality', 'best').lower()
            if quality_pref not in valid_qualities:
                quality_pref = 'best'

            logger.info(
                "Manual download queuing Audible title: asin=%s, title='%s', format=%s, quality=%s, priority=%s",
                asin_override,
                book.get('Title', 'Unknown'),
                format_pref,
                quality_pref,
                priority
            )

            queue_kwargs = {
                'title': book.get('Title', 'Unknown'),
                'author': book.get('Author', 'Unknown'),
                'download_type': 'audible',
                'audible_format': format_pref,
                'audible_quality': quality_pref,
                'indexer': result.get('indexer', 'Audible Library') or 'Audible Library',
                'file_size': result.get('size_bytes') or 0,
                'ownership_details': ownership_details
            }

            download_result = dm_service.add_to_queue(
                asin_override,
                search_result_id=None,
                priority=priority,
                **queue_kwargs
            )

            if download_result.get('success'):
                logger.info(
                    "Queued Audible download via manual download: asin=%s queue_id=%s",
                    asin_override,
                    download_result.get('download_id')
                )
                return jsonify({
                    'success': True,
                    'message': f"Audible download queued for {book.get('Title', 'Unknown')}",
                    'download_id': download_result.get('download_id'),
                    'audible_download': True,
                    'ownership_details': ownership_details
                }), 200

            error_message = download_result.get('message', 'Failed to queue Audible download')
            logger.error(
                "Failed to queue Audible download via manual download: asin=%s, error=%s",
                asin_override,
                error_message
            )
            if 'already in queue' in error_message.lower():
                return jsonify({
                    'success': False,
                    'error': 'This book is already in the download queue. Check the Downloads page to view its progress.',
                    'duplicate': True
                }), 409

            logger.error(f"Failed to queue Audible download for {book_asin}: {error_message}")
            return jsonify({'success': False, 'error': error_message}), 400
        
        download_url = result.get('download_url', '')
        if not download_url or not _was_issued(download_url):
            logger.warning("Rejected manual download for book %s: download_url not from a recent search", book_asin)
            return jsonify({
                'success': False,
                'error': 'This search result has expired. Run the search again and pick the result from the new list.'
            }), 400
        if (
            result.get('use_fl_wedge')
            and not result.get('freeleech')
            and download_url
            and any(str(key).startswith('mam_') for key in result.keys())
        ):
            separator = '&' if '?' in download_url else '?'
            download_url = f"{download_url}{separator}fl"
            logger.info("Spending a MAM freeleech wedge on download for book %s", book_asin)

        queue_kwargs = {
            'title': book.get('Title', 'Unknown'),
            'author': book.get('Author', 'Unknown'),
            'download_url': download_url,
            'indexer': result.get('indexer', 'Unknown'),
            'file_format': result.get('format', 'M4B'),
            'file_size': result.get('size_bytes', 0),
            'quality_score': result.get('quality_assessment', {}).get('total_score', 0) if isinstance(result.get('quality_assessment'), dict) else 0,
            'download_type': result.get('download_type') or result.get('result_type') or result.get('protocol') or 'torrent',
        }
        
        logger.info(f"Attempting to queue download for book {book_asin}: {book.get('Title', 'Unknown')}")
        logger.debug(f"Queue kwargs: {queue_kwargs}")
        
        download_result = dm_service.add_to_queue(
            book_asin,
            search_result_id=result.get('id'),
            priority=priority,
            **queue_kwargs
        )

        if download_result.get('success'):
            logger.info(f"Download queued via manual download: asin={book_asin}, id={download_result.get('download_id')}")
            return jsonify({
                'success': True,
                'message': f"Download queued for {book.get('Title', 'Unknown')}",
                'download_id': download_result.get('download_id')
            })
        else:
            logger.error(f"Failed to queue download for {book_asin}: {download_result.get('message')}")
            return jsonify({'success': False, 'error': download_result.get('message', 'Failed to queue download')}), 400
        
    except Exception as e:
        logger.error(f"Error initiating manual download: {e}", exc_info=True)
        return jsonify({'error': 'Internal error (details in server log)'}), 500


@manual_search_api_bp.route('/books/wanted', methods=['GET'])
def get_wanted_books():
    """Get all books with 'Wanted' status"""
    try:
        if not database_service:
            return jsonify({'error': 'Database service not available'}), 503
        
        wanted_books = database_service.books.get_books_by_status("Wanted")
        return jsonify({
            'books': wanted_books,
            'count': len(wanted_books)
        })
        
    except Exception as e:
        logger.error(f"Error getting wanted books: {e}", exc_info=True)
        return jsonify({'error': 'Internal error (details in server log)'}), 500

@manual_search_api_bp.route('/books/<int:book_id>/status', methods=['PUT'])
def update_book_status(book_id):
    try:
        if not database_service:
            return jsonify({'error': 'Database service not available'}), 503
        
        data = request.get_json()
        if not data or 'status' not in data:
            return jsonify({'error': 'Status parameter required'}), 400
        
        new_status = data['status']
        success = database_service.books.update_book_status(book_id, new_status)
        
        if success:
            return jsonify({
                'message': f'Book status updated to {new_status}',
                'success': True,
                'book_id': book_id,
                'new_status': new_status
            })
        else:
            return jsonify({
                'message': 'Failed to update book status',
                'success': False
            }), 404
            
    except Exception as e:
        logger.error(f"Error updating book status: {e}", exc_info=True)
        return jsonify({'error': 'Internal error (details in server log)'}), 500

@manual_search_api_bp.route('/test', methods=['GET'])
def test_search_api():
    """Test endpoint for search API"""
    try:
        status = {
            'search_api': 'active',
            'automatic_search_available': automatic_search_service is not None,
            'manual_search_available': manual_search_service is not None,
            'database_available': database_service is not None,
            'timestamp': str(datetime.now())
        }
        
        if automatic_search_service:
            auto_status = automatic_search_service.get_status()
            status['automatic_search_status'] = {
                'running': auto_status.get('running', False),
                'paused': auto_status.get('paused', False),
                'queue_size': auto_status.get('queue_size', 0)
            }
        
        return jsonify(status)
        
    except Exception as e:
        logger.error(f"Error in search API test: {e}", exc_info=True)
        return jsonify({'error': 'Internal error (details in server log)'}), 500
