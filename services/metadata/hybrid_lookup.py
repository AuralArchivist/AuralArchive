"""
Module Name: hybrid_lookup.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Combine Audnexus author-centric data with Audible catalog/search capabilities.

Location:
    /services/metadata/hybrid_lookup.py
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Optional, Any, Tuple

from core.logging import get_module_logger


class HybridAudiobookService:
    """
    Hybrid service that combines Audnexus and Audible APIs
    - Uses Audnexus for author-centric operations (better data, no rate limits)
    - Uses Audible API for general book search and discovery
    - Falls back to the other source when one fails
    """
    
    def __init__(self, audnexus, audible, logger=None):
        self.logger = logger or get_module_logger("Service.Audnexus.Hybrid")

        self.audnexus = audnexus
        self.audible = audible

        self.logger.success("Hybrid audiobook service started successfully")
    
    # --- Author operations (primary: Audnexus, fallback: Audible) ---
    
    def search_author_page(self, author_name: str, region: str = "us") -> Optional[Dict]:
        """
        Search for author information with Audnexus first, fallback to Audible.
        """
        try:
            self.logger.info("Searching for author", extra={"author_name": author_name, "region": region})

            author_data = self.audnexus.find_author_by_name(author_name, region)

            if author_data:
                formatted_author = self.audnexus.format_author_for_compatibility(author_data)

                formatted_author['source'] = 'audnexus'
                formatted_author['audible_author_id'] = author_data.get('asin', '')
                formatted_author['total_books_count'] = 0
                formatted_author['audible_books_count'] = 0

                self.logger.info("Found author via Audnexus", extra={"author_name": author_name})
                return formatted_author

            self.logger.info("Audnexus failed, trying Audible", extra={"author_name": author_name})
            audible_data = self.audible.search_author_page(author_name)

            if audible_data:
                audible_data['source'] = 'audible'
                self.logger.info("Found author via Audible", extra={"author_name": author_name})
                return audible_data

            self.logger.warning("Author not found in either service", extra={"author_name": author_name})
            return None

        except Exception as exc:
            self.logger.error(
                "Error searching for author",
                extra={"author_name": author_name, "exc": exc}, exc_info=True
            )
            return None
    
    def get_author_books_from_audible(
        self,
        author_name: str,
        region: str = "us",
        enrich_with_audnexus: bool = False,
        limit: Optional[int] = None,
    ) -> List[Dict]:
        """Get books by author using Audible as the primary data source"""
        try:
            self.logger.info("Getting books for author", extra={"author_name": author_name, "region": region, "limit": limit, "enrich_with_audnexus": enrich_with_audnexus})
            
            audible_books = self.audible.get_author_books_from_audible(
                author_name,
                region=region,
                limit=limit,
            )

            if limit is not None:
                audible_books = audible_books[:limit]
            
            books = []
            
            for book in audible_books:
                if not enrich_with_audnexus or not book.get('ASIN'):
                    book.setdefault('enhanced_by', 'audible_only')
                    books.append(book)
                    continue

                try:
                    enhanced_book = self.audnexus.get_book_details(book['ASIN'], region)
                except Exception as enhancement_error:
                    self.logger.debug(
                        "Audnexus enhancement failed",
                        extra={"book_asin": book.get('ASIN'), "exc": enhancement_error}
                    )
                    enhanced_book = None
                
                if enhanced_book:
                    audnexus_formatted = self.audnexus.format_book_for_compatibility(enhanced_book)
                    book.update({k: v for k, v in audnexus_formatted.items() if v and v != 'N/A'})
                    book['enhanced_by'] = 'audnexus'
                else:
                    book['enhanced_by'] = 'audible_only'
                
                books.append(book)
            
            if not books and enrich_with_audnexus:
                self.logger.info("Audible returned no books; attempting Audnexus fallback", extra={"author_name": author_name})
                author_details = self.audnexus.find_author_by_name(author_name, region)
                titles = author_details.get('titles', []) if author_details else []
                for title in titles:
                    formatted = self.audnexus.format_book_for_compatibility(title)
                    formatted['enhanced_by'] = 'audnexus_only'
                    books.append(formatted)
                if books:
                    self.logger.info("Audnexus fallback supplied titles", extra={"author_name": author_name, "count": len(books)})
            
            self.logger.info("Retrieved books for author", extra={"author_name": author_name, "count": len(books)})
            return books
            
        except Exception as exc:
            self.logger.error(
                "Error getting books for author",
                extra={"author_name": author_name, "exc": exc}, exc_info=True
            )
            return []

    def fetch_author_catalog(
        self,
        author_name: str,
        region: str = "us",
        limit: Optional[int] = None,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Fetch raw and formatted catalog data directly from Audible."""

        return self.audible.fetch_author_catalog(author_name, region=region, limit=limit)
    
    # --- Book operations (primary: Audible, enhancement: Audnexus) ---
    
    def search_books(self, query: str, region: str = "us", num_results: int = 25) -> List[Dict]:
        """Search for books using Audible API with Audnexus enhancement"""
        try:
            self.logger.info("Searching books", extra={"query": query, "region": region, "num_results": num_results})
            
            books = self.audible.search_books(query, region, num_results)

            def _enrich(book):
                if not book.get('ASIN'):
                    book['enhanced_by'] = 'audible_only'
                    return book
                audnexus_book = self.audnexus.get_book_details(book['ASIN'], region)
                if audnexus_book:
                    audnexus_formatted = self.audnexus.format_book_for_compatibility(audnexus_book)
                    book.update({k: v for k, v in audnexus_formatted.items() if v and v != 'N/A'})
                    book['enhanced_by'] = 'audnexus'
                else:
                    book['enhanced_by'] = 'audible_only'
                return book

            enhanced_books = []
            with ThreadPoolExecutor(max_workers=5, thread_name_prefix="audnexus_enrich") as pool:
                futures = {pool.submit(_enrich, book): book for book in books}
                for future in as_completed(futures):
                    try:
                        enhanced_books.append(future.result())
                    except Exception as enrich_err:
                        self.logger.warning("Audnexus enrichment error: %s", enrich_err)
                        enhanced_books.append(futures[future])
            
            self.logger.info("Enhanced books from search", extra={"query": query, "count": len(enhanced_books)})
            return enhanced_books
            
        except Exception as exc:
            self.logger.error(
                "Error searching books",
                extra={"query": query, "exc": exc}, exc_info=True
            )
            return []
    
    # --- Service management ---
    
    def test_connection(self) -> tuple[bool, str]:
        """Test connection to both services"""
        try:
            audnexus_ok, audnexus_msg = self.audnexus.test_connection()
            audible_ok, audible_msg = self.audible.test_connection()
            
            if audnexus_ok and audible_ok:
                return True, f"Both services OK - Audnexus: {audnexus_msg}, Audible: {audible_msg}"
            elif audnexus_ok:
                return True, f"Audnexus OK (primary), Audible issues: {audible_msg}"
            elif audible_ok:
                return True, f"Audible OK (fallback), Audnexus issues: {audnexus_msg}"
            else:
                return False, f"Both services failed - Audnexus: {audnexus_msg}, Audible: {audible_msg}"
                
        except Exception as exc:
            return False, f"Service test error: {exc}"
