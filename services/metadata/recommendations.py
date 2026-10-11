"""
Module Name: recommendations.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Library-based recommendations from Audible catalog searches.

Location:
    /services/metadata/recommendations.py
"""

from typing import Any, Dict, List, Tuple

from core.logging import get_module_logger


class AudibleRecommendationsService:
    """Service for managing Audible recommendations integration."""
    
    def __init__(self, config_service, audible_manager, logger=None):
        self.logger = logger or get_module_logger("Service.Audible.Recommendations")
        self.config_service = config_service
        self.audible_manager = audible_manager
        
    def is_configured(self) -> bool:
        """Check if Audible recommendations are properly configured."""
        return self.audible_manager.is_configured()
    
    def authenticate(self) -> Tuple[bool, str]:
        """Load the stored Audible auth via the manager (auth-file only; the
        interactive login lives in api/audible/auth.py)."""
        return self.audible_manager.load_saved_auth()
    
    def test_connection(self) -> Tuple[bool, str]:
        """Test the Audible connection using the manager."""
        return self.audible_manager.test_connection()
    
    def get_library_based_recommendations(self, num_results: int = 20) -> List[Dict[str, Any]]:
        """Recommendations derived from the user's own library (authors, series,
        genres) via public catalog searches. Public entry point for the Discover
        page, which uses this instead of the Audible recommendations API."""
        return self._get_catalog_fallback(num_results)

    _RESPONSE_GROUPS = "contributors,product_attrs,product_desc,product_extended_attrs,series,rating,media"

    def _get_catalog_fallback(self, num_results: int) -> List[Dict[str, Any]]:
        """Get personalized recommendations based on user's library preferences."""
        try:
            preferences = self._analyze_library_preferences()
            queries = (
                [dict(num_results=5, author=a, products_sort_by="Relevance")
                 for a in preferences.get('top_authors', [])[:5]]
                + [dict(num_results=3, keywords=f'"{s}"', products_sort_by="Relevance")
                   for s in preferences.get('active_series', [])[:3]]
                + [dict(num_results=8, category_id=c['id'], products_sort_by="BestSellers")
                   for c in preferences.get('top_categories', [])[:4]]
            )

            all_books, failures = [], 0
            for params in queries:
                try:
                    response = self.audible_manager.make_api_call(
                        "1.0/catalog/products", response_groups=self._RESPONSE_GROUPS, **params
                    )
                except Exception:
                    failures += 1
                    self.logger.debug("Recommendation search failed", extra={"params": params}, exc_info=True)
                    continue
                if response:
                    all_books.extend(self._format_book_data(book) for book in response.get("products", []))
            if queries and failures == len(queries):
                self.logger.warning("All %s recommendation searches failed", failures)

            owned_books = preferences.get('owned_books', set())
            owned_titles = [t.lower() for t in preferences.get('owned_titles', [])]
            seen_asins = set()
            unique_books = []

            def add_unique(books):
                for book in books:
                    if len(unique_books) >= num_results:
                        return
                    asin = book.get('asin')
                    title = (book.get('title') or '').lower()
                    if asin in owned_books or asin in seen_asins:
                        continue
                    if any(title in owned for owned in owned_titles):
                        continue
                    seen_asins.add(asin)
                    unique_books.append(book)

            add_unique(all_books)
            if len(unique_books) < num_results:
                add_unique(self._get_generic_fallback(num_results - len(unique_books)))

            self.logger.info(
                "Library-based recommendations computed",
                extra={"unique_books": len(unique_books), "requested": num_results},
            )
            return unique_books

        except Exception as e:
            self.logger.error(
                "Library-based recommendations failed",
                extra={"error": str(e)},
                exc_info=True,
            )
            return self._get_generic_fallback(num_results)

    def _analyze_library_preferences(self) -> Dict[str, Any]:
        """Analyze user's library to understand reading preferences."""
        try:
            from collections import Counter
            from sqlalchemy import text
            from services.container import container

            database_service = container.get_database_service()
            if not database_service:
                self.logger.warning("Database service unavailable, using generic recommendations")
                return {}

            with database_service.connection_manager.get_engine().connect() as conn:
                books = [tuple(row) for row in conn.execute(text("""
                    SELECT title, author, series, asin
                    FROM books
                    WHERE author IS NOT NULL AND author != ''
                """)).fetchall()]

            if not books:
                self.logger.warning("No books found in library")
                return {}
            
            authors = Counter()
            series = Counter()
            owned_books = set()
            owned_titles = []
            
            for title, author, series_name, asin in books:
                if author:
                    author_list = [a.strip() for a in author.split(',')]
                    for a in author_list:
                        if a and a != 'Unknown Author':
                            authors[a] += 1
                
                if series_name and series_name.strip() and series_name.strip().upper() != 'N/A':
                    series[series_name.strip()] += 1
                
                if asin:
                    owned_books.add(asin)
                if title:
                    owned_titles.append(title)
            
            top_authors = [author for author, count in authors.most_common(10)]
            active_series = [s for s, count in series.most_common(8) if count >= 2]
            
            top_categories = database_service.categories.get_library_top_categories(limit=4)

            preferences = {
                'top_authors': top_authors,
                'active_series': active_series,
                'top_categories': top_categories,
                'owned_books': owned_books,
                'owned_titles': owned_titles,
                'total_books': len(books),
            }
            
            self.logger.info(
                "Library analysis completed",
                extra={
                    "authors": len(top_authors),
                    "series": len(active_series),
                    "categories": len(top_categories),
                    "total_books": len(books),
                },
            )
            return preferences
            
        except Exception as e:
            self.logger.error(
                "Library analysis failed",
                extra={"error": str(e)},
                exc_info=True,
            )
            return {}
    
    def _get_generic_fallback(self, num_results: int) -> List[Dict[str, Any]]:
        """Get generic recommendations as last resort."""
        try:
            search_terms = ["bestseller", "popular", "award winning", "fiction", "mystery"]
            all_books = []
            
            for term in search_terms:
                try:
                    response = self.audible_manager.make_api_call(
                        "1.0/catalog/products",
                        num_results=min(10, num_results // len(search_terms) + 5),
                        response_groups="contributors,media,price,product_attrs,product_desc,rating",
                        keywords=term,
                        products_sort_by="Relevance"
                    )
                    
                    if response:
                        books = response.get("products", [])
                        all_books.extend([self._format_book_data(book) for book in books])
                        
                        if len(all_books) >= num_results:
                            break
                            
                except Exception as e:
                    self.logger.debug(
                        "Search term failed",
                        extra={"term": term, "error": str(e)},
                    )
                    continue
            
            seen_asins = set()
            unique_books = []
            for book in all_books:
                asin = book.get('asin')
                if asin and asin not in seen_asins:
                    seen_asins.add(asin)
                    unique_books.append(book)
                    if len(unique_books) >= num_results:
                        break
            
            return unique_books
            
        except Exception as e:
            self.logger.error(
                "Generic fallback failed",
                extra={"error": str(e)},
                exc_info=True,
            )
            return []
    
    def _format_book_data(self, book: Dict[str, Any]) -> Dict[str, Any]:
        """Format book data using the shared manager."""
        formatted = self.audible_manager.format_book_data(book)
        formatted['source'] = 'audible_recommendations'
        return formatted
