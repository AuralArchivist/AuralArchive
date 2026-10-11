"""
Module Name: metadata_service.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Singleton service orchestrating metadata searches and database updates for
    books. Coordinates matching, search strategies, and update workflows.

Location:
    /services/metadata/metadata_service.py
"""

from typing import Tuple, Dict, List, Optional, Callable

from .metadata_lookup_strategies import MetadataSearchStrategies
from .matching import MetadataMatching
from .database_updates import MetadataDatabaseUpdates
from .error_handling import MetadataErrorHandler
from core.logging import get_module_logger


_LOGGER = get_module_logger("Service.Metadata.Service")
_error_handler = MetadataErrorHandler()


class CancellationContext:
    """Context object for tracking and checking operation cancellation"""
    
    def __init__(self, operation_id: str, cancellation_checker: Callable[[str], bool]):
        self.operation_id = operation_id
        self.cancellation_checker = cancellation_checker
        self.cancelled = False
    
    def check_cancellation(self) -> bool:
        """Check if the operation has been cancelled"""
        if not self.cancelled:
            self.cancelled = self.cancellation_checker(self.operation_id)
        return self.cancelled
    
    def raise_if_cancelled(self):
        """Raise exception if operation has been cancelled"""
        if self.check_cancellation():
            raise CancellationException(f"Operation {self.operation_id} was cancelled")


class CancellationException(Exception):
    """Exception raised when an operation is cancelled"""
    pass

class MetadataUpdateService:
    """Enhanced singleton service for updating book metadata with modular components"""
    
    def __init__(self, *, logger=None):
        self.logger = logger or _LOGGER

        self.audible_service = None
        self.database_service = None
        self.search_strategies = None
        self.database_updates = None

        self.matching = MetadataMatching()
        self.error_handler = _error_handler

        self.logger.success("Metadata update service started successfully", extra={"lazy_loading": True})
    
    def _ensure_dependencies(self):
        """Lazy load dependencies when first needed"""
        if self.audible_service is None or self.database_service is None:
            try:
                from services.container import container
                
                if self.audible_service is None:
                    self.audible_service = container.get_audible_catalog_client()
                    
                if self.database_service is None:
                    self.database_service = container.get_database_service()
                    
                if self.search_strategies is None:
                    self.search_strategies = MetadataSearchStrategies(self.audible_service)
                    
                if self.database_updates is None:
                    self.database_updates = MetadataDatabaseUpdates(self.database_service)
                
                self.logger.info(
                    "Service dependencies lazy loaded successfully",
                    extra={
                        "audible_service_loaded": bool(self.audible_service),
                        "database_service_loaded": bool(self.database_service),
                    },
                )
                
            except Exception as e:
                self.logger.error(
                    "Failed to lazy load dependencies",
                    extra={"error": str(e)},
                    exc_info=True,
                )
                raise

    def _notify_status(self, status_id, method: str, *args, **kwargs) -> None:
        """Best-effort update of the sidebar status event; never raises."""
        if status_id is None:
            return
        try:
            from services.container import container
            status_service = container.get_status_service()
            if status_service:
                getattr(status_service, method)(status_id, *args, **kwargs)
        except Exception:
            self.logger.debug("Status console update failed", exc_info=True)

    @_error_handler.with_retry(max_retries=2, retry_delay=1.0)
    def update_single_book(self, book_id: int, cancellation_context: Optional[CancellationContext] = None,
                           *, notify_status: bool = True) -> Tuple[bool, str]:
        """Update metadata for a single book from the database

        Returns:
            Tuple of (success: bool, message: str)

        Raises:
            CancellationException: If the operation is cancelled
        """
        _status_id = None
        try:
            from services.container import container
            _ss = container.get_status_service() if notify_status else None
            if _ss:
                _status_evt = _ss.start_event(
                    category='metadata',
                    title=f'Book #{book_id}',
                    message='Fetching metadata…',
                    source='Metadata',
                    entity_id=book_id,
                    state='running',
                )
                _status_id = _status_evt['id']
        except Exception:
            self.logger.debug("Could not open a status console event", exc_info=True)

        try:
            if cancellation_context:
                cancellation_context.raise_if_cancelled()
            
            self._ensure_dependencies()
            
            if cancellation_context:
                cancellation_context.raise_if_cancelled()
            
            book_data = self.database_service.books.get_book_by_id(book_id)
            
            if not book_data:
                self._notify_status(_status_id, 'fail_event', message=f'Book #{book_id} not found')
                return False, f"Book with ID {book_id} not found in database"

            self._notify_status(_status_id, 'update_event', title=book_data.get('Title') or f'Book #{book_id}')
            
            if cancellation_context:
                cancellation_context.raise_if_cancelled()
            
            self.logger.info(
                "Updating metadata for book",
                extra={
                    "book_id": book_id,
                    "title": book_data.get('Title', 'Unknown'),
                    "author": book_data.get('Author', 'Unknown'),
                },
            )
            
            current_title = book_data.get('Title', '')
            current_author = book_data.get('Author', '')
            current_asin = book_data.get('ASIN', '')
            
            if cancellation_context:
                cancellation_context.raise_if_cancelled()
            
            fresh_metadata = self.search_strategies.search_for_book_metadata(
                title=current_title,
                author=current_author,
                asin=current_asin,
                cancellation_context=cancellation_context
            )
            
            if cancellation_context:
                cancellation_context.raise_if_cancelled()
            
            if not fresh_metadata:
                self.logger.warning(
                    "No metadata found for book",
                    extra={"book_id": book_id, "title": current_title, "author": current_author},
                )
                return False, "No metadata found for this book"
            
            self.logger.info(
                "Found fresh metadata for book",
                extra={
                    "book_id": book_id,
                    "title": fresh_metadata.get('title', 'Unknown'),
                    "author": fresh_metadata.get('author', 'Unknown'),
                },
            )
            
            if cancellation_context:
                cancellation_context.raise_if_cancelled()
            
            update_success, update_message = self.database_updates.update_book_in_database(
                book_id=book_id,
                fresh_metadata=fresh_metadata
            )
            
            if update_success:
                self.logger.info("Successfully updated metadata for book", extra={"book_id": book_id})
                _title = book_data.get('Title') or f'Book #{book_id}'
                self._notify_status(_status_id, 'complete_event', message='Metadata updated')
                self._notify_status(_status_id, 'update_event', title=_title)
                return True, "Metadata updated successfully"
            else:
                self.logger.error(
                    "Failed to update book in database",
                    extra={"book_id": book_id, "error": update_message},
                )
                self._notify_status(_status_id, 'fail_event', message='Metadata update failed', error=update_message)
                return False, f"Database update failed: {update_message}"
                
        except CancellationException:
            self.logger.info("Metadata update was cancelled", extra={"book_id": book_id})
            self._notify_status(_status_id, 'cancel_event', message='Cancelled')
            raise
        except Exception as e:
            self.logger.error(
                "Error updating book",
                extra={"book_id": book_id, "error": str(e)},
                exc_info=True,
            )
            self._notify_status(_status_id, 'fail_event', message='Metadata update error', error=str(e))
            return False, str(e)
    
    def update_multiple_books(self, book_ids: List[int]) -> Dict[str, any]:
        """Update metadata for multiple books in batch"""
        try:
            self._ensure_dependencies()
            
            self.logger.info(
                "Starting batch metadata update",
                extra={"total": len(book_ids)},
            )
            
            results = {
                'total': len(book_ids),
                'successful': 0,
                'failed': 0,
                'errors': [],
                'updated_books': []
            }
            
            for book_id in book_ids:
                try:
                    success, message = self.update_single_book(book_id)
                    
                    if success:
                        results['successful'] += 1
                        results['updated_books'].append({
                            'book_id': book_id,
                            'message': message
                        })
                    else:
                        results['failed'] += 1
                        results['errors'].append({
                            'book_id': book_id,
                            'error': message
                        })
                        
                except Exception as e:
                    results['failed'] += 1
                    results['errors'].append({
                        'book_id': book_id,
                        'error': str(e)
                    })
            
            summary_message = "Batch update completed"
            self.logger.info(
                summary_message,
                extra={"successful": results['successful'], "failed": results['failed']},
            )
            results['summary'] = f"Batch update completed: {results['successful']} successful, {results['failed']} failed"
            
            return results
            
        except Exception as e:
            error_message = "Error in batch update"
            self.logger.error(
                error_message,
                extra={"error": str(e)},
                exc_info=True,
            )
            return {
                'total': len(book_ids),
                'successful': 0,
                'failed': len(book_ids),
                'errors': [{'error': error_message}],
                'summary': error_message
            }
