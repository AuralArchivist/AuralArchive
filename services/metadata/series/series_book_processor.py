"""
Module Name: series_book_processor.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Process series book metadata, deduplicate editions, and mark library status before database sync.

Location:
    /services/metadata/series/series_book_processor.py
"""

from sqlalchemy import text

from core.logging import get_module_logger
from core.language_matching import language_allowed
from services.settings.languages import get_preferred_languages
from integrations.audible.availability import is_buyable


class SeriesBookProcessor:
    """Processes series book data for storage."""
    
    def __init__(self, db_service, logger=None):
        self.logger = logger or get_module_logger("Service.Audible.Series.BookProcessor")
        self.db = db_service
    
    def process_series_books(self, series_asin, books_data):
        """Process series books and determine library status"""
        try:
            filtered_books = [book for book in books_data if is_buyable(book)]

            if not filtered_books:
                self.logger.info(
                    "All series entries filtered out due to unavailable titles",
                    extra={"series_asin": series_asin, "incoming": len(books_data)},
                )
                return []

            books_data = self._deduplicate_books(filtered_books)

            preferred_languages = get_preferred_languages()
            processed_books = []
            language_filtered = 0

            for book in books_data:
                if not is_buyable(book):
                    self.logger.debug(
                        "Skipping unbuyable series title",
                        extra={"title": book.get('title'), "asin": book.get('asin')},
                    )
                    continue

                language_value = book.get('language', 'en')
                if not language_allowed(language_value, preferred_languages):
                    language_filtered += 1
                    self.logger.debug(
                        "Skipping series title due to language filter",
                        extra={"title": book.get('title'), "asin": book.get('asin'), "language": language_value},
                    )
                    continue

                book_asin = book.get('asin')
                
                in_library = self._check_in_library(book_asin)
                
                processed_book = {
                    'series_asin': series_asin,
                    'book_asin': book_asin,
                    'book_title': book.get('title', 'Unknown'),
                    'sequence': book.get('sequence', ''),
                    'sort_order': book.get('sort_order', 0),
                    'in_library': in_library,
                    'author': book.get('author', 'Unknown Author'),
                    'narrator': book.get('narrator', 'Unknown Narrator'),
                    'publisher': book.get('publisher', 'Unknown Publisher'),
                    'release_date': book.get('release_date', ''),
                    'runtime': book.get('runtime', 0),
                    'rating': book.get('rating', ''),
                    'num_ratings': book.get('num_ratings', 0),
                    'summary': book.get('summary', 'No summary available'),
                    'cover_image': book.get('cover_image', ''),
                    'language': book.get('language', 'en')
                }
                
                processed_books.append(processed_book)
            
            self.logger.info(
                "Processed books for series",
                extra={"series_asin": series_asin, "count": len(processed_books), "language_filtered": language_filtered},
            )
            return processed_books

        except Exception as e:
            self.logger.error(
                "Error processing series books",
                extra={"series_asin": series_asin, "error": str(e)},
                exc_info=True,
            )
            return []

    def _deduplicate_books(self, books_data):
        """Remove duplicate book entries from series data
        Audible sometimes returns multiple editions (ISBN, ASIN, regional variants)

        Strategy:
        1. Group by title + sequence
        2. For each group, prefer Audible ASINs (B0...) over ISBNs (numeric)
        3. If multiple Audible ASINs, prefer the shortest/most recent one
        """
        try:
            if not books_data:
                return []
            
            groups = {}
            for book in books_data:
                title = book.get('title', '').strip()
                sequence = str(book.get('sequence', '')).strip()
                
                normalized_title = self._normalize_title(title)
                key = (normalized_title, sequence)
                
                if key not in groups:
                    groups[key] = []
                groups[key].append(book)
            
            deduplicated = []
            for key, book_group in groups.items():
                if len(book_group) == 1:
                    deduplicated.append(book_group[0])
                else:
                    best_book = self._select_best_edition(book_group)
                    deduplicated.append(best_book)
                    
                    asins = [b.get('asin') for b in book_group]
                    self.logger.debug(
                        "Deduplicated editions",
                        extra={
                            "normalized_title": key[0],
                            "sequence": key[1],
                            "kept_asin": best_book.get('asin'),
                            "skipped": [a for a in asins if a != best_book.get('asin')],
                        },
                    )
            
            if len(deduplicated) < len(books_data):
                self.logger.debug(
                    "Deduplication reduced book count",
                    extra={
                        "before": len(books_data),
                        "after": len(deduplicated),
                        "removed": len(books_data) - len(deduplicated),
                    },
                )
            
            return deduplicated
            
        except Exception as e:
            self.logger.error("Error deduplicating books", extra={"error": str(e)}, exc_info=True)
            return books_data
    
    def _normalize_title(self, title):
        """
        Normalize book title for comparison
        Removes common subtitle variations and punctuation differences
        """
        import re
        
        normalized = title.lower()
        
        normalized = re.split(r'[:\-\(]', normalized)[0]
        
        normalized = ' '.join(normalized.split())
        
        normalized = normalized.strip(' .,;!?')
        
        return normalized
    
    def _select_best_edition(self, book_group):
        """Select the best edition from a group of duplicate books

        Preference order:
        1. Most recent release date (newer editions/remasters)
        2. Audible ASIN format (starts with 'B0', 10 chars) over ISBNs (numeric, 10-13 chars)
        3. Book with metadata (title != 'Unknown')
        4. Longest/most complete title
        """
        buyable_books = [b for b in book_group if is_buyable(b)]
        candidate_pool = buyable_books if buyable_books else book_group

        def score_book(book):
            """Score a book for selection - higher is better"""
            asin = book.get('asin', '')
            title = book.get('title', 'Unknown')
            release_date = book.get('release_date', '')
            
            score = 0

            if is_buyable(book):
                score += 500
            else:
                score -= 500
            
            if release_date:
                try:
                    year = int(release_date.split('-')[0])
                    score += year
                except (ValueError, IndexError):
                    pass
            
            if asin.startswith('B0') and len(asin) == 10:
                score += 100
            elif asin.startswith('B') and len(asin) == 10:
                score += 50
            elif asin.isdigit():
                score -= 100
            
            if title and title != 'Unknown':
                score += 20
            
            score += len(title) * 0.1
            
            if book.get('author') and book.get('author') != 'Unknown Author':
                score += 10
            if book.get('narrator') and book.get('narrator') != 'Unknown Narrator':
                score += 10
            if book.get('cover_image'):
                score += 5
            if book.get('runtime') and book.get('runtime', 0) > 0:
                score += 5
            
            return score
        
        sorted_books = sorted(candidate_pool, key=score_book, reverse=True)
        return sorted_books[0]
    
    def _check_in_library(self, book_asin):
        """Whether this ASIN is owned, using the same rule as the series detail
        page (core/database/series.py get_series_books): books.library_status
        'owned' (Audible-synced titles count even before a local file exists)
        or a local file on disk."""
        try:
            engine = self.db.connection_manager.get_engine()
            with engine.connect() as conn:
                result = conn.execute(
                    text("SELECT library_status, file_path FROM books WHERE asin = :asin LIMIT 1"),
                    {"asin": book_asin},
                ).fetchone()
            if not result:
                return False
            library_status, file_path = result
            if (library_status or '').strip().lower() == 'owned':
                return True
            return bool(file_path and str(file_path).strip())
        except Exception as e:
            self.logger.error(
                "Error checking library status",
                extra={"book_asin": book_asin, "error": str(e)},
                exc_info=True,
            )
            return False

    def calculate_series_stats(self, processed_books):
        """Calculate statistics for a series"""
        try:
            total = len(processed_books)
            owned = sum(1 for b in processed_books if b.get('in_library'))
            missing = total - owned
            
            return {
                'total_books': total,
                'owned_books': owned,
                'missing_books': missing,
                'completion_percentage': (owned / total * 100) if total > 0 else 0
            }
        except Exception as e:
            self.logger.error("Error calculating series stats", extra={"error": str(e)}, exc_info=True)
            return {
                'total_books': 0,
                'owned_books': 0,
                'missing_books': 0,
                'completion_percentage': 0
            }
