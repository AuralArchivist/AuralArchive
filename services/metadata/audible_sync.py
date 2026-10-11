"""
Module Name: audible_sync.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Synchronize Audible library metadata to the database with API-based fetch, enrichment, and batching.

Location:
    /services/metadata/audible_sync.py
"""

import threading
import time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Any
from core.logging import get_module_logger

from integrations.audible.metadata_sync.audible_api_helper import AudibleApiHelper
from integrations.audible.metadata_sync.metadata_processor import MetadataProcessor
from services.metadata.covers.image_cache_service import ImageCacheService


class SyncMode:
    """Sync mode constants following OpenAudible pattern"""
    QUICK = "quick"
    FULL = "full"


class AudibleMetadataSyncService:
    """Syncs Audible library metadata into the database. The container keeps
    one instance, so only one sync runs at a time."""
    
    def __init__(self, logger=None, socketio=None):
        from services.container import container

        self.logger = logger or get_module_logger("Service.Audible.MetadataSync")
        self.socketio = socketio
        
        self.db_service = container.get_database_service()
        self.image_cache = ImageCacheService()
        self.config_service = container.get_config_service()
        self.metadata_service = container.get_metadata_update_service()
        
        self.api_helper = AudibleApiHelper()
        self.metadata_processor = MetadataProcessor(logger=self.logger)
        
        self._sync_lock = threading.Lock()
        self._is_syncing = False
        self._sync_progress = {
            'total_books': 0,
            'processed_books': 0,
            'successful_books': 0,
            'failed_books': 0,
            'current_book': None,
            'start_time': None,
            'estimated_completion': None,
            'status': 'idle',
            'message': 'Ready to sync'
        }
        
        self.max_workers = 8
        self.batch_size = 20
        self.request_delay = 0.1
        self.cache_duration_hours = 6

        self.logger.info("Audible Metadata Sync Service initialized")

    def use_socketio(self, socketio) -> None:
        """Send progress through this Socket.IO server (kept as is when None)."""
        if socketio is not None:
            self.socketio = socketio
    
    def sync_library(self, mode: str = SyncMode.FULL, force_refresh: bool = False) -> Dict[str, Any]:
        """Synchronize Audible library with database using specified mode.

        Following OpenAudible pattern:
        - QUICK: Only sync new/changed books (like Quick_Refresh)
        - FULL: Complete library sync (like Rescan_Library)
        """
        with self._sync_lock:
            if self._is_syncing:
                return {
                    'success': False,
                    'error': 'Sync already in progress',
                    'progress': self._sync_progress.copy()
                }
            
            self._is_syncing = True
            self._reset_progress()
        
        try:
            start_time = datetime.now()
            self._sync_progress['start_time'] = start_time
            self._sync_progress['status'] = 'fetching_library'
            self._sync_progress['message'] = 'Fetching basic library from Audible API...'
            self._emit_progress_update()
            
            if mode == SyncMode.QUICK:
                self.logger.info("Quick Sync: Fetching only outdated books")
                basic_library = self._get_quick_sync_books()
                sync_type = "Quick Sync"
            else:
                self.logger.info("Full Sync: Fetching complete library from Audible API")
                basic_library = self.api_helper.get_library_list()
                sync_type = "Full Sync"
            
            if not basic_library:
                if mode == SyncMode.QUICK:
                    return {
                        'success': True,
                        'message': 'Quick Sync complete - no books need updating',
                        'stats': {
                            'total_books': 0,
                            'processed_books': 0,
                            'successful_books': 0,
                            'failed_books': 0,
                            'sync_mode': mode,
                            'duration_seconds': 0
                        }
                    }
                else:
                    raise Exception(f"Failed to fetch library for {sync_type}. Audible API may not be available or configured.")
            
            self._sync_progress['total_books'] = len(basic_library)
            self._sync_progress['status'] = 'processing_metadata'
            self._sync_progress['sync_mode'] = mode
            self._sync_progress['message'] = f'{sync_type}: Processing metadata for {len(basic_library)} books...'
            self._emit_progress_update()
            
            if not basic_library:
                return {
                    'success': True,
                    'message': 'No books found in library',
                    'stats': self._sync_progress.copy()
                }
            
            self.logger.info(
                "Processing books",
                extra={"book_count": len(basic_library), "max_workers": self.max_workers}
            )
            
            processed_books = []
            failed_books = []
            
            for i in range(0, len(basic_library), self.batch_size):
                batch = basic_library[i:i + self.batch_size]
                batch_results = self._process_book_batch(batch, force_refresh)
                
                for result in batch_results:
                    if result['success']:
                        processed_books.append(result['book_data'])
                    else:
                        failed_books.append(result)
                
                self._sync_progress['processed_books'] = len(processed_books) + len(failed_books)
                self._sync_progress['successful_books'] = len(processed_books)
                self._sync_progress['failed_books'] = len(failed_books)
                
                if self._sync_progress['processed_books'] > 0:
                    elapsed = (datetime.now() - start_time).total_seconds()
                    rate = self._sync_progress['processed_books'] / elapsed
                    remaining = self._sync_progress['total_books'] - self._sync_progress['processed_books']
                    eta_seconds = remaining / rate if rate > 0 else 0
                    self._sync_progress['estimated_completion'] = datetime.now() + timedelta(seconds=eta_seconds)
                
                self._sync_progress['message'] = f'Processed {self._sync_progress["processed_books"]}/{self._sync_progress["total_books"]} books...'
                self._emit_progress_update()
                
                time.sleep(0.1)
            
            if processed_books:
                self._sync_progress['status'] = 'saving_to_database'
                self._sync_progress['message'] = f'Saving {len(processed_books)} books to database...'
                self._emit_progress_update()
                
                self.logger.info(
                    "Saving books to database",
                    extra={"books": len(processed_books)}
                )
                library_rows = []
                for book in processed_books:
                    asin_value = book.get('asin')
                    if not asin_value:
                        continue
                    library_rows.append({
                        'asin': asin_value,
                        'title': book.get('title'),
                        'author': book.get('author'),
                        'authors': book.get('authors'),
                        'narrator': book.get('narrator'),
                        'narrators': book.get('narrators'),
                        'series_title': book.get('series_title'),
                        'series_sequence': book.get('series_sequence'),
                        'publisher': book.get('publisher'),
                        'release_date': book.get('release_date'),
                        'runtime_length_min': book.get('runtime_length_min'),
                        'summary': book.get('summary') or book.get('description'),
                        'genres': book.get('genres'),
                        'language': book.get('language'),
                        'rating': book.get('rating'),
                        'num_ratings': book.get('num_ratings'),
                        'purchase_date': book.get('purchase_date'),
                        'cover_image_url': book.get('cover_image_url'),
                        'local_cover_path': book.get('local_cover_path'),
                        'metadata_source': book.get('metadata_source'),
                        'sync_status': book.get('sync_status') or 'completed',
                    })
                if library_rows:
                    try:
                        self.db_service.audible_library.bulk_insert_or_update_books(library_rows)
                    except Exception:
                        self.logger.warning("Could not save Audible library rows", exc_info=True)
                successful_db, failed_db = self.db_service.books.bulk_insert_or_update_books(processed_books)
                
                self.logger.info(
                    "Database operations completed",
                    extra={"successful": successful_db, "failed": failed_db}
                )
            
            end_time = datetime.now()
            duration = (end_time - start_time).total_seconds()
            
            self._sync_progress['status'] = 'completed'
            self._sync_progress['message'] = 'Sync completed successfully'
            self._sync_progress['duration'] = duration
            self._emit_progress_update()
            
            if self.socketio:
                self.socketio.emit('audible_sync_complete', {
                    'success': True,
                    'message': f'Library sync completed: {len(processed_books)} books processed',
                    'stats': self._sync_progress.copy()
                })
            
            result = {
                'success': True,
                'message': 'Sync completed successfully',
                'stats': {
                    'total_books': len(basic_library),
                    'processed_books': len(processed_books),
                    'failed_books': len(failed_books),
                    'duration_seconds': duration,
                    'books_per_second': len(processed_books) / duration if duration > 0 else 0,
                    'completion_time': end_time.isoformat()
                },
                'failed_asins': [f.get('asin') for f in failed_books if f.get('asin')]
            }
            
            self.logger.info(
                "Sync completed",
                extra=result['stats']
            )
            return result
            
        except Exception as exc:
            self.logger.error(
                "Library sync failed",
                extra={"exc": exc}, exc_info=True
            )
            self._sync_progress['status'] = 'failed'
            self._sync_progress['error'] = str(exc)
            self._sync_progress['message'] = f'Sync failed: {exc}'
            self._emit_progress_update()
            
            return {
                'success': False,
                'error': str(exc),
                'progress': self._sync_progress.copy()
            }
        finally:
            self._is_syncing = False
    
    def _process_book_batch(self, books_batch: List[Dict], force_refresh: bool = False) -> List[Dict[str, Any]]:
        """Process a batch of books using parallel threading."""
        results = []
        
        with ThreadPoolExecutor(max_workers=min(self.max_workers, len(books_batch))) as executor:
            future_to_book = {
                executor.submit(self._process_single_book, book, force_refresh): book 
                for book in books_batch
            }
            
            for future in as_completed(future_to_book):
                book = future_to_book[future]
                try:
                    result = future.result()
                    results.append(result)
                except Exception as exc:
                    self.logger.error(
                        "Failed to process book",
                        extra={"asin": book.get('asin'), "exc": exc}, exc_info=True
                    )
                    results.append({
                        'success': False,
                        'asin': book.get('asin'),
                        'error': str(exc)
                    })
        
        return results
    
    def _process_single_book(self, basic_book: Dict[str, str], force_refresh: bool = False) -> Dict[str, Any]:
        """Process a single book's metadata using MetadataUpdateService."""
        try:
            asin = basic_book.get('asin')
            title = basic_book.get('title', '')
            author = basic_book.get('author', '')
            
            if not asin:
                return {
                    'success': False,
                    'error': 'No ASIN found'
                }
            
            self._sync_progress['current_book'] = title
            
            if not force_refresh:
                existing_book = self.db_service.audible_library.get_book_by_asin(asin)
                if existing_book and existing_book.get('last_updated'):
                    try:
                        last_update = datetime.fromisoformat(existing_book['last_updated'])
                        if datetime.now() - last_update < timedelta(hours=self.cache_duration_hours):
                            return {
                                'success': True,
                                'book_data': existing_book,
                                'cached': True
                            }
                    except (ValueError, TypeError):
                        pass
            
            self.logger.debug(
                "Getting metadata",
                extra={"asin": asin, "title": title}
            )
            
            self.metadata_service._ensure_dependencies()
            
            metadata = self.metadata_service.search_strategies.search_for_book_metadata(
                title=title,
                author=author,
                asin=asin
            )
            
            if not metadata:
                self.logger.warning(
                    "No metadata found; using basic info",
                    extra={"asin": asin}
                )
                normalized_book = self.metadata_processor.create_basic_book_entry(basic_book)
            else:
                normalized_book = self.metadata_processor.normalize_metadata_to_db_format(metadata, asin)

            purchase_date = basic_book.get('purchase_date')
            if purchase_date and 'purchase_date' not in normalized_book:
                normalized_book['purchase_date'] = self.metadata_processor._normalize_date(purchase_date)
            
            cover_url = normalized_book.get('cover_image_url')
            if cover_url:
                try:
                    cached_cover_path = self.image_cache.get_cached_image_url(cover_url)
                    if cached_cover_path:
                        normalized_book['local_cover_path'] = cached_cover_path
                        self.logger.debug(
                            "Cached cover image",
                            extra={"asin": asin, "cover_path": cached_cover_path}
                        )
                except Exception as exc:
                    self.logger.warning(
                        "Failed to cache cover",
                        extra={"asin": asin, "exc": exc}
                    )
            
            normalized_book['last_updated'] = datetime.now().isoformat()
            normalized_book['sync_status'] = 'completed'
            normalized_book['metadata_source'] = 'metadata_service' if metadata else 'basic_api'
            
            time.sleep(self.request_delay)
            
            return {
                'success': True,
                'book_data': normalized_book,
                'cached': False
            }
            
        except Exception as exc:
            self.logger.error(
                "Error processing book",
                extra={"asin": basic_book.get('asin'), "exc": exc}, exc_info=True
            )
            return {
                'success': False,
                'asin': basic_book.get('asin'),
                'error': str(exc)
            }
    
    def _get_quick_sync_books(self) -> List[Dict[str, Any]]:
        """Get books for quick sync - only new or outdated books.
        Following OpenAudible's Quick_Refresh pattern.
        """
        try:
            full_library = self.api_helper.get_library_list()
            if not full_library:
                self.logger.warning(
                    "API returned empty library; quick sync will refresh only outdated books",
                    extra={"cache_hours": self.cache_duration_hours}
                )
                
                outdated_asins = self.db_service.audible_library.get_outdated_books(self.cache_duration_hours)
                
                if not outdated_asins:
                    self.logger.info(
                        "No outdated books found in database. Quick sync complete.",
                        extra={"cache_hours": self.cache_duration_hours}
                    )
                    return []
                
                outdated_books = []
                for asin in outdated_asins[:10]:
                    book_data = self.db_service.audible_library.get_book_by_asin(asin)
                    if book_data:
                        api_format = {
                            'asin': book_data.get('asin'),
                            'title': book_data.get('title'),
                            'author': book_data.get('author')
                        }
                        outdated_books.append(api_format)
                
                self.logger.info(
                    "Quick sync will refresh outdated books",
                    extra={"count": len(outdated_books)}
                )
                return outdated_books
            
            outdated_asins = set(self.db_service.audible_library.get_outdated_books(self.cache_duration_hours))
            
            existing_asins = set(self.db_service.audible_library.get_all_asins())
            
            books_to_sync = []
            for book in full_library:
                asin = book.get('asin')
                if not asin:
                    continue
                
                if asin not in existing_asins or asin in outdated_asins:
                    books_to_sync.append(book)
            
            self.logger.info(
                "Quick sync book selection",
                extra={
                    "to_process": len(books_to_sync),
                    "new": len(full_library) - len(existing_asins),
                    "outdated": len(outdated_asins)
                }
            )
            
            return books_to_sync
            
        except Exception as exc:
            self.logger.error(
                "Failed to get quick sync books",
                extra={"exc": exc}, exc_info=True
            )
            return []
    
    def _reset_progress(self):
        """Reset progress tracking."""
        self._sync_progress = {
            'total_books': 0,
            'processed_books': 0,
            'successful_books': 0,
            'failed_books': 0,
            'current_book': None,
            'start_time': None,
            'estimated_completion': None,
            'status': 'starting',
            'message': 'Initializing sync...'
        }
    
    def _emit_progress_update(self):
        """Emit progress update via SocketIO."""
        if self.socketio:
            try:
                self.socketio.emit('audible_sync_progress', self._sync_progress)
            except Exception as exc:
                self.logger.warning(
                    "Failed to emit progress update",
                    extra={"exc": exc}
                )
    
    def get_sync_status(self) -> Dict[str, Any]:
        """Return sync state and a copy of the current progress details."""
        with self._sync_lock:
            return {
                'is_syncing': self._is_syncing,
                'progress': self._sync_progress.copy()
            }
