"""
Module Name: series.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Database operations for series metadata and membership.

Location:
    /core/database/repositories/series.py
"""

from typing import List, Dict, Optional
from sqlalchemy import text
from ..error_handling import error_handler
from core.logging import get_module_logger


class SeriesOperations:
    """Handles all series-related database operations"""

    def __init__(self, connection_manager, author_override_operations=None, *, logger=None):
        self.connection_manager = connection_manager
        self.logger = logger or get_module_logger("Service.Database.Series")
        self.author_override_operations = author_override_operations

    def _apply_author_override(self, author_name: Optional[str], asin: Optional[str]) -> Optional[str]:
        if not author_name or not self.author_override_operations:
            return author_name
        preferred = self.author_override_operations.get_preferred_author_name(author_name, asin)
        return preferred or author_name
    
    @error_handler.with_retry(max_retries=3, retry_delay=0.5)
    def upsert_series_metadata(self, series_data: Dict) -> bool:
        """Insert or update series metadata"""
        engine = self.connection_manager.get_engine()
        try:
            with engine.begin() as conn:
                conn.execute(
                    text("""
                        INSERT INTO series_metadata (
                            series_asin, series_title, series_url, sku, sku_lite,
                            total_books, description, cover_url, last_synced, updated_at
                        ) VALUES (
                            :series_asin, :series_title, :series_url, :sku, :sku_lite,
                            :total_books, :description, :cover_url, datetime('now'), datetime('now')
                        )
                        ON CONFLICT(series_asin) DO UPDATE SET
                            series_title = excluded.series_title,
                            series_url = excluded.series_url,
                            sku = excluded.sku,
                            sku_lite = excluded.sku_lite,
                            total_books = excluded.total_books,
                            description = excluded.description,
                            cover_url = excluded.cover_url,
                            last_synced = excluded.last_synced,
                            updated_at = datetime('now')
                    """),
                    {
                        "series_asin": series_data.get('series_asin'),
                        "series_title": series_data.get('series_title'),
                        "series_url": series_data.get('series_url'),
                        "sku": series_data.get('sku'),
                        "sku_lite": series_data.get('sku_lite'),
                        "total_books": series_data.get('total_books', 0),
                        "description": series_data.get('description'),
                        "cover_url": series_data.get('cover_url'),
                    }
                )
            self.logger.info("Upserted series metadata", extra={
                "series_title": series_data.get('series_title'),
                "series_asin": series_data.get('series_asin')
            })
            return True
        
        except Exception as e:
            self.logger.exception("Error upserting series metadata", extra={
                "series_title": series_data.get('series_title'),
                "series_asin": series_data.get('series_asin'),
                "error": str(e)
            })
            return False
    
    @error_handler.with_retry(max_retries=3, retry_delay=0.5)
    def upsert_series_book(self, series_book_data: Dict) -> bool:
        """Insert or update a book in a series with full metadata"""
        engine = self.connection_manager.get_engine()
        try:
            with engine.begin() as conn:
                in_library = conn.execute(
                    text("SELECT 1 FROM books WHERE asin = :asin"),
                    {"asin": series_book_data.get('book_asin')}
                ).fetchone() is not None
                
                normalized_author = self._apply_author_override(
                    series_book_data.get('author'),
                    series_book_data.get('book_asin')
                )

                conn.execute(
                    text("""
                        INSERT INTO series_books (
                            series_asin, book_asin, book_title, sequence, sort_order,
                            relationship_type, in_library,
                            author, narrator, publisher, release_date, runtime,
                            rating, num_ratings, summary, cover_image, language,
                            last_checked
                        ) VALUES (
                            :series_asin, :book_asin, :book_title, :sequence, :sort_order,
                            :relationship_type, :in_library,
                            :author, :narrator, :publisher, :release_date, :runtime,
                            :rating, :num_ratings, :summary, :cover_image, :language,
                            datetime('now')
                        )
                        ON CONFLICT(series_asin, book_asin) DO UPDATE SET
                            book_title = excluded.book_title,
                            sequence = excluded.sequence,
                            sort_order = excluded.sort_order,
                            relationship_type = excluded.relationship_type,
                            in_library = excluded.in_library,
                            author = excluded.author,
                            narrator = excluded.narrator,
                            publisher = excluded.publisher,
                            release_date = excluded.release_date,
                            runtime = excluded.runtime,
                            rating = excluded.rating,
                            num_ratings = excluded.num_ratings,
                            summary = excluded.summary,
                            cover_image = excluded.cover_image,
                            language = excluded.language,
                            last_checked = datetime('now')
                    """),
                    {
                        "series_asin": series_book_data.get('series_asin'),
                        "book_asin": series_book_data.get('book_asin'),
                        "book_title": series_book_data.get('book_title'),
                        "sequence": series_book_data.get('sequence'),
                        "sort_order": series_book_data.get('sort_order'),
                        "relationship_type": series_book_data.get('relationship_type', 'child'),
                        "in_library": in_library,
                        "author": normalized_author,
                        "narrator": series_book_data.get('narrator'),
                        "publisher": series_book_data.get('publisher'),
                        "release_date": series_book_data.get('release_date'),
                        "runtime": series_book_data.get('runtime'),
                        "rating": series_book_data.get('rating'),
                        "num_ratings": series_book_data.get('num_ratings'),
                        "summary": series_book_data.get('summary'),
                        "cover_image": series_book_data.get('cover_image'),
                        "language": series_book_data.get('language'),
                    }
                )
            return True
        
        except Exception as e:
            self.logger.exception("Error upserting series book", extra={
                "series_asin": series_book_data.get('series_asin'),
                "book_asin": series_book_data.get('book_asin'),
                "error": str(e)
            })
            return False
    
    @error_handler.with_retry(max_retries=3, retry_delay=0.5)
    def get_all_series(self) -> List[Dict]:
        """Get all series with statistics"""
        engine = self.connection_manager.get_engine()
        conn = None
        try:
            conn = engine.connect()
            
            result = conn.execute(text("""
                SELECT 
                    sm.series_asin,
                    sm.series_title,
                    sm.series_url,
                    sm.cover_url,
                    sm.total_books,
                    COUNT(sb.id) as known_books,
                    SUM(CASE WHEN sb.in_library = 1 THEN 1 ELSE 0 END) as owned_books,
                    sm.last_synced
                FROM series_metadata sm
                LEFT JOIN series_books sb ON sm.series_asin = sb.series_asin
                GROUP BY sm.series_asin
                ORDER BY sm.series_title ASC
            """))
            
            rows = result.fetchall()
            series_list = []
            
            for row in rows:
                raw_total_books = row[4] or 0
                known_books = row[5] or 0
                owned_books = row[6] or 0

                total_books = known_books if known_books > 0 else raw_total_books
                effective_total = max(total_books, owned_books)
                missing_books = max(effective_total - owned_books, 0)
                completion_percentage = 0.0
                if effective_total > 0:
                    completion_percentage = round((owned_books / effective_total) * 100, 1)

                series_list.append({
                    'series_asin': row[0],
                    'series_title': row[1],
                    'series_url': row[2],
                    'cover_url': row[3],
                    'total_books': effective_total,
                    'known_books': known_books,
                    'owned_books': owned_books,
                    'missing_books': missing_books,
                    'completion_percentage': completion_percentage,
                    'last_synced': row[7]
                })
            
            return series_list
        
        except Exception as e:
            self.logger.exception("Error getting all series", extra={
                "error": str(e)
            })
            return []
        finally:
            if conn is not None:
                conn.close()
    
    @error_handler.with_retry(max_retries=3, retry_delay=0.5)
    def get_series_by_asin(self, series_asin: str) -> Optional[Dict]:
        """Get series metadata by ASIN"""
        engine = self.connection_manager.get_engine()
        conn = None
        try:
            conn = engine.connect()
            
            row = conn.execute(
                text("SELECT * FROM series_metadata WHERE series_asin = :asin"),
                {"asin": series_asin}
            ).fetchone()
            if not row:
                return None
            
            return {
                'series_asin': row[0],
                'series_title': row[1],
                'series_url': row[2],
                'sku': row[3],
                'sku_lite': row[4],
                'total_books': row[5],
                'description': row[6],
                'cover_url': row[7],
                'last_synced': row[8],
                'created_at': row[9],
                'updated_at': row[10]
            }
        
        except Exception as e:
            self.logger.exception("Error getting series by ASIN", extra={
                "series_asin": series_asin,
                "error": str(e)
            })
            return None
        finally:
            if conn is not None:
                conn.close()
    
    @error_handler.with_retry(max_retries=3, retry_delay=0.5)
    def get_series_books(self, series_asin: str) -> List[Dict]:
        engine = self.connection_manager.get_engine()
        conn = None
        try:
            conn = engine.connect()
            
            self.logger.debug("Executing series books query", extra={
                "series_asin": series_asin
            })
            
            result = conn.execute(
                text("""
                SELECT 
                    sb.book_asin,
                    sb.book_title,
                    sb.sequence,
                    sb.sort_order,
                    sb.in_library,
                    CASE 
                        WHEN b.file_path IS NOT NULL AND TRIM(b.file_path) != '' THEN 1
                        ELSE 0
                    END as has_local_file,
                    CASE 
                        WHEN b.asin IS NOT NULL THEN 1
                        ELSE 0
                    END as has_library_entry,
                    COALESCE(b.title, sb.book_title) as title,
                    COALESCE(b.author, sb.author) as author,
                    COALESCE(b.narrator, sb.narrator) as narrator,
                    COALESCE(b.runtime, sb.runtime) as runtime,
                    COALESCE(b.rating, sb.rating) as rating,
                    COALESCE(b.num_ratings, sb.num_ratings) as num_ratings,
                    COALESCE(b.release_date, sb.release_date) as release_date,
                    CASE 
                        WHEN sb.cover_image LIKE '/cache/images/%' THEN sb.cover_image
                        WHEN b.cover_image LIKE '/cache/images/%' THEN b.cover_image
                        ELSE COALESCE(sb.cover_image, b.cover_image)
                    END as cover_image,
                    COALESCE(b.publisher, sb.publisher) as publisher,
                    COALESCE(b.summary, sb.summary) as summary,
                    b.library_status as lib_status,
                    COALESCE(
                        (SELECT bx.asin FROM books bx
                            WHERE bx.asin = sb.book_asin
                              AND (LOWER(COALESCE(bx.library_status, '')) = 'owned'
                                   OR (bx.file_path IS NOT NULL AND TRIM(bx.file_path) != ''))
                            LIMIT 1),
                        (SELECT bx.asin FROM books bx
                            WHERE bx.series_asin = sb.series_asin
                              AND (
                                  LOWER(TRIM(bx.title)) = LOWER(TRIM(sb.book_title))
                                  OR (TRIM(COALESCE(bx.sequence, '')) != ''
                                      AND TRIM(COALESCE(bx.sequence, '')) = TRIM(COALESCE(sb.sequence, '')))
                              )
                              AND (LOWER(COALESCE(bx.library_status, '')) = 'owned'
                                   OR (bx.file_path IS NOT NULL AND TRIM(bx.file_path) != ''))
                            ORDER BY CASE WHEN bx.file_path IS NOT NULL AND TRIM(bx.file_path) != '' THEN 0 ELSE 1 END
                            LIMIT 1)
                    ) as owned_lib_asin,
                    b.monitored as book_monitored,
                    tv.asin as hidden_asin
                FROM series_books sb
                LEFT JOIN books b ON sb.book_asin = b.asin
                LEFT JOIN title_visibility tv ON sb.book_asin = tv.asin
                WHERE sb.series_asin = :series_asin
                ORDER BY CAST(sb.sort_order AS REAL) ASC, COALESCE(b.title, sb.book_title) ASC
                """),
                {"series_asin": series_asin}
            )
            
            rows = result.fetchall()
            self.logger.debug("Series books query complete", extra={
                "series_asin": series_asin,
                "row_count": len(rows)
            })
            
            books = []
            
            for row in rows:
                row = row._mapping
                has_local_file = row['has_local_file'] == 1
                library_entry_exists = row['has_library_entry'] == 1
                book_title = row['title'] if row['title'] else f"Book {row['sequence']}" if row['sequence'] else "Unknown Title"
                db_status = (row['lib_status'] or '').strip().lower()
                owned_lib_asin = row['owned_lib_asin']
                is_owned = bool(owned_lib_asin) or db_status == 'owned' or has_local_file
                link_asin = owned_lib_asin or row['book_asin']
                if is_owned:
                    library_status = 'in_library'
                elif db_status in ('wanted', 'queued', 'pending', 'processing') or library_entry_exists:
                    library_status = 'wanted'
                else:
                    library_status = 'missing'
                
                author_name = row['author'] or 'Unknown Author'
                author_name = self._apply_author_override(author_name, row['book_asin']) or author_name

                book_monitored = row['book_monitored']
                hidden_asin = row['hidden_asin']
                if is_owned:
                    monitored = bool(book_monitored) if book_monitored is not None else True
                else:
                    monitored = hidden_asin is None

                books.append({
                    'asin': link_asin,
                    'title': book_title,
                    'sequence': row['sequence'],
                    'sort_order': row['sort_order'],
                    'in_library': is_owned,
                    'author': author_name,
                    'narrator': row['narrator'] or 'Unknown Narrator',
                    'runtime': row['runtime'] or 0,
                    'rating': row['rating'] or '',
                    'num_ratings': row['num_ratings'] or 0,
                    'release_date': row['release_date'] or '',
                    'cover_image': row['cover_image'] or '',
                    'publisher': row['publisher'] or 'Unknown Publisher',
                    'summary': row['summary'] or 'No summary available',
                    'library_status': library_status,
                    'monitored': monitored
                })
            
            self.logger.debug("Returning series books", extra={
                "series_asin": series_asin,
                "book_count": len(books)
            })
            return books
        
        except Exception as e:
            self.logger.exception("Error getting series books", extra={
                "series_asin": series_asin,
                "error": str(e)
            })
            return []
        finally:
            if conn is not None:
                conn.close()
    
    @error_handler.with_retry(max_retries=3, retry_delay=0.5)
    def update_book_series_asin(self, book_asin: str, series_asin: str) -> bool:
        """Update the series_asin for a book in the books table"""
        engine = self.connection_manager.get_engine()
        try:
            with engine.begin() as conn:
                conn.execute(
                    text("UPDATE books SET series_asin = :series_asin WHERE asin = :book_asin"),
                    {"series_asin": series_asin, "book_asin": book_asin}
                )
            return True
        
        except Exception as e:
            self.logger.exception("Error updating book series ASIN", extra={
                "book_asin": book_asin,
                "series_asin": series_asin,
                "error": str(e)
            })
            return False
    
    @error_handler.with_retry(max_retries=3, retry_delay=0.5)
    def sync_library_status(self) -> int:
        """Sync in_library status for all series_books based on books table"""
        engine = self.connection_manager.get_engine()
        try:
            with engine.begin() as conn:
                result = conn.execute(text("""
                    UPDATE series_books
                    SET in_library = (
                        SELECT CASE
                            WHEN EXISTS (
                                SELECT 1 FROM books
                                WHERE (
                                          books.asin = series_books.book_asin
                                          OR (books.series_asin = series_books.series_asin
                                              AND (
                                                  LOWER(TRIM(books.title)) = LOWER(TRIM(series_books.book_title))
                                                  OR (TRIM(COALESCE(books.sequence, '')) != ''
                                                      AND TRIM(COALESCE(books.sequence, '')) = TRIM(COALESCE(series_books.sequence, '')))
                                              ))
                                      )
                                  AND (
                                      LOWER(COALESCE(books.library_status, '')) = 'owned'
                                      OR (books.file_path IS NOT NULL AND TRIM(books.file_path) != '')
                                  )
                            ) THEN 1 ELSE 0 END
                    ),
                    last_checked = datetime('now')
                """))
                updated = result.rowcount
            self.logger.debug("Synced library status for series books", extra={
                "updated_count": updated
            })
            return updated
        
        except Exception as e:
            self.logger.exception("Error syncing library status", extra={
                "error": str(e)
            })
            return 0
    
    @error_handler.with_retry(max_retries=3, retry_delay=0.5)
    def get_all_series_asins(self, limit: Optional[int] = None) -> List[str]:
        """Get all unique series ASINs from books in the library"""
        engine = self.connection_manager.get_engine()
        conn = None
        try:
            conn = engine.connect()
            
            query = """
                SELECT DISTINCT series_asin 
                FROM books 
                WHERE series_asin IS NOT NULL 
                AND series_asin != ''
                ORDER BY series_asin
            """
            params = {}
            if limit:
                query += " LIMIT :limit"
                params["limit"] = int(limit)
            
            result = conn.execute(text(query), params)
            rows = result.fetchall()
            
            series_asins = [row[0] for row in rows]
            self.logger.debug("Found series ASINs in library", extra={
                "count": len(series_asins),
                "limit": limit
            })
            return series_asins
            
        except Exception as e:
            self.logger.exception("Error getting series ASINs", extra={
                "limit": limit,
                "error": str(e)
            })
            return []
        finally:
            if conn is not None:
                conn.close()
