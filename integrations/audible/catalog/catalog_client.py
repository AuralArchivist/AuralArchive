"""
Module Name: catalog_client.py
Author: AuralArchivist
Created: August 14, 2025
Last Modified: October 9, 2026
Description:
    Search, fetch, and format Audible catalog data with shared helpers.

Location:
    /integrations/audible/catalog/catalog_client.py
"""

from typing import Any, Dict, List, Optional, Tuple

from core.logging import get_module_logger
from .catalog_search import AudibleSearch
from .formatting import AudibleFormatter
from .cover_utils import CoverImageUtils
from .error_handling import AudibleErrorHandler
from .author_scraper import AudibleAuthorScraper
from integrations.audible.availability import is_buyable

class AudibleCatalogClient:
    """The Audible catalog: search, fetch and format catalog data."""
    
    def __init__(self):
        self.logger = get_module_logger("Service.Audible.Catalog")

        self.search = AudibleSearch()
        self.formatter = AudibleFormatter()
        self.cover_utils = CoverImageUtils()
        self.error_handler = AudibleErrorHandler()
        self.author_scraper = AudibleAuthorScraper()

        self.logger.info("AudibleCatalogClient initialized", extra={"instance_id": id(self)})
    
    def search_books(self, query: str, region: str = "us", num_results: int = 25) -> List[Dict]:
        """Search for books on Audible and return formatted results"""
        try:
            self.logger.info("Starting book search", extra={"query": query, "region": region, "requested": num_results})
            
            raw_results = self.search.search_books(query, region, num_results)
            
            if not raw_results:
                self.logger.warning("No results found for query", extra={"query": query, "region": region})
                return []
            
            processed_books = self.formatter.process_search_results(raw_results, region)
            
            self.logger.info("Processed search results", extra={"query": query, "region": region, "count": len(processed_books)})
            return processed_books
            
        except Exception as e:
            self.logger.error("Error in search_books", extra={"query": query, "region": region, "error": str(e)}, exc_info=True)
            return []
    
    def test_connection(self) -> tuple[bool, str]:
        """Test connection to Audible API"""
        try:
            self.logger.info("Testing Audible API connection")
            
            params = self.search._build_search_params(query="test", num_results=1, region="us")
            response = self.search.session.get(self.search.base_url, params=params, timeout=15)
            if response.status_code != 200:
                self.logger.error("Audible API connection test failed", extra={"status_code": response.status_code})
                return False, f"API connection failed (HTTP {response.status_code})"

            products = response.json().get("products")
            if not isinstance(products, list):
                self.logger.error("Audible API connection test returned an unexpected payload")
                return False, "API connection failed (unexpected response)"

            self.logger.info("Audible API connection test successful", extra={"results": len(products)})
            return True, f"API accessible - test returned {len(products)} results"

        except Exception as e:
            self.logger.error("Audible API connection test error", extra={"error": str(e)}, exc_info=True)
            return False, f"Connection error: {str(e)}"
    
    def search_author_page(self, author_name: str) -> Optional[Dict]:
        """Search for an author's page on Audible and return author information"""
        try:
            self.logger.info(f"Searching for author: {author_name}")
            return self.author_scraper.search_author_page(author_name)
        
        except Exception as e:
            self.logger.error(f"Error searching for author {author_name}: {e}", exc_info=True)
            return None
    
    def fetch_author_catalog(
        self,
        author_name: str,
        region: str = "us",
        limit: Optional[int] = None,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Fetch raw and formatted catalog data for an author without persistence."""

        self.logger.info(f"Fetching author catalog for: {author_name}")

        requested_results = limit if limit is not None else 200
        if requested_results <= 0:
            requested_results = 50

        raw_results: List[Dict[str, Any]] = []
        formatted_books: List[Dict[str, Any]] = []

        try:
            raw_results = self.search.search_by_author(
                author_name,
                region=region,
                num_results=requested_results,
                response_groups="contributors,product_attrs,product_desc,product_extended_attrs,series,rating,media,relationships,customer_rights,category_ladders",
            )

            if raw_results:
                raw_results = self._filter_buyable_products(raw_results)

                if not raw_results:
                    self.logger.info(f"All catalog entries for {author_name} were filtered due to customer rights restrictions")
                    return [], []

                formatted_books = self.formatter.process_search_results(raw_results, region)

                if limit is not None:
                    formatted_books = formatted_books[:limit]
                    allowed_asins = {
                        book.get('ASIN')
                        for book in formatted_books
                        if book.get('ASIN')
                    }
                    if allowed_asins:
                        raw_results = [
                            product
                            for product in raw_results
                            if product.get('asin') in allowed_asins
                        ]

                return raw_results, formatted_books

            self.logger.info(
                f"Author search returned no results for {author_name}, attempting scraper fallback"
            )

            author_data = self.search_author_page(author_name)
            if author_data and 'books' in author_data:
                books = author_data.get('books', [])
                for book in books:
                    formatted_books.append({
                        'Title': book.get('title', ''),
                        'Author': author_name,
                        'Series': 'N/A',
                        'Sequence': 'N/A',
                        'ASIN': book.get('asin', ''),
                        'Cover Image': book.get('cover_image', ''),
                        'Runtime': book.get('runtime', ''),
                        'Overall Rating': book.get('rating', ''),
                        'Summary': '',
                        'Status': 'Available on Audible'
                    })

                if limit is not None:
                    formatted_books = formatted_books[:limit]

                return [], formatted_books

            self.logger.info(
                f"No author page found, falling back to keyword search for: {author_name}"
            )

            search_query = f"author:{author_name}"
            keyword_results = self.search_books(search_query, num_results=requested_results)

            if limit is not None:
                keyword_results = keyword_results[:limit]

            return [], keyword_results

        except Exception as exc:
            self.logger.error("Error fetching catalog for author", extra={"author": author_name, "error": str(exc)}, exc_info=True)
            return [], []

    def _filter_buyable_products(self, products: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Remove products that are no longer purchasable on Audible."""
        filtered = []
        for product in products or []:
            if is_buyable(product):
                filtered.append(product)
            else:
                asin = product.get('asin')
                title = product.get('title') or product.get('product_title')
                self.logger.debug("Skipping unbuyable catalog entry", extra={"title": title, "asin": asin})
        return filtered

    def get_author_books_from_audible(
        self,
        author_name: str,
        region: str = "us",
        limit: Optional[int] = None,
    ) -> List[Dict]:
        """Get all books by an author from Audible using the catalog API."""
        try:
            raw_books, formatted_books = self.fetch_author_catalog(
                author_name,
                region=region,
                limit=limit
            )

            if limit is not None and formatted_books:
                formatted_books = formatted_books[:limit]

            self.logger.info("Returning author books", extra={"author": author_name, "count": len(formatted_books)})
            return formatted_books

        except Exception as e:
            self.logger.error("Error getting books for author", extra={"author": author_name, "error": str(e)}, exc_info=True)
            return []
