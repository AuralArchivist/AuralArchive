"""
Module Name: audible_library.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    CRUD and bulk operations for the audible_library table (ASIN primary key).

Location:
    /core/database/repositories/audible_library.py
"""

from sqlalchemy import text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from ..schema import audible_library as audible_library_table
from typing import List, Dict, Any, Optional, Set, Tuple
from datetime import datetime, timedelta
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from core.logging import get_module_logger


class AudibleLibraryOperations:
    """Database operations for Audible library books with ASIN primary key"""

    def __init__(self, connection_manager, *, logger=None):
        self.connection_manager = connection_manager
        self.logger = logger or get_module_logger("Service.Database.AudibleLibrary")
        self._lock = threading.Lock()
    
    def get_all_books(self, include_metadata: bool = True) -> List[Dict[str, Any]]:
        """Get all books from audible library."""
        try:
            engine = self.connection_manager.get_engine()
            
            if include_metadata:
                query = """
                    SELECT asin, title, author, authors, narrator, narrators,
                           series_title, series_sequence, publisher, publication_date,
                           release_date, duration_minutes, runtime_length_min, summary,
                           description, genres, tags, language, format, rating,
                           num_ratings, purchase_date, listened, progress, favorite,
                           cover_image_url, local_cover_path, file_path, file_size,
                           added_date, last_updated, metadata_source, sync_status
                    FROM audible_library 
                    ORDER BY last_updated DESC
                """
            else:
                query = """
                    SELECT asin, title, author, narrator, rating, num_ratings,
                           runtime_length_min, series_title, series_sequence
                    FROM audible_library 
                    ORDER BY last_updated DESC
                """
            
            with engine.connect() as conn:
                result = conn.execute(text(query))
                rows = result.fetchall()
            
            books = [dict(row._mapping) for row in rows]
            
            self.logger.debug(f"Retrieved {len(books)} books from audible library")
            return books
            
        except Exception as e:
            self.logger.error(f"Error getting all books: {e}", exc_info=True)
            return []
    
    def get_book_by_asin(self, asin: str) -> Optional[Dict[str, Any]]:
        """Get a single book by ASIN"""
        try:
            engine = self.connection_manager.get_engine()
            with engine.connect() as conn:
                row = conn.execute(text("SELECT * FROM audible_library WHERE asin = :asin"), {"asin": asin}).fetchone()
            
            if row:
                return dict(row._mapping)
            return None
                
        except Exception as e:
            self.logger.error(f"Error getting book by ASIN {asin}: {e}", exc_info=True)
            return None
    
    def delete_book(self, asin: str) -> bool:
        """Delete a book from the audible library."""
        try:
            with self._lock:
                engine = self.connection_manager.get_engine()
                with engine.begin() as conn:
                    result = conn.execute(text("DELETE FROM audible_library WHERE asin = :asin"), {"asin": asin})
                    rows_affected = result.rowcount
                
                if rows_affected > 0:
                    self.logger.debug(f"Deleted book with ASIN: {asin}")
                    return True
                else:
                    self.logger.warning(f"No book found with ASIN: {asin}")
                    return False
                
        except Exception as e:
            self.logger.error(f"Error deleting book {asin}: {e}", exc_info=True)
            return False
    
    def bulk_insert_or_update_books(self, books: List[Dict[str, Any]], 
                                   max_workers: int = 8) -> Tuple[int, int]:
        """Bulk insert or update books using parallel processing.

        Returns:
            Tuple of (successful_operations, failed_operations)
        """
        if not books:
            return 0, 0
        
        successful = 0
        failed = 0
        
        valid_books = [book for book in books if book.get('asin')]
        
        if len(valid_books) != len(books):
            failed = len(books) - len(valid_books)
            self.logger.warning(f"Filtered out {failed} books without valid ASINs")
        
        try:
            batch_size = min(20, max(1, len(valid_books) // max_workers))
            
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = []
                for i in range(0, len(valid_books), batch_size):
                    batch = valid_books[i:i + batch_size]
                    future = executor.submit(self._process_book_batch, batch)
                    futures.append(future)
                
                for future in as_completed(futures):
                    try:
                        batch_successful, batch_failed = future.result()
                        successful += batch_successful
                        failed += batch_failed
                    except Exception as e:
                        self.logger.error(f"Batch processing failed: {e}", exc_info=True)
                        failed += len(batch)
            
            self.logger.info(f"Bulk operation completed: {successful} successful, {failed} failed")
            return successful, failed
            
        except Exception as e:
            self.logger.error(f"Error in bulk insert/update: {e}", exc_info=True)
            return successful, len(valid_books) - successful
    
    def _process_book_batch(self, books: List[Dict[str, Any]]) -> Tuple[int, int]:
        """Process a batch of books for bulk operations"""
        successful = 0
        failed = 0
        
        try:
            engine = self.connection_manager.get_engine()
            with engine.begin() as conn:
                for book in books:
                    try:
                        insert_data = dict(book)
                        if 'last_updated' not in insert_data:
                            insert_data['last_updated'] = datetime.now().isoformat()
                        conn.execute(sqlite_insert(audible_library_table).prefix_with("OR REPLACE").values(**insert_data))
                        successful += 1
                    except Exception as e:
                        self.logger.error(f"Error processing book {book.get('asin', 'unknown')}: {e}", exc_info=True)
                        failed += 1
            
        except Exception as e:
            self.logger.error(f"Error in batch processing: {e}", exc_info=True)
            failed += len(books) - successful
        
        return successful, failed
    
    def get_outdated_books(self, hours: int = 6) -> List[str]:
        """Get ASINs of books that need metadata refresh."""
        try:
            engine = self.connection_manager.get_engine()
            cutoff_time = (datetime.now() - timedelta(hours=hours)).isoformat()
            
            with engine.connect() as conn:
                results = conn.execute(
                    text("""
                        SELECT asin FROM audible_library 
                        WHERE last_updated < :cutoff OR sync_status = 'pending'
                        ORDER BY last_updated ASC
                    """),
                    {"cutoff": cutoff_time}
                ).fetchall()
            
            asins = [row[0] for row in results]
            
            self.logger.debug(f"Found {len(asins)} books needing update (older than {hours} hours)")
            return asins
            
        except Exception as e:
            self.logger.error(f"Error getting outdated books: {e}", exc_info=True)
            return []
    
    def get_all_asins(self) -> List[str]:
        """Get all ASINs currently in the database."""
        try:
            engine = self.connection_manager.get_engine()
            with engine.connect() as conn:
                results = conn.execute(text("SELECT asin FROM audible_library ORDER BY asin")).fetchall()
            asins = [row[0] for row in results]
            
            self.logger.debug(f"Retrieved {len(asins)} ASINs from database")
            return asins
            
        except Exception as e:
            self.logger.error(f"Error getting all ASINs: {e}", exc_info=True)
            return []
    
    def get_purchased_asins(self) -> Set[str]:
        """ASINs the Audible account owns (the sync records a purchase date for each)."""
        with self.connection_manager.get_engine().connect() as conn:
            rows = conn.execute(text(
                "SELECT asin FROM audible_library "
                "WHERE purchase_date IS NOT NULL AND TRIM(purchase_date) != ''"
            ))
            return {row[0].strip().upper() for row in rows if row[0]}

    def get_library_stats(self) -> Dict[str, Any]:
        """Get library statistics"""
        try:
            engine = self.connection_manager.get_engine()
            with engine.connect() as conn:
                total_books = conn.execute(text("SELECT COUNT(*) FROM audible_library")).scalar()
                
                total_minutes = conn.execute(text("SELECT SUM(runtime_length_min) FROM audible_library WHERE runtime_length_min IS NOT NULL")).scalar() or 0
                total_hours = total_minutes // 60
                
                finished_books = conn.execute(text("SELECT COUNT(*) FROM audible_library WHERE listened = 1")).scalar()
                
                avg_rating = conn.execute(text("SELECT AVG(rating) FROM audible_library WHERE rating IS NOT NULL AND rating > 0")).scalar() or 0
                
                last_updated = conn.execute(text("SELECT MAX(last_updated) FROM audible_library")).scalar()
            
            completion_rate = (finished_books / total_books * 100) if total_books > 0 else 0
            
            return {
                'total_books': total_books,
                'total_hours': total_hours,
                'finished_books': finished_books,
                'completion_rate': round(completion_rate, 2),
                'average_rating': round(avg_rating, 2) if avg_rating else 0,
                'last_updated': last_updated or datetime.now().isoformat()
            }
            
        except Exception as e:
            self.logger.error(f"Error getting library stats: {e}", exc_info=True)
            return {
                'total_books': 0,
                'total_hours': 0,
                'finished_books': 0,
                'completion_rate': 0,
                'average_rating': 0,
                'last_updated': datetime.now().isoformat()
            }
    