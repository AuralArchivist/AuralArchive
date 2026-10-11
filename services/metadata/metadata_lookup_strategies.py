"""
Module Name: metadata_lookup_strategies.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Search strategies for finding book metadata via Audible service, including
    ASIN, title/author, title-only, author-only, and series-based queries.

Location:
    /services/metadata/metadata_lookup_strategies.py
"""

from typing import Dict, Optional, TYPE_CHECKING
import threading
from .matching import matcher
from core.logging import get_module_logger


_LOGGER = get_module_logger("Service.Metadata.LookupStrategies")

if TYPE_CHECKING:
    from .metadata_service import CancellationContext

class MetadataSearchStrategies:
    """Handles different search strategies for finding book metadata"""
    
    def __init__(self, audible_service, *, logger=None):
        self.audible_service = audible_service
        self.logger = logger or _LOGGER
        self._stats_lock = threading.Lock()
        self._stats: Dict[str, int] = {
            "asin_searches": 0,
            "title_author_searches": 0,
            "title_only_searches": 0,
            "successful_matches": 0,
            "failed_matches": 0,
        }
    
    def search_for_book_metadata(self, title: str, author: str, asin: str, 
                                cancellation_context: Optional['CancellationContext'] = None) -> Optional[Dict]:
        """Search for book metadata using multiple strategies"""
        try:
            self.logger.info(
                "Searching for metadata",
                extra={"title": title, "author": author, "asin": asin},
            )
            
            if cancellation_context:
                cancellation_context.raise_if_cancelled()
            
            if asin and asin not in ['N/A', '']:
                if cancellation_context:
                    cancellation_context.raise_if_cancelled()
                with self._stats_lock:
                    self._stats["asin_searches"] += 1
                result = self._search_by_asin_strategy(asin, cancellation_context)
                if result:
                    with self._stats_lock:
                        self._stats["successful_matches"] += 1
                    return result
            
            if title and author:
                if cancellation_context:
                    cancellation_context.raise_if_cancelled()
                with self._stats_lock:
                    self._stats["title_author_searches"] += 1
                result = self._search_by_title_author_strategy(title, author, asin, cancellation_context)
                if result:
                    with self._stats_lock:
                        self._stats["successful_matches"] += 1
                    return result
            
            if title:
                if cancellation_context:
                    cancellation_context.raise_if_cancelled()
                with self._stats_lock:
                    self._stats["title_only_searches"] += 1
                result = self._search_by_title_only_strategy(title, author, asin, cancellation_context)
                if result:
                    with self._stats_lock:
                        self._stats["successful_matches"] += 1
                    return result
            
            self.logger.warning(
                "No suitable metadata matches found",
                extra={"title": title, "author": author, "asin": asin},
            )
            with self._stats_lock:
                self._stats["failed_matches"] += 1
            return None
            
        except Exception as e:
            if "cancelled" in str(e).lower():
                raise
            self.logger.error(
                "Error searching for book metadata",
                extra={"error": str(e), "title": title, "author": author, "asin": asin},
                exc_info=True,
            )
            return None
    
    def _search_by_asin_strategy(self, asin: str, cancellation_context: Optional['CancellationContext'] = None) -> Optional[Dict]:
        try:
            self.logger.info("Strategy 1: searching by ASIN", extra={"asin": asin})
            
            if cancellation_context:
                cancellation_context.raise_if_cancelled()
            
            search_results = self.audible_service.search_books(asin, region="us", num_results=10)
            
            if cancellation_context:
                cancellation_context.raise_if_cancelled()
            
            if not search_results:
                self.logger.info("No results found for ASIN search", extra={"asin": asin})
                return None
            
            exact_match = matcher.find_exact_asin_match(search_results, asin)
            if exact_match:
                self.logger.info(
                    "Found exact ASIN match",
                    extra={"asin": asin, "title": exact_match.get('Title')},
                )
                return exact_match
            
            for result in search_results:
                result_asin = result.get('ASIN', '')
                if result_asin and result_asin == asin:
                    self.logger.info(
                        "Found ASIN match in results",
                        extra={"asin": asin, "title": result.get('Title')},
                    )
                    return result
            
            self.logger.info(
                "No exact ASIN match found",
                extra={"asin": asin, "result_count": len(search_results)},
            )
            return None
            
        except Exception as e:
            self.logger.error(
                "Error in ASIN search strategy",
                extra={"asin": asin, "error": str(e)},
                exc_info=True,
            )
            return None
    
    def _search_by_title_author_strategy(self, title: str, author: str, asin: str, 
                                        cancellation_context: Optional['CancellationContext'] = None) -> Optional[Dict]:
        try:
            search_query = f"{title} {author}"
            self.logger.info(
                "Strategy 2: searching by title and author",
                extra={"title": title, "author": author, "asin": asin},
            )
            
            if cancellation_context:
                cancellation_context.raise_if_cancelled()
            
            search_results = self.audible_service.search_books(search_query, region="us", num_results=15)
            
            if cancellation_context:
                cancellation_context.raise_if_cancelled()
            
            if not search_results:
                self.logger.info("No results found for title + author search")
                return None
            
            if asin and asin not in ['N/A', '']:
                exact_match = matcher.find_exact_asin_match(search_results, asin)
                if exact_match:
                    self.logger.info(
                        "Found exact ASIN match in title/author search",
                        extra={"asin": asin, "title": exact_match.get('Title')},
                    )
                    return exact_match
            
            best_match = matcher.find_best_match(search_results, title, author)
            if best_match:
                self.logger.info(
                    "Found best title/author match",
                    extra={"title": best_match.get('Title'), "author": best_match.get('Author')},
                )
                return best_match
            
            return None
            
        except Exception as e:
            self.logger.error(
                "Error in title + author search strategy",
                extra={"error": str(e), "title": title, "author": author, "asin": asin},
                exc_info=True,
            )
            return None
    
    def _search_by_title_only_strategy(self, title: str, author: str, asin: str, 
                                      cancellation_context: Optional['CancellationContext'] = None) -> Optional[Dict]:
        try:
            self.logger.info("Strategy 3: searching by title only", extra={"title": title, "asin": asin})
            
            if cancellation_context:
                cancellation_context.raise_if_cancelled()
            
            search_results = self.audible_service.search_books(title, region="us", num_results=10)
            
            if cancellation_context:
                cancellation_context.raise_if_cancelled()
            
            if not search_results:
                self.logger.info("No results found for title-only search", extra={"title": title})
                return None
            
            if asin and asin not in ['N/A', '']:
                exact_match = matcher.find_exact_asin_match(search_results, asin)
                if exact_match:
                    self.logger.info(
                        "Found exact ASIN match in title-only search",
                        extra={"asin": asin, "title": exact_match.get('Title')},
                    )
                    return exact_match
            
            best_match = matcher.find_best_match(search_results, title, author)
            if best_match:
                self.logger.info(
                    "Found match by title",
                    extra={"title": best_match.get('Title'), "author": best_match.get('Author')},
                )
                return best_match
            
            return None
            
        except Exception as e:
            self.logger.error(
                "Error in title-only search strategy",
                extra={"error": str(e), "title": title, "author": author, "asin": asin},
                exc_info=True,
            )
            return None
    
    def _parse_sequence(self, sequence_str: str) -> float:
        """Parse sequence string to float for sorting"""
        cleaned = str(sequence_str or '').replace('Book ', '').replace('#', '').strip()
        try:
            return float(cleaned)
        except ValueError:
            return 999.0
    
    def validate_search_strategy_config(self) -> bool:
        """Validate that search strategies can be executed"""
        try:
            if not self.audible_service:
                self.logger.error("No AudibleCatalogClient available for search strategies")
                return False
            
            test_results = self.audible_service.search_books("test", num_results=1)
            if test_results is None:
                self.logger.error("AudibleCatalogClient search test failed")
                return False
            
            self.logger.info("Search strategies validation passed")
            return True
            
        except Exception as e:
            self.logger.error(
                "Search strategies validation failed",
                extra={"error": str(e)},
                exc_info=True,
            )
            return False
    
    def get_strategy_stats(self) -> Dict[str, int]:
        """Get statistics about search strategy usage since service start."""
        with self._stats_lock:
            return dict(self._stats)

