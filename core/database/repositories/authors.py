"""
Module Name: authors.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Database operations for authors, including overrides and statistics.

Location:
    /core/database/repositories/authors.py
"""

import re
from collections import defaultdict
from typing import List, Dict, Set, DefaultDict
from sqlalchemy import text
from .book_rows import book_row_to_dict
from core.logging import get_module_logger
from core.text import runtime_to_minutes


_AUTHOR_DELIMITER_PATTERN = re.compile(r"\s*(?:,|&|;|\band\b|\bwith\b)\s*", re.IGNORECASE)


class AuthorOperations:
    """Handles all author-related database operations"""

    def __init__(self, connection_manager, author_override_operations=None, *, logger=None):
        self.connection_manager = connection_manager
        self.logger = logger or get_module_logger("Service.Database.Authors")
        self.author_override_operations = author_override_operations

    def _normalize_author_name(self, name: str) -> str:
        """Trim whitespace and collapse repeated spacing for consistent comparisons."""
        if not name:
            return ""

        cleaned = str(name).replace("\u00a0", " ")
        normalized = " ".join(cleaned.strip().split())
        return normalized

    def _apply_override_to_name(self, name: str) -> str:
        if not name or not self.author_override_operations:
            return name
        preferred = self.author_override_operations.get_preferred_author_name(name)
        return preferred or name

    def _apply_override_to_book(self, book: Dict):
        if not book or not self.author_override_operations:
            return
        author_value = book.get("Author")
        asin_value = book.get("ASIN")
        preferred = self.author_override_operations.get_preferred_author_name(author_value, asin_value)
        if preferred and preferred != author_value:
            book["Author"] = preferred
            book["author"] = preferred

    def _split_author_field(self, author_value: str) -> List[str]:
        """Split a stored author string into individual author names."""
        if not author_value:
            return []

        if not isinstance(author_value, str):
            author_value = str(author_value)

        parts = _AUTHOR_DELIMITER_PATTERN.split(author_value)
        authors: List[str] = []
        for part in parts:
            normalized = self._normalize_author_name(part)
            if normalized:
                authors.append(self._apply_override_to_name(normalized))
        return authors

    def _author_field_matches(self, author_field: str, target_author: str) -> bool:
        """Check whether a stored author field references the requested author."""
        if not author_field or not target_author:
            return False

        normalized_target = self._apply_override_to_name(
            self._normalize_author_name(target_author)
        ).lower()
        if not normalized_target:
            return False

        for name in self._split_author_field(author_field):
            if name.lower() == normalized_target:
                return True
        return False

    def _build_author_search_terms(self, author: str) -> List[str]:
        terms: List[str] = []
        normalized = self._normalize_author_name(author)
        if normalized:
            terms.append(normalized)

        if self.author_override_operations:
            aliases = self.author_override_operations.get_aliases_for_preferred(author)
            for alias in aliases:
                normalized_alias = self._normalize_author_name(alias)
                if normalized_alias and normalized_alias not in terms:
                    terms.append(normalized_alias)

        return terms if terms else [author]
    
    def get_all_authors(self) -> List[str]:
        """Get all unique authors."""
        engine = self.connection_manager.get_engine()
        conn = None
        try:
            conn = engine.connect()
            
            result = conn.execute(text("""
                SELECT DISTINCT author 
                FROM books 
                WHERE author IS NOT NULL AND author != '' 
                ORDER BY author COLLATE NOCASE
            """))

            unique_authors: Set[str] = set()
            for (author_field,) in result.fetchall():
                for name in self._split_author_field(author_field):
                    unique_authors.add(name)

            authors_list = sorted(unique_authors, key=lambda value: value.lower())
            self.logger.debug("Retrieved unique authors", extra={
                "author_count": len(authors_list)
            })
            return authors_list
        
        except Exception as e:
            self.logger.exception("Error getting all authors", extra={
                "error": str(e)
            })
            return []
        
        finally:
            if conn is not None:
                conn.close()
    
    def get_books_by_author(self, author: str) -> List[Dict]:
        """Get all books by a specific author."""
        engine = self.connection_manager.get_engine()
        conn = None
        try:
            conn = engine.connect()
            books: List[Dict] = []
            seen_ids: Set[int] = set()
            search_terms = self._build_author_search_terms(author)

            for term in search_terms:
                search_pattern = f"%{term}%"
                result = conn.execute(
                    text("SELECT * FROM books WHERE author LIKE :pattern ORDER BY title COLLATE NOCASE"),
                    {"pattern": search_pattern}
                )
                rows = result.fetchall()

                for row in rows:
                    book_id = row[0]
                    if book_id in seen_ids:
                        continue

                    book = book_row_to_dict(row)
                    if self._author_field_matches(book.get("Author"), author):
                        self._apply_override_to_book(book)
                        books.append(book)
                        seen_ids.add(book_id)

            self.logger.debug("Retrieved books for author", extra={
                "author": author,
                "book_count": len(books),
                "search_terms": search_terms
            })
            return books
        
        except Exception as e:
            self.logger.exception("Error getting books for author", extra={
                "author": author,
                "error": str(e)
            })
            return []
        
        finally:
            if conn is not None:
                conn.close()
    
    def get_author_stats(self, author: str) -> Dict:
        """Get statistics for a specific author."""
        try:
            books = self.get_books_by_author(author)

            stats = {
                'author': author,
                'total_books': len(books),
                'series_count': 0,
                'status_distribution': {},
                'total_runtime_minutes': 0,
                'average_rating': 0,
                'languages': set(),
                'publishers': set(),
                'date_range': {'earliest': None, 'latest': None}
            }

            total_rating = 0.0
            rated_books = 0
            series_set: Set[str] = set()

            for book in books:
                status_value = book.get('library_status')
                if status_value:
                    stats['status_distribution'][status_value] = stats['status_distribution'].get(status_value, 0) + 1

                series_name = book.get('Series')
                if series_name and series_name != 'N/A':
                    series_set.add(series_name)

                stats['total_runtime_minutes'] += runtime_to_minutes(book.get('Runtime'))

                rating_value = book.get('Overall Rating') or book.get('Rating')
                if rating_value and rating_value != 'N/A':
                    try:
                        total_rating += float(rating_value)
                        rated_books += 1
                    except (TypeError, ValueError):
                        pass

                language_value = book.get('Language')
                if language_value:
                    stats['languages'].add(language_value)

                publisher_value = book.get('Publisher')
                if publisher_value:
                    stats['publishers'].add(publisher_value)

                release_date = book.get('Release Date')
                if release_date and release_date != 'Unknown':
                    earliest = stats['date_range']['earliest']
                    latest = stats['date_range']['latest']
                    if not earliest or release_date < earliest:
                        stats['date_range']['earliest'] = release_date
                    if not latest or release_date > latest:
                        stats['date_range']['latest'] = release_date

            stats['series_count'] = len(series_set)
            stats['average_rating'] = round(total_rating / rated_books, 2) if rated_books > 0 else 0
            stats['total_runtime_hours'] = round(stats['total_runtime_minutes'] / 60, 1)
            stats['languages'] = list(stats['languages'])
            stats['publishers'] = list(stats['publishers'])

            self.logger.debug("Calculated author stats", extra={
                "author": author,
                "book_count": stats.get('total_books', 0)
            })
            return stats

        except Exception as e:
            self.logger.exception("Error getting author stats", extra={
                "author": author,
                "error": str(e)
            })
            return {'error': str(e)}
    
    def search_authors(self, query: str) -> List[str]:
        """Search authors by name."""
        try:
            normalized_query = (query or "").strip().lower()
            if not normalized_query:
                return []

            all_authors = self.get_all_authors()
            matched = [name for name in all_authors if normalized_query in name.lower()]
            matched.sort(key=lambda value: value.lower())
            self.logger.debug("Author search completed", extra={
                "query": query,
                "result_count": len(matched)
            })
            return matched

        except Exception as e:
            self.logger.exception("Error searching authors", extra={
                "query": query,
                "error": str(e)
            })
            return []
    
    def get_top_authors_by_book_count(self, limit: int = 10) -> List[Dict]:
        """Get top authors by number of books."""
        engine = self.connection_manager.get_engine()
        conn = None
        try:
            conn = engine.connect()

            result = conn.execute(text("""
                SELECT author
                FROM books
                WHERE author IS NOT NULL AND author != ''
            """))

            author_counts: DefaultDict[str, int] = defaultdict(int)
            for (author_field,) in result.fetchall():
                for name in self._split_author_field(author_field):
                    author_counts[name] += 1

            sorted_authors = sorted(
                author_counts.items(),
                key=lambda item: item[1],
                reverse=True
            )[:limit]

            results = [{'author': name, 'book_count': count} for name, count in sorted_authors]
            self.logger.debug("Retrieved top authors by book count", extra={
                "limit": limit,
                "result_count": len(results)
            })
            return results

        except Exception as e:
            self.logger.exception("Error getting top authors by book count", extra={
                "limit": limit,
                "error": str(e)
            })
            return []

        finally:
            if conn is not None:
                conn.close()
    
    def get_authors_with_series(self) -> List[Dict]:
        """Get authors who have books in series."""
        engine = self.connection_manager.get_engine()
        conn = None
        try:
            conn = engine.connect()
            
            result = conn.execute(text("""
                SELECT author, series
                FROM books
                WHERE author IS NOT NULL AND author != '' AND series != 'N/A'
            """))

            author_series: DefaultDict[str, Set[str]] = defaultdict(set)
            author_book_counts: DefaultDict[str, int] = defaultdict(int)

            for author_field, series_name in result.fetchall():
                if not series_name or series_name == 'N/A':
                    continue

                for name in self._split_author_field(author_field):
                    author_series[name].add(series_name)
                    author_book_counts[name] += 1

            author_entries = [
                {
                    'author': name,
                    'series_count': len(series_set),
                    'total_books': author_book_counts.get(name, 0)
                }
                for name, series_set in author_series.items()
                if series_set
            ]

            author_entries.sort(key=lambda item: (item['series_count'], item['total_books']), reverse=True)
            self.logger.debug("Retrieved authors with series", extra={
                "result_count": len(author_entries)
            })
            return author_entries
        
        except Exception as e:
            self.logger.exception("Error getting authors with series", extra={
                "error": str(e)
            })
            return []
        
        finally:
            if conn is not None:
                conn.close()
    
    
    def get_author_metadata(self, author_name: str) -> Dict:
        """Get author metadata from the authors table."""
        engine = self.connection_manager.get_engine()
        conn = None
        try:
            conn = engine.connect()
            row = conn.execute(
                text("SELECT * FROM authors WHERE name = :name"),
                {"name": author_name}
            ).fetchone()
            if row:
                metadata = dict(row._mapping)
                self.logger.debug("Retrieved author metadata", extra={
                    "author": author_name
                })
                return metadata
            else:
                self.logger.debug("No author metadata found", extra={
                    "author": author_name
                })
                return {}
        
        except Exception as e:
            self.logger.exception("Error getting author metadata", extra={
                "author": author_name,
                "error": str(e)
            })
            return {}
        
        finally:
            if conn is not None:
                conn.close()
    
    def upsert_author_metadata(self, author_data: Dict) -> bool:
        """Insert or update author metadata."""
        engine = self.connection_manager.get_engine()
        try:
            with engine.begin() as conn:
                existing = conn.execute(
                    text("SELECT id FROM authors WHERE name = :name"),
                    {"name": author_data['name']}
                ).fetchone()
                
                if existing:
                    conn.execute(
                        text("""
                            UPDATE authors SET
                                audible_author_id = :audible_author_id,
                                author_image_url = :author_image_url,
                                author_bio = :author_bio,
                                author_page_url = :author_page_url,
                                total_books_count = :total_books_count,
                                audible_books_count = :audible_books_count,
                                last_fetched_at = CURRENT_TIMESTAMP,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE name = :name
                        """),
                        {
                            "audible_author_id": author_data.get('audible_author_id'),
                            "author_image_url": author_data.get('author_image_url'),
                            "author_bio": author_data.get('author_bio'),
                            "author_page_url": author_data.get('author_page_url'),
                            "total_books_count": author_data.get('total_books_count', 0),
                            "audible_books_count": author_data.get('audible_books_count', 0),
                            "name": author_data['name']
                        }
                    )
                    self.logger.debug("Updated author metadata", extra={
                        "author": author_data.get('name')
                    })
                else:
                    conn.execute(
                        text("""
                            INSERT INTO authors (
                                name, audible_author_id, author_image_url, author_bio,
                                author_page_url, total_books_count, audible_books_count,
                                last_fetched_at
                            ) VALUES (
                                :name, :audible_author_id, :author_image_url, :author_bio,
                                :author_page_url, :total_books_count, :audible_books_count,
                                CURRENT_TIMESTAMP
                            )
                        """),
                        {
                            "name": author_data['name'],
                            "audible_author_id": author_data.get('audible_author_id'),
                            "author_image_url": author_data.get('author_image_url'),
                            "author_bio": author_data.get('author_bio'),
                            "author_page_url": author_data.get('author_page_url'),
                            "total_books_count": author_data.get('total_books_count', 0),
                            "audible_books_count": author_data.get('audible_books_count', 0)
                        }
                    )
                    self.logger.debug("Inserted new author metadata", extra={
                        "author": author_data.get('name')
                    })
            return True
        
        except Exception as e:
            self.logger.exception("Error upserting author metadata", extra={
                "author": author_data.get('name'),
                "error": str(e)
            })
            return False
    