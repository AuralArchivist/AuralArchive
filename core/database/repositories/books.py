"""
Module Name: books.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Database operations for book records, including bulk import and status updates.

Location:
    /core/database/repositories/books.py
"""

import re
from typing import List, Dict, Optional, Any, Tuple
from sqlalchemy import text, update as sa_update, insert as sa_insert, func
from ..schema import books as books_table
from ..error_handling import error_handler
from .book_rows import book_row_to_dict
from core import events
from core.logging import get_module_logger

_RATING_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def _rating_number(value: Any) -> Optional[float]:
    """Numeric rating for the Float column; text like 'N/A' becomes None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    found = _RATING_NUMBER.search(str(value))
    return float(found.group()) if found else None


class BookOperations:
    """Handles all book-related database operations"""

    def __init__(self, connection_manager, author_override_operations=None, *, logger=None):
        self.connection_manager = connection_manager
        self.logger = logger or get_module_logger("Service.Database.Books")
        self.author_override_operations = author_override_operations

    def _normalize_lookup_value(self, value: Optional[str]) -> str:
        return (value or '').strip().lower()

    def _sanitize_asin_value(self, asin_value: Optional[str]) -> Optional[str]:
        if asin_value is None:
            return None
        asin_str = str(asin_value).strip()
        if not asin_str or asin_str.upper() == 'N/A':
            return None
        return asin_str

    def _normalize_book_dict(self, book: Dict) -> Dict:
        """Add lowercase/alias keys for schema columns that have uppercase names.
        Templates and API responses expect 'asin' (lowercase); the DB schema
        column is 'ASIN'. Adding the alias here keeps all callers consistent."""
        if 'ASIN' in book and 'asin' not in book:
            book['asin'] = book['ASIN']
        return book

    def _should_merge_source(self, source: Optional[str], metadata_source: Optional[str]) -> bool:
        source_value = (source or '').strip().lower()
        metadata_value = (metadata_source or '').strip().lower()
        return source_value in ('audible', 'audible_library') or metadata_value == 'audible'

    def _find_existing_book_by_title_author(self, conn, title: Optional[str], author: Optional[str]) -> Optional[Dict[str, Any]]:
        normalized_title = self._normalize_lookup_value(title)
        if not normalized_title or normalized_title == 'unknown title':
            return None
        normalized_author = self._normalize_lookup_value(author)

        result = conn.execute(
            text("""
                SELECT id, asin, source, library_status, metadata_source
                FROM books
                WHERE lower(title) = :title AND lower(COALESCE(author, '')) = :author
                LIMIT 1
            """),
            {"title": normalized_title, "author": normalized_author},
        )
        row = result.fetchone()
        if row:
            return dict(row._mapping)
        return None

    def _prepare_db_row_from_book_data(self, book_data: Dict[str, Any], library_status: str) -> Tuple[Dict[str, Any], Optional[str]]:
        asin_clean = self._sanitize_asin_value(book_data.get('ASIN'))
        language_value = book_data.get('Language', 'Unknown Language')

        db_row = {
            'title': book_data.get('Title', 'Unknown Title'),
            'author': book_data.get('Author', 'Unknown Author'),
            'series': book_data.get('Series', 'N/A'),
            'sequence': book_data.get('Sequence', 'N/A'),
            'narrator': book_data.get('Narrator', 'Unknown Narrator'),
            'runtime': book_data.get('Runtime', 'Unknown Runtime'),
            'release_date': book_data.get('Release Date', 'Unknown Release Date'),
            'language': language_value,
            'publisher': book_data.get('Publisher', 'Unknown Publisher'),
            'overall_rating': book_data.get('Overall Rating') or None,
            'rating': _rating_number(book_data.get('Rating')),
            'library_status': library_status,
            'asin': asin_clean if asin_clean else 'N/A',
            'summary': book_data.get('Summary', 'No summary available'),
            'cover_image': book_data.get('Cover Image', ''),
            'num_ratings': book_data.get('num_ratings', 0),
            'series_asin': book_data.get('series_asin') or book_data.get('Series ASIN'),
            'source': book_data.get('source', 'manual'),
            'metadata_source': book_data.get('metadata_source', 'audible'),
            'file_path': book_data.get('file_path')
        }

        return db_row, asin_clean

    def _merge_book_record(self, conn, existing_record: Dict[str, Any], update_data: Dict[str, Any]) -> bool:
        allowed_columns = [
            'title', 'author', 'series', 'sequence', 'narrator', 'runtime', 'release_date',
            'language', 'publisher', 'overall_rating', 'rating', 'num_ratings', 'library_status',
            'asin', 'summary', 'cover_image', 'series_asin', 'source', 'metadata_source', 'file_path'
        ]

        update_dict: Dict[str, Any] = {}

        for column in allowed_columns:
            if column not in update_data:
                continue
            value = update_data[column]
            if value is None:
                continue

            if column == 'library_status':
                existing_library_status = (existing_record.get('library_status') or '').strip().lower()
                if existing_library_status and existing_library_status != 'wanted' and str(value).strip().lower() == 'wanted':
                    continue

            if column == 'asin':
                sanitized_asin = self._sanitize_asin_value(value)
                if not sanitized_asin:
                    continue
                existing_asin = self._sanitize_asin_value(existing_record.get('asin'))
                if existing_asin and existing_asin != sanitized_asin:
                    self.logger.debug("Skipping ASIN update due to mismatch", extra={
                        "book_id": existing_record.get('id'),
                        "existing_asin": existing_asin,
                        "incoming_asin": sanitized_asin
                    })
                    continue
                value = sanitized_asin

            update_dict[column] = value

        if not update_dict:
            return False

        stmt = (
            sa_update(books_table)
            .where(books_table.c.id == existing_record['id'])
            .values(**update_dict, updated_at=func.current_timestamp())
        )
        result = conn.execute(stmt)

        if result.rowcount > 0:
            self.logger.info("Updated existing book with Audible metadata", extra={
                "book_id": existing_record.get('id'),
                "title": update_data.get('title', existing_record.get('id')),
                "asin": existing_record.get('asin')
            })
            return True
        return False

    def _try_merge_existing_book_by_title(self, conn, update_data: Dict[str, Any]) -> bool:
        existing = self._find_existing_book_by_title_author(
            conn,
            update_data.get('title'),
            update_data.get('author')
        )
        if not existing:
            return False
        return self._merge_book_record(conn, existing, update_data)

    def _apply_author_overrides(self, book: Dict[str, Any]):
        """If an override exists for the book's author, update the record in-place."""
        if not book or not self.author_override_operations:
            return

        author_value = book.get('Author') or book.get('author')
        asin_value = book.get('ASIN') or book.get('asin')
        if not author_value:
            return

        preferred = self.author_override_operations.get_preferred_author_name(author_value, asin_value)
        if preferred and preferred != author_value:
            book['Author'] = preferred
            book['author'] = preferred
            if 'AuthorName' in book:
                book['AuthorName'] = preferred
    
    @error_handler.with_retry(max_retries=3, retry_delay=0.5)
    def check_book_exists(self, asin: str) -> bool:
        """Check if a book with the given ASIN exists."""
        if not asin or asin.strip() == '' or asin == 'N/A':
            return False
        
        engine = self.connection_manager.get_engine()
        with engine.connect() as conn:
            result = conn.execute(
                text("SELECT 1 FROM books WHERE asin = :asin LIMIT 1"),
                {"asin": asin},
            )
            exists = result.fetchone() is not None

        self.logger.debug("Book existence check", extra={"asin": asin, "exists": exists})
        return exists
    
    @error_handler.with_retry(max_retries=3, retry_delay=0.5)
    def add_book(self, book_data: Dict, library_status: str = "wanted") -> bool:
        """Add a book to the database."""
        if not error_handler.validate_required_fields(book_data, ['Title']):
            return False

        engine = self.connection_manager.get_engine()
        with engine.begin() as conn:
            db_row, asin_clean = self._prepare_db_row_from_book_data(book_data, library_status)

            if self._should_merge_source(db_row.get('source'), db_row.get('metadata_source')):
                existing_match = self._find_existing_book_by_title_author(conn, db_row.get('title'), db_row.get('author'))
                if existing_match and self._merge_book_record(conn, existing_match, db_row):
                    return True

            if asin_clean:
                dup_result = conn.execute(
                    text("SELECT id, asin, source, library_status, metadata_source FROM books WHERE asin = :asin LIMIT 1"),
                    {"asin": asin_clean},
                )
                duplicate = dup_result.fetchone()
                if duplicate:
                    existing_record = dict(duplicate._mapping)
                    if self._merge_book_record(conn, existing_record, db_row):
                        return True
                    self.logger.warning("Book already exists, skipping add", extra={
                        "asin": asin_clean,
                        "title": book_data.get('Title', 'Unknown'),
                        "source": db_row.get('source'),
                        "library_status": db_row.get('library_status')
                    })
                    return False

            insert_cols = [
                'title', 'author', 'series', 'sequence', 'narrator', 'runtime',
                'release_date', 'language', 'publisher', 'overall_rating',
                'rating', 'library_status', 'asin', 'summary', 'cover_image', 'num_ratings',
                'series_asin', 'source', 'metadata_source', 'file_path',
            ]
            insert_values = {col: db_row.get(col) for col in insert_cols}

            was_hidden = False
            if asin_clean:
                hidden_row = conn.execute(
                    text("SELECT 1 FROM title_visibility WHERE asin = :asin LIMIT 1"),
                    {"asin": asin_clean},
                ).fetchone()
                if hidden_row:
                    was_hidden = True
                    insert_values['monitored'] = False

            book_id = conn.execute(sa_insert(books_table).values(**insert_values)).inserted_primary_key[0]

            if was_hidden:
                conn.execute(
                    text("DELETE FROM title_visibility WHERE asin = :asin"),
                    {"asin": asin_clean},
                )

        error_handler.log_operation("Book added", f"'{book_data.get('Title')}' with library_status '{library_status}'")
        events.publish(events.BOOK_ADDED, book_id=book_id, asin=asin_clean,
                       cover_url=book_data.get('Cover Image') or book_data.get('cover_image'),
                       contributors=book_data.get('Contributors') or [])
        return True
    
    def bulk_insert_or_update_books(self, books: List[Dict[str, Any]], 
                                   max_workers: int = 8) -> Tuple[int, int]:
        """Bulk insert or update books using parallel processing.
        Designed for Audible sync - sets source='audible' and library_status='owned'.

        Returns:
            Tuple of (successful_operations, failed_operations)
        """
        if not books:
            return 0, 0
        
        successful = 0
        failed = 0
        added_books: List[Dict[str, Any]] = []
        series_needed: List[str] = []
        
        valid_books = [book for book in books if book.get('asin')]
        
        if len(valid_books) != len(books):
            failed = len(books) - len(valid_books)
            self.logger.warning("Filtered books without ASINs", extra={
                "filtered_count": failed,
                "total_received": len(books)
            })
        
        try:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            batch_size = min(20, max(1, len(valid_books) // max_workers))
            
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = []
                for i in range(0, len(valid_books), batch_size):
                    batch = valid_books[i:i + batch_size]
                    future = executor.submit(self._process_book_batch, batch)
                    futures.append(future)
                
                for future in as_completed(futures):
                    try:
                        batch_successful, batch_failed, batch_added, batch_series = future.result()
                        successful += batch_successful
                        failed += batch_failed
                        added_books.extend(batch_added)
                        series_needed.extend(batch_series)
                    except Exception as e:
                        self.logger.exception("Batch processing failed", extra={
                            "error": str(e)
                        })
                        failed += len(batch)
            
            self.logger.info("Bulk book operation completed", extra={
                "successful": successful,
                "failed": failed,
                "total": len(valid_books)
            })
            for book in added_books:
                events.publish(events.BOOK_ADDED, **book)
            for asin in series_needed:
                events.publish(events.SERIES_LOOKUP_NEEDED, asin=asin)
            return successful, failed
            
        except Exception as e:
            self.logger.exception("Error in bulk insert/update", extra={
                "error": str(e)
            })
            return successful, len(valid_books) - successful
    
    def _process_book_batch(self, books: List[Dict[str, Any]]) -> Tuple[int, int, List[Dict[str, Any]], List[str]]:
        """Process a batch; returns counts, the new books' event payloads, and
        ASINs of updated books that still need series data."""
        successful = 0
        failed = 0
        added_books: List[Dict[str, Any]] = []
        missing_series_asins: List[str] = []
        
        engine = self.connection_manager.get_engine()
        conn = None
        try:
            conn = engine.connect()
            
            for book in books:
                try:
                    field_mapping = {
                        'asin': 'asin',
                        'title': 'title',
                        'author': 'author',
                        'authors': 'author',
                        'narrator': 'narrator',
                        'runtime_length_min': 'runtime',
                        'release_date': 'release_date',
                        'language': 'language',
                        'publisher': 'publisher',
                        'rating': 'overall_rating',
                        'num_ratings': 'num_ratings',
                        'summary': 'summary',
                        'cover_image_url': 'cover_image',
                        'series_title': 'series',
                        'series_sequence': 'sequence',
                        'series_asin': 'series_asin',
                        'source': 'source',
                        'library_status': 'library_status',
                    }

                    db_data = {}
                    for api_field, db_field in field_mapping.items():
                        if api_field in book and book[api_field] is not None:
                            db_data[db_field] = book[api_field]

                    if 'rating' in book and book['rating'] is not None:
                        db_data['overall_rating'] = book['rating']
                        db_data['rating'] = _rating_number(book['rating'])

                    asin = book.get('asin')
                    db_data['asin'] = asin
                    db_data['title'] = book.get('title') or book.get('Title', 'Unknown Title')
                    db_data['author'] = book.get('author') or book.get('authors') or book.get('Author', 'Unknown Author')

                    library_status_value = db_data.get('library_status') or 'owned'
                    db_data['library_status'] = library_status_value

                    db_data['metadata_source'] = 'audible'

                    source_value = db_data.get('source') or book.get('source') or 'audible'
                    db_data['source'] = source_value

                    existing_row = None
                    if asin:
                        existing_result = conn.execute(
                            text("SELECT id, series_asin, library_status FROM books WHERE asin = :asin LIMIT 1"),
                            {"asin": asin}
                        )
                        existing_row = existing_result.fetchone()

                    series_candidate = False
                    if asin:
                        if not db_data.get('series_asin'):
                            if existing_row and existing_row[1]:
                                db_data['series_asin'] = existing_row[1]
                            else:
                                series_candidate = True
                        elif db_data.get('series_asin') in ('', None):
                            series_candidate = True

                    should_merge = self._should_merge_source(
                        db_data.get('source'),
                        db_data.get('metadata_source')
                    )

                    if should_merge and self._try_merge_existing_book_by_title(conn, db_data):
                        successful += 1
                        if series_candidate and asin:
                            missing_series_asins.append(asin)
                        continue

                    if existing_row:
                        existing_library_status = (existing_row[2] or '').strip().lower()
                        if existing_library_status and existing_library_status != 'wanted' and db_data.get('library_status') == 'wanted':
                            db_data.pop('library_status', None)

                        update_dict = {k: v for k, v in db_data.items() if k != 'asin'}
                        if update_dict:
                            conn.execute(
                                sa_update(books_table)
                                .where(books_table.c.id == existing_row[0])
                                .values(**update_dict, updated_at=func.current_timestamp())
                            )
                        successful += 1
                        if series_candidate and asin:
                            missing_series_asins.append(asin)
                        continue

                    book_id = conn.execute(sa_insert(books_table).values(**db_data)).inserted_primary_key[0]
                    successful += 1
                    added_books.append({'book_id': book_id, 'asin': asin, 'cover_url': db_data.get('cover_image'),
                                        'contributors': book.get('Contributors') or []})

                except Exception as e:
                    self.logger.exception("Error processing book in batch", extra={
                        "asin": book.get('asin', 'unknown'),
                        "error": str(e)
                    })
                    failed += 1
            
            conn.commit()
            conn.close()
            
        except Exception as e:
            self.logger.exception("Error in batch processing", extra={
                "error": str(e)
            })
            failed += len(books) - successful
        finally:
            if conn is not None:
                conn.close()
        
        return successful, failed, added_books, missing_series_asins

    def get_all_books(self) -> List[Dict]:
        """Get all books from the database."""
        engine = self.connection_manager.get_engine()
        conn = None
        try:
            conn = engine.connect()
            result = conn.execute(text("SELECT * FROM books ORDER BY title COLLATE NOCASE"))
            rows = result.fetchall()
            
            books = []
            for row in rows:
                book = book_row_to_dict(row)
                self._apply_author_overrides(book)
                self._normalize_book_dict(book)
                books.append(book)
            self.logger.debug("Retrieved books from database", extra={
                "count": len(books)
            })
            return books
        
        except Exception as e:
            self.logger.exception("Error getting all books", extra={
                "error": str(e)
            })
            return []
        
        finally:
            if conn is not None:
                conn.close()
    
    def get_unmonitored_books(self) -> List[Dict]:
        """Get all owned books with monitored explicitly set to False."""
        engine = self.connection_manager.get_engine()
        conn = None
        try:
            conn = engine.connect()
            result = conn.execute(text("SELECT * FROM books WHERE monitored = 0 ORDER BY title COLLATE NOCASE"))
            rows = result.fetchall()


            books = []
            for row in rows:
                book = book_row_to_dict(row)
                self._apply_author_overrides(book)
                self._normalize_book_dict(book)
                books.append(book)
            return books
        except Exception as e:
            self.logger.exception("Error getting unmonitored books", extra={"error": str(e)})
            return []
        finally:
            if conn is not None:
                conn.close()

    def get_recent_books(self, limit: int = 6) -> List[Dict]:
        """Get the most recently updated or added books."""
        engine = self.connection_manager.get_engine()
        conn = None
        try:
            conn = engine.connect()
            result = conn.execute(
                text("""
                    SELECT * FROM books
                    ORDER BY datetime(COALESCE(updated_at, created_at, '1970-01-01')) DESC
                    LIMIT :limit
                """),
                {"limit": limit}
            )
            rows = result.fetchall()


            books = []
            for row in rows:
                book = book_row_to_dict(row)
                self._apply_author_overrides(book)
                self._normalize_book_dict(book)
                books.append(book)
            self.logger.debug("Retrieved recent books", extra={
                "count": len(books),
                "limit": limit
            })
            return books

        except Exception as e:
            self.logger.exception("Error getting recent books", extra={
                "limit": limit,
                "error": str(e)
            })
            return []

        finally:
            if conn is not None:
                conn.close()

    def get_book_by_asin(self, asin: str) -> Optional[Dict]:
        """Get a specific book by ASIN."""
        if not asin or asin.strip() == '' or asin == 'N/A':
            return None
            
        engine = self.connection_manager.get_engine()
        conn = None
        try:
            conn = engine.connect()
            row_result = conn.execute(text("SELECT * FROM books WHERE asin = :asin"), {"asin": asin})
            book = row_result.fetchone()
            
            if book:
                result = book_row_to_dict(book)
                self._apply_author_overrides(result)
                
                result['AuthorName'] = result.get('Author')
                result['SeriesName'] = result.get('Series')
                result['book_number'] = result.get('Sequence')
                result['narrator_name'] = result.get('Narrator')
                result['release_date'] = result.get('Release Date')
                result['publisher_name'] = result.get('Publisher')
                result['asin'] = result.get('ASIN')
                result['runtime_length_min'] = result.get('Runtime')
                
                self.logger.debug("Retrieved book by ASIN", extra={
                    "asin": asin,
                    "title": result.get('Title')
                })
                return result
            else:
                self.logger.debug("Book not found by ASIN", extra={
                    "asin": asin
                })
                return None
        
        except Exception as e:
            self.logger.exception("Error getting book by ASIN", extra={
                "asin": asin,
                "error": str(e)
            })
            return None
        
        finally:
            if conn is not None:
                conn.close()
    
    def get_book_by_id(self, book_id: int) -> Optional[Dict]:
        """Get a specific book by ID."""
        engine = self.connection_manager.get_engine()
        conn = None
        try:
            conn = engine.connect()
            row_result = conn.execute(text("SELECT * FROM books WHERE id = :book_id"), {"book_id": book_id})
            book = row_result.fetchone()
            
            if book:
                result = book_row_to_dict(book)
                self._apply_author_overrides(result)
                self._normalize_book_dict(result)
                self.logger.debug("Retrieved book by ID", extra={
                    "book_id": book_id,
                    "title": result.get('Title')
                })
                return result
            else:
                self.logger.warning("Book not found by ID", extra={
                    "book_id": book_id
                })
                return None
        
        except Exception as e:
            self.logger.exception("Error getting book by ID", extra={
                "book_id": book_id,
                "error": str(e)
            })
            return None
        
        finally:
            if conn is not None:
                conn.close()
    
    @error_handler.with_retry(max_retries=3, retry_delay=0.5)
    def update_book_status(self, book_id: int, new_status: str) -> bool:
        """Transition a book's library_status, enforcing the write-authority
        rules in core/database/library_status.py (e.g. nothing may grant
        ownership except this explicit call)."""
        from .library_status import CHANGED, REFUSED, transition_library_status

        engine = self.connection_manager.get_engine()
        conn = None
        try:
            conn = engine.connect()

            status = (new_status or '').strip().lower()
            outcome = transition_library_status(conn, book_id, status)
            conn.commit()

            if outcome == CHANGED:
                events.publish(events.BOOK_STATUS_CHANGED, book_id=book_id, status=status)
            if outcome != REFUSED:
                error_handler.log_operation("Book library_status updated", f"ID {book_id} to '{new_status}'")
                return True
            else:
                self.logger.warning("Library status transition refused or book not found", extra={
                    "book_id": book_id,
                    "new_status": new_status
                })
                return False

        finally:
            if conn is not None:
                conn.close()

    @error_handler.with_retry(max_retries=3, retry_delay=0.5)
    def set_book_monitored(self, book_id: int, monitored: bool) -> bool:
        """Toggle whether a book is eligible for automatic search.

        Explicit user action only - never touched by import/sync code. Has
        no effect on manual actions (search modal, direct download), only on
        automatic_download_service's candidate selection.
        """
        engine = self.connection_manager.get_engine()
        conn = None
        try:
            conn = engine.connect()
            result = conn.execute(
                sa_update(books_table)
                .where(books_table.c.id == book_id)
                .values(monitored=bool(monitored), updated_at=func.current_timestamp())
            )
            conn.commit()
            return result.rowcount > 0
        finally:
            if conn is not None:
                conn.close()

    def delete_book(self, book_id: int) -> bool:
        """Delete a book from the database."""
        engine = self.connection_manager.get_engine()
        conn = None
        try:
            conn = engine.connect()
            result = conn.execute(text("DELETE FROM books WHERE id = :book_id"), {"book_id": book_id})
            conn.commit()
            
            if result.rowcount > 0:
                error_handler.log_operation("Book deleted", f"ID {book_id}")
                return True
            else:
                self.logger.warning("No book found for delete", extra={
                    "book_id": book_id
                })
                return False
        
        except Exception as e:
            self.logger.exception("Error deleting book", extra={
                "book_id": book_id,
                "error": str(e)
            })
            return False
        
        finally:
            if conn is not None:
                conn.close()
    
    def search_books(self, query: str) -> List[Dict]:
        """Search books by title, author, or series."""
        engine = self.connection_manager.get_engine()
        conn = None
        try:
            conn = engine.connect()
            search_pattern = f"%{query}%"
            
            search_sql = text("""
                SELECT * FROM books 
                WHERE title LIKE :pattern OR author LIKE :pattern OR series LIKE :pattern
                ORDER BY title COLLATE NOCASE
            """)
            result = conn.execute(search_sql, {"pattern": search_pattern})
            rows = result.fetchall()
            
            
            books = []
            for row in rows:
                book = book_row_to_dict(row)
                self._apply_author_overrides(book)
                self._normalize_book_dict(book)
                books.append(book)
            self.logger.debug("Search returned results", extra={
                "query": query,
                "result_count": len(books)
            })
            return books
        
        except Exception as e:
            self.logger.exception("Error searching books", extra={
                "query": query,
                "error": str(e)
            })
            return []
        
        finally:
            if conn is not None:
                conn.close()
    
    def get_books_by_status(self, status: str) -> List[Dict]:
        """Get all books with a specific library_status (case-insensitive)."""
        engine = self.connection_manager.get_engine()
        conn = None
        try:
            conn = engine.connect()
            result = conn.execute(
                text("SELECT * FROM books WHERE lower(library_status) = :status ORDER BY title COLLATE NOCASE"),
                {"status": (status or '').strip().lower()},
            )
            rows = result.fetchall()
            
            
            books = []
            for row in rows:
                book = book_row_to_dict(row)
                self._apply_author_overrides(book)
                self._normalize_book_dict(book)
                books.append(book)
            self.logger.debug("Retrieved books by status", extra={
                "status": status,
                "count": len(books)
            })
            return books
        
        except Exception as e:
            self.logger.exception("Error getting books by status", extra={
                "status": status,
                "error": str(e)
            })
            return []
        
        finally:
            if conn is not None:
                conn.close()
