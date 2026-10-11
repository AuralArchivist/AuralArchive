"""
Module Name: database_updates.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Handles database update operations for the metadata service, including
    validation, sanitization, and persistence of refreshed metadata.

Location:
    /services/metadata/database_updates.py
"""

from typing import Dict, Tuple, List

from sqlalchemy import text

from .error_handling import error_handler
from core.logging import get_module_logger


_LOGGER = get_module_logger("Service.Metadata.DatabaseUpdates")

class MetadataDatabaseUpdates:
    """Handles database update operations for metadata service"""
    
    def __init__(self, database_service, *, logger=None):
        self.database_service = database_service
        self.logger = logger or _LOGGER
    
    def update_book_in_database(self, book_id: int, fresh_metadata: Dict) -> Tuple[bool, str]:
        """Update book in database with fresh metadata"""
        try:
            error_handler.log_update_attempt(book_id, "Database update", "Updating with fresh metadata")
            
            is_valid, validation_message = error_handler.validate_book_data(fresh_metadata)
            if not is_valid:
                return False, f"Invalid metadata: {validation_message}"
            
            sanitized_metadata = error_handler.sanitize_metadata(fresh_metadata)
            
            engine = self.database_service.connection_manager.get_engine()

            update_query = """
                UPDATE books SET
                    title = :title,
                    author = :author,
                    series = :series,
                    sequence = :sequence,
                    narrator = :narrator,
                    runtime = :runtime,
                    release_date = :release_date,
                    language = :language,
                    publisher = :publisher,
                    overall_rating = :overall_rating,
                    rating = :rating,
                    asin = :asin,
                    summary = :summary,
                    cover_image = :cover_image,
                    num_ratings = :num_ratings,
                    series_asin = :series_asin
                WHERE id = :book_id
            """


            def get_metadata_value(normalized_key, capitalized_key, default):
                return sanitized_metadata.get(normalized_key) or sanitized_metadata.get(capitalized_key) or default

            params = {
                "title": get_metadata_value('title', 'Title', 'Unknown Title'),
                "author": get_metadata_value('author', 'Author', 'Unknown Author'),
                "series": get_metadata_value('series_title', 'Series', 'N/A'),
                "sequence": get_metadata_value('series_sequence', 'Sequence', 'N/A'),
                "narrator": get_metadata_value('narrator', 'Narrator', 'Unknown Narrator'),
                "runtime": get_metadata_value('runtime_length_min', 'Runtime', 'Unknown Runtime'),
                "release_date": get_metadata_value('release_date', 'Release Date', 'Unknown'),
                "language": get_metadata_value('language', 'Language', 'Unknown'),
                "publisher": get_metadata_value('publisher', 'Publisher', 'Unknown Publisher'),
                "overall_rating": get_metadata_value('rating', 'Overall Rating', 'N/A'),
                "rating": get_metadata_value('rating', 'Overall Rating', 'N/A'),
                "asin": sanitized_metadata.get('asin') or sanitized_metadata.get('ASIN', ''),
                "summary": get_metadata_value('summary', 'Summary', 'No summary available'),
                "cover_image": get_metadata_value('cover_image_url', 'Cover Image', ''),
                "num_ratings": sanitized_metadata.get('num_ratings', 0),
                "series_asin": sanitized_metadata.get('series_asin'),
                "book_id": book_id,
            }

            self.logger.debug(
                "Executing book update",
                extra={"book_id": book_id, "param_count": len(params)},
            )

            with engine.begin() as conn:
                result = conn.execute(text(update_query), params)
                rows_affected = result.rowcount

            if rows_affected > 0:
                ladders = fresh_metadata.get('Categories') or fresh_metadata.get('categories')
                if ladders:
                    try:
                        self.database_service.categories.set_book_categories(book_id, ladders)
                    except Exception as category_error:
                        self.logger.warning("Could not save book categories", extra={"book_id": book_id, "error": str(category_error)})

                try:
                    from services.metadata.authors import process_book_contributors_for_authors
                    author_results = process_book_contributors_for_authors(fresh_metadata)
                    if author_results:
                        self.logger.info(
                            "Processed contributor authors during metadata update",
                            extra={"book_id": book_id, "authors_processed": len(author_results)},
                        )
                except Exception as contributor_error:
                    self.logger.warning(
                        "Author contributor processing failed",
                        extra={"book_id": book_id, "error": str(contributor_error)},
                        exc_info=True,
                    )

                success_message = "Successfully updated book in database"
                self.logger.info(success_message, extra={"book_id": book_id, "rows_affected": rows_affected})
                error_handler.log_update_result(book_id, True, success_message)
                return True, success_message
            else:
                error_message = "No rows updated for book"
                self.logger.warning(error_message, extra={"book_id": book_id})
                error_handler.log_update_result(book_id, False, error_message)
                return False, error_message

        except Exception as e:
            error_message = "Database update failed"
            self.logger.error(
                error_message,
                extra={"book_id": book_id, "error": str(e)},
                exc_info=True,
            )
            error_handler.log_update_result(book_id, False, error_message)
            return False, error_message
    
    def validate_database_connection(self) -> Tuple[bool, str]:
        """Validate that database connection is working"""
        try:
            if not self.database_service:
                return False, "No database service available"
            
            engine = self.database_service.connection_manager.get_engine()
            with engine.connect() as conn:
                result = conn.execute(text("SELECT 1")).fetchone()

            if result:
                return True, "Database connection successful"
            else:
                return False, "Database connection test failed"

        except Exception as e:
            return False, f"Database connection error: {str(e)}"

    def get_update_statistics(self) -> Dict[str, int]:
        """Get statistics about recent book updates from the database."""
        try:
            engine = self.database_service.connection_manager.get_engine()
            with engine.connect() as conn:
                total_updates_today = conn.execute(text(
                    "SELECT COUNT(*) AS cnt FROM books WHERE DATE(updated_at) = DATE('now')"
                )).mappings().fetchone()["cnt"]

                books_needing_updates = conn.execute(text(
                    """
                    SELECT COUNT(*) AS cnt FROM books
                    WHERE summary IN ('No summary available', '', 'N/A')
                       OR summary IS NULL
                       OR cover_image IN ('', 'N/A')
                       OR cover_image IS NULL
                       OR author IN ('Unknown Author', '')
                       OR author IS NULL
                    """
                )).mappings().fetchone()["cnt"]

            return {
                "total_updates_today": total_updates_today,
                "successful_updates": 0,
                "failed_updates": 0,
                "books_needing_updates": books_needing_updates,
            }

        except Exception as e:
            self.logger.error(
                "Error getting update statistics",
                extra={"error": str(e)},
                exc_info=True,
            )
            return {"error": str(e)}
    
    def update_multiple_books(self, book_updates: List[Tuple[int, Dict]]) -> Dict[str, int]:
        """Update multiple books in a batch operation"""
        try:
            results = {
                'successful': 0,
                'failed': 0,
                'total': len(book_updates)
            }
            
            self.logger.info(
                "Starting batch metadata update",
                extra={"total": results['total']},
            )
            
            for book_id, metadata in book_updates:
                try:
                    success, message = self.update_book_in_database(book_id, metadata)
                    if success:
                        results['successful'] += 1
                    else:
                        results['failed'] += 1
                        self.logger.warning(
                            "Batch update failed",
                            extra={"book_id": book_id, "error": message},
                        )
                        
                except Exception as e:
                    results['failed'] += 1
                    self.logger.error(
                        "Error in batch update",
                        extra={"book_id": book_id, "error": str(e)},
                        exc_info=True,
                    )
            
            self.logger.info(
                "Batch update completed",
                extra={"successful": results['successful'], "failed": results['failed']},
            )
            return results
            
        except Exception as e:
            self.logger.error(
                "Error in batch update operation",
                extra={"error": str(e)},
                exc_info=True,
            )
            return {'successful': 0, 'failed': len(book_updates), 'total': len(book_updates), 'error': str(e)}
