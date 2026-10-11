"""
Module Name: library.py
Author: AuralArchivist
Created: July 22, 2025
Last Modified: October 9, 2026
Description:
    Library routes for book views, detail APIs, and metadata refresh.

Location:
    /routes/library.py
"""


from datetime import datetime, timezone

from flask import Blueprint, jsonify, render_template, request, session
from flask_login import login_required

from services.metadata.covers import get_cached_book_cover_url
from services.container import container
from core.database.repositories import hardcover_cache
from core.logging import get_module_logger
from core.text import runtime_to_minutes

library_bp = Blueprint('library', __name__)
logger = get_module_logger("Routes.Library")

def _event_time(value):
    """Stored timestamp as aware UTC: epoch seconds, SQLite UTC text, or local ISO."""
    if value is None or value == '':
        return None
    try:
        if isinstance(value, (int, float)) or str(value).isdigit():
            return datetime.fromtimestamp(int(value), tz=timezone.utc)
        text = str(value)
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            # CURRENT_TIMESTAMP columns are UTC; datetime.now() writes local ISO with a 'T'.
            parsed = parsed.replace(tzinfo=timezone.utc) if 'T' not in text else parsed.astimezone()
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None

def format_book_for_template(book):
    """Format book data for MediaVault template with cached cover images."""
    
    cached_cover = get_cached_book_cover_url(book)
    original_cover = book.get('Cover Image')
    
    if cached_cover:
        cover_image = cached_cover
    elif original_cover and not original_cover.startswith('/metadata/items/'):
        cover_image = original_cover
    else:
        cover_image = None
    
    library_status = (book.get('library_status') or 'wanted').lower()
    source = book.get('source', 'manual').lower()

    status_display_map = {
        'wanted': 'Wanted',
        'owned': 'Owned',
        'missing': 'Missing',
    }

    status_display = status_display_map.get(library_status, library_status.replace('_', ' ').title())

    source_raw = book.get('source', 'manual').lower()
    file_location = book.get('file_path') or None

    return {
        'id': book.get('ID'),
        'title': book.get('Title', 'Unknown Title'),
        'author': book.get('Author', 'Unknown Author'),
        'cover_image': cover_image,
        'status': status_display,
        'library_status': library_status,
        'monitored': bool(book.get('monitored', True)),
        'source': source,
        'genre': get_genre_from_summary(book.get('Summary', '')),
        'series': book.get('Series', 'N/A'),
        'sequence': book.get('Sequence', ''),
        'series_asin': book.get('series_asin', ''),
        'runtime': book.get('Runtime', '0 hrs 0 mins'),
        'rating': book.get('Overall Rating', 'N/A'),
        'num_ratings': book.get('num_ratings', 0),
        'release_date': book.get('Release Date', 'Unknown'),
        'date_added': str(book.get('Created At') or ''),
        'asin': book.get('ASIN', ''),
        'narrator': book.get('Narrator', 'Unknown'),
        'publisher': book.get('Publisher', 'Unknown'),
        'language': book.get('Language', 'English'),
        'summary': book.get('Summary', ''),
        'file_path': book.get('file_path'),
        'file_location': file_location,
        'source_label': source_raw,
        'library_id': book.get('library_id'),
    }


def _library_context():
    """Library names by id, and whether there's more than one library."""
    try:
        libraries = container.get_library_repository().all()
    except Exception:
        logger.warning("Could not load libraries", exc_info=True)
        libraries = []
    return {lib['id']: lib['name'] for lib in libraries}, [
        {'id': lib['id'], 'name': lib['name'], 'is_default': lib['is_default'], 'enabled': lib['enabled']}
        for lib in libraries
    ]

def get_genre_from_summary(summary):
    """Extract genre from book summary."""
    if not summary:
        return 'unknown'
    
    summary_lower = summary.lower()
    
    if any(word in summary_lower for word in ['science', 'physics', 'biology', 'chemistry']):
        return 'science'
    elif any(word in summary_lower for word in ['history', 'historical', 'ancient', 'medieval']):
        return 'history'
    elif any(word in summary_lower for word in ['biography', 'memoir', 'life of']):
        return 'biography'
    elif any(word in summary_lower for word in ['self-help', 'productivity', 'habits', 'success']):
        return 'self-help'
    elif any(word in summary_lower for word in ['novel', 'story', 'tale', 'adventure']):
        return 'fiction'
    else:
        return 'non-fiction'

def _abs_enabled() -> bool:
    try:
        from services.sync.audiobookshelf import connection
        return connection.is_enabled()
    except Exception:
        return False


def _abs_book_context():
    """Audiobookshelf progress and play links for the Library page, by book id."""
    try:
        from services.sync.audiobookshelf import progress
        return progress.library_page_context()
    except Exception:
        logger.warning("Could not load Audiobookshelf progress for the library page", exc_info=True)
        return {}


def _abs_book_detail(book_id: int):
    try:
        from services.sync.audiobookshelf import progress
        return progress.book_detail(book_id)
    except Exception:
        logger.warning("Could not load Audiobookshelf details", extra={"book_id": book_id}, exc_info=True)
        return None


@library_bp.route('/')
@login_required
def library_page():
    """Display the library page with MediaVault design."""
    try:
        db_service = container.get_database_service()
        all_books = db_service.books.get_all_books()
        try:
            all_authors = db_service.authors.get_all_authors()
        except Exception:
            logger.warning("Could not load authors for the library page", exc_info=True)
            all_authors = []
        
        try:
            audible_asins = db_service.audible_library.get_purchased_asins()
        except Exception as exc:
            logger.warning(f"Could not load Audible library titles for the Source filter: {exc}")
            audible_asins = set()

        library_names, libraries = _library_context()
        abs_books = _abs_book_context()
        books_data = []
        for book in all_books:
            formatted_book = format_book_for_template(book)
            formatted_book['in_audible'] = (formatted_book.get('asin') or '').strip().upper() in audible_asins
            formatted_book['library_name'] = library_names.get(formatted_book.get('library_id'))
            formatted_book.update(abs_books.get(formatted_book['id']) or {})
            books_data.append(formatted_book)
        
        total_books = len(books_data)
        owned_books = len([b for b in books_data if b['library_status'] == 'owned'])
        wanted_books = len([b for b in books_data if b['library_status'] == 'wanted'])
        downloading_count = 0

        total_hours = 0
        for b in books_data:
            total_hours += runtime_to_minutes(b.get('runtime')) / 60

        genres = sorted({b['genre'] for b in books_data if b.get('genre')})

        return render_template('library.html',
                               title='Library',
                               books=books_data,
                               libraries=libraries,
                               abs_enabled=bool(abs_books) or _abs_enabled(),
                               genres=genres,
                               total_books=total_books,
                               owned_books=owned_books,
                               wanted_books=wanted_books,
                               total_authors=len(all_authors),
                               total_hours=int(total_hours),
                               downloading_count=downloading_count)
    
    except Exception as e:
        logger.error("Error loading library page: %s", e, exc_info=True)
    return render_template('library.html',
                   title='Library',
                   books=[],
                   libraries=[],
                   abs_enabled=False,
                   genres=[],
                   total_books=0,
                   owned_books=0,
                   wanted_books=0,
                   total_authors=0,
                   total_hours=0,
                   downloading_count=0)
    

@library_bp.route('/book/<string:asin>')
def get_book_details_by_asin(asin):
    """Get detailed information for a specific book by ASIN."""
    try:
        book_id = int(asin)
        db_service = container.get_database_service()
        book = db_service.books.get_book_by_id(book_id)
    except ValueError:
        db_service = container.get_database_service()
        book = db_service.books.get_book_by_asin(asin)
    
    try:
        if book:
            formatted_book = format_book_for_template(book)
            return jsonify({'success': True, 'book': formatted_book})
        else:
            return jsonify({'error': 'Book not found'}), 404
    
    except Exception as e:
        logger.error("Error fetching book details by ASIN: %s", e, exc_info=True)
        return jsonify({'error': 'Failed to fetch book details'}), 500

@library_bp.route('/book/<int:book_id>')
def get_book_details(book_id):
    """Get detailed information for a specific book by ID."""
    try:
        db_service = container.get_database_service()
        book = db_service.books.get_book_by_id(book_id)
        if book:
            formatted_book = format_book_for_template(book)
            library_names, libraries = _library_context()
            formatted_book['library_name'] = library_names.get(formatted_book.get('library_id'))
            formatted_book['libraries'] = libraries
            formatted_book['abs'] = _abs_book_detail(book_id)
            formatted_book['hardcover_linked'] = False
            hc_config = container.get_config_service().get_hardcover_config() or {}
            formatted_book['hardcover_connected'] = bool(hc_config.get('token'))
            try:
                engine = db_service.connection_manager.get_engine()
                with engine.connect() as conn:
                    link = hardcover_cache.get_link_for_book(conn, book_id)
                if link:
                    formatted_book['hardcover_linked'] = True
                    formatted_book['hardcover_book_id'] = link['hardcover_book_id']
            except Exception as ex:
                logger.warning("Could not check Hardcover link for book %s: %s", book_id, ex)
            return jsonify({'success': True, 'book': formatted_book})
        else:
            return jsonify({'error': 'Book not found'}), 404
    
    except Exception as e:
        logger.error("Error fetching book details: %s", e, exc_info=True)
        return jsonify({'error': 'Failed to fetch book details'}), 500

@library_bp.route('/book/<int:book_id>/history')
def get_book_history(book_id):
    """Return a timeline of events for a specific book."""
    try:
        db_service = container.get_database_service()
        book = db_service.books.get_book_by_id(book_id)
        if not book:
            return jsonify({'success': False, 'error': 'Book not found'}), 404

        events = []

        created = book.get('Created At') or book.get('created_at')
        if created:
            events.append({'type': 'info', 'label': 'Added to library', 'ts': str(created)})

        updated = book.get('Updated At') or book.get('updated_at')
        if updated and updated != created:
            events.append({'type': 'info', 'label': 'Metadata updated', 'ts': str(updated)})

        import_date = book.get('import_date')
        if import_date:
            events.append({'type': 'success', 'label': 'Imported to library', 'ts': str(import_date)})

        try:
            asin = book.get('ASIN') or book.get('asin')
            if asin:
                rows = container.get_download_management_service().queue_manager.get_downloads_for_asin(asin)
                for row in rows:
                    status = (row['status'] or '').upper()
                    client, fmt, fsize = row['download_client'], row['file_format'], row['file_size']
                    queued_at, started_at, completed_at = row['queued_at'], row['started_at'], row['completed_at']
                    if queued_at:
                        events.append({'type': 'info', 'label': f'Download queued via {client or "unknown"}', 'ts': str(queued_at)})
                    if started_at:
                        events.append({'type': 'info', 'label': f'Download started · {client or ""}', 'ts': str(started_at)})
                    if completed_at and status in ('COMPLETE', 'DOWNLOAD_COMPLETE', 'IMPORTED', 'SEEDING', 'SEEDING_COMPLETE'):
                        size_label = f' · {round(fsize / 1073741824, 1)} GB' if fsize and fsize > 0 else ''
                        fmt_label = f' · {fmt}' if fmt else ''
                        events.append({'type': 'success', 'label': f'Download completed{fmt_label}{size_label}', 'ts': str(completed_at)})
                    if status.endswith('FAILED'):
                        ts = completed_at or started_at or queued_at
                        events.append({'type': 'error', 'label': 'Download failed', 'ts': str(ts) if ts else ''})
        except Exception as ex:
            logger.warning("Could not fetch download queue history for book %s: %s", book_id, ex)

        for event in events:
            event['_dt'] = _event_time(event['ts'])
        events.sort(key=lambda x: x['_dt'] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        for event in events:
            dt = event.pop('_dt')
            event['ts'] = dt.isoformat() if dt else ''

        return jsonify({'success': True, 'events': events})

    except Exception as e:
        logger.error("Error fetching book history: %s", e, exc_info=True)
        return jsonify({'success': False, 'error': 'Internal error (details in server log)'}), 500

@library_bp.route('/book/<int:book_id>/status', methods=['PUT'])
def update_book_status(book_id):
    """Update a book's status."""
    try:
        db_service = container.get_database_service()
        new_status = (request.get_json(silent=True) or {}).get('status')
        if not new_status:
            return jsonify({'error': 'Status is required'}), 400
        
        if db_service.books.update_book_status(book_id, new_status):
            return jsonify({'success': True, 'message': 'Status updated'})
        else:
            return jsonify({'error': 'Failed to update status'}), 500
    
    except Exception as e:
        logger.error("Error updating book status: %s", e, exc_info=True)
        return jsonify({'error': 'Failed to update status'}), 500

@library_bp.route('/book/<int:book_id>/monitored', methods=['PUT'])
def update_book_monitored(book_id):
    """Toggle whether a book is eligible for automatic search."""
    try:
        db_service = container.get_database_service()
        payload = request.get_json(silent=True) or {}
        if 'monitored' not in payload:
            return jsonify({'error': 'monitored is required'}), 400

        monitored = bool(payload.get('monitored'))
        if db_service.books.set_book_monitored(book_id, monitored):
            return jsonify({'success': True, 'monitored': monitored})
        else:
            return jsonify({'error': 'Failed to update monitored'}), 500

    except Exception as e:
        logger.error("Error updating book monitored flag: %s", e, exc_info=True)
        return jsonify({'error': 'Failed to update monitored'}), 500

@library_bp.route('/book/<int:book_id>', methods=['DELETE'])
def delete_book(book_id):
    """Delete a book from the library."""
    try:
        db_service = container.get_database_service()
        logger.info("Delete request received for book ID %s", book_id)
        if db_service.books.delete_book(book_id):
            logger.info("Book deleted (library route) ID=%s", book_id)
            return jsonify({'success': True, 'message': 'Book deleted'})
        else:
            logger.warning("Book delete failed (library route) ID=%s", book_id)
            return jsonify({'error': 'Failed to delete book'}), 500
    
    except Exception as e:
        logger.error("Error deleting book: %s", e, exc_info=True)
        return jsonify({'error': 'Failed to delete book'}), 500

@library_bp.route('/books/bulk-delete', methods=['POST'])
def bulk_delete_books():
    """Delete multiple books from the library."""
    try:
        db_service = container.get_database_service()
        payload = request.get_json(silent=True) or {}
        book_ids = payload.get('book_ids') or payload.get('ids') or []

        if not isinstance(book_ids, list) or not book_ids:
            return jsonify({'error': 'book_ids list is required'}), 400

        deleted = []
        failed = []

        logger.info("Bulk delete requested for %s book(s)", len(book_ids))

        for raw_id in book_ids:
            try:
                book_id = int(raw_id)
            except (TypeError, ValueError):
                failed.append({'id': raw_id, 'error': 'invalid id'})
                continue

            if db_service.books.delete_book(book_id):
                logger.info("Book deleted (bulk) ID=%s", book_id)
                deleted.append(book_id)
            else:
                logger.warning("Book delete failed (bulk) ID=%s", book_id)
                failed.append({'id': book_id, 'error': 'delete_failed'})

        return jsonify({
            'success': True,
            'deleted_ids': deleted,
            'failed': failed
        })

    except Exception as e:
        logger.error(f"Error in bulk delete: {e}", exc_info=True)
        return jsonify({'error': 'Failed to delete books'}), 500

# --- Metadata update routes - refactored for new service architecture --------

@library_bp.route('/book/<int:book_id>/update-metadata', methods=['POST'])
def update_single_book_metadata(book_id):
    """Update metadata for a single book using the new service architecture."""
    try:
        logger.info(f"Received metadata update request for book ID: {book_id}")
        
        metadata_service = container.get_metadata_update_service()
        
        success, message = metadata_service.update_single_book(book_id)
        
        if success:
            logger.info(f"Successfully updated metadata for book ID {book_id}: {message}")
            return jsonify({
                'success': True,
                'message': message
            })
        else:
            logger.warning(f"Failed to update metadata for book ID {book_id}: {message}")
            return jsonify({
                'success': False,
                'error': message
            }), 400
    
    except Exception as e:
        logger.error(f"Error updating metadata for book ID {book_id}: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': 'Failed to update metadata'
        }), 500

@library_bp.route('/update-metadata', methods=['POST'])
def update_library_metadata():
    """Update metadata for selected books using the new batch processing with cancellation support."""
    try:
        data = request.get_json(silent=True) or {}
        book_ids = data.get('book_ids', [])
        operation_id = data.get('operation_id')
        
        if not book_ids:
            return jsonify({
                'success': False,
                'error': 'No books selected for update'
            }), 400
        
        if operation_id:
            session[f'operation_{operation_id}'] = True
            logger.info(f"Starting batch metadata update for {len(book_ids)} books (Operation ID: {operation_id})")
        else:
            logger.info(f"Starting batch metadata update for {len(book_ids)} books")
        
        metadata_service = container.get_metadata_update_service()
        
        book_ids_int = [int(book_id) for book_id in book_ids]
        
        if operation_id and not session.get(f'operation_{operation_id}', False):
            logger.info(f"Operation {operation_id} was cancelled before starting")
            return jsonify({
                'success': False,
                'cancelled': True,
                'message': 'Operation was cancelled',
                'results': {'total': len(book_ids), 'processed': 0, 'successful': 0, 'failed': 0, 'errors': []}
            })
        
        results = metadata_service.update_multiple_books(book_ids_int)
        
        if operation_id:
            session.pop(f'operation_{operation_id}', None)
        
        logger.info(f"Batch metadata update completed: {results['summary']}")
        
        return jsonify({
            'success': results['successful'] > 0,
            'message': results['summary'],
            'results': {
                'total': results['total'],
                'successful': results['successful'],
                'failed': results['failed'],
                'details': results['errors'][:10]
            }
        })
    
    except Exception as e:
        logger.error(f"Error in batch metadata update: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': 'Failed to update metadata'
        }), 500


# --- Library statistics and analytics ----------------------------------------

@library_bp.route('/stats')
def get_library_stats():
    """Get library statistics using all services."""
    try:
        db_service = container.get_database_service()
        books = db_service.books.get_all_books()
        
        stats = {
            'total_books': len(books),
            'by_status': {},
            'by_genre': {},
            'by_language': {},
            'total_runtime_hours': 0,
            'authors_count': 0,
            'series_count': 0,
            'missing_metadata_count': 0
        }
        
        authors = set()
        series = set()
        
        for book in books:
            status = book.get('library_status', 'Unknown')
            stats['by_status'][status] = stats['by_status'].get(status, 0) + 1
            
            genre = get_genre_from_summary(book.get('Summary', ''))
            stats['by_genre'][genre] = stats['by_genre'].get(genre, 0) + 1
            
            language = book.get('Language', 'Unknown')
            stats['by_language'][language] = stats['by_language'].get(language, 0) + 1
            
            if book.get('Author'):
                authors.add(book['Author'])
            
            if book.get('Series') and book.get('Series') != 'N/A':
                series.add(book['Series'])
            
            if not book.get('Summary') or not book.get('Cover Image') or not book.get('ASIN'):
                stats['missing_metadata_count'] += 1
            
            stats['total_runtime_hours'] += runtime_to_minutes(book.get('Runtime')) / 60
        
        stats['authors_count'] = len(authors)
        stats['series_count'] = len(series)
        
        return jsonify({'success': True, 'stats': stats})
    
    except Exception as e:
        logger.error(f"Error getting library stats: {e}", exc_info=True)
        return jsonify({'error': 'Failed to get stats'}), 500

@library_bp.route('/genres')
def get_genres():
    """Get all genres in the library for filtering."""
    try:
        db_service = container.get_database_service()
        books = db_service.books.get_all_books()
        
        genres = set()
        for book in books:
            genre = get_genre_from_summary(book.get('Summary', ''))
            if genre != 'unknown':
                genres.add(genre)
        
        return jsonify({
            'success': True,
            'genres': sorted(list(genres))
        })
    
    except Exception as e:
        logger.error(f"Error getting genres: {e}", exc_info=True)
        return jsonify({'error': 'Failed to get genres'}), 500

