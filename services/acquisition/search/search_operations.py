"""
Module Name: search_operations.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Core search execution, history tracking, and result coordination for the
    search engine service.

Location:
    /services/acquisition/search/search_operations.py
"""

import time
from datetime import datetime
from typing import Any, Dict, List

from core.logging import get_module_logger
from core.text import normalize_search_terms
from .query_variants import (
    clean_series_name,
    build_series_book_queries,
    derive_series_queries_from_title,
)
from .result_identity import result_key


_LOGGER = get_module_logger("Service.SearchEngine.SearchOperations")


class SearchOperations:
    """Handles core search operations for the SearchEngineService."""
    
    def __init__(self, fuzzy_matcher, quality_assessor, result_processor, *, logger=None):
        self.logger = logger or _LOGGER
        self.fuzzy_matcher = fuzzy_matcher
        self.quality_assessor = quality_assessor
        self.result_processor = result_processor
        
        self.indexer_operations = None
        
        self.max_concurrent_searches = 5

    def search_for_audiobook(self, title: str, author: str,
                           manual_search: bool = True,
                           query_components=None,
                           series: str = None,
                           sequence: str = None) -> Dict[str, Any]:
        """Search for audiobook across all indexers."""
        try:
            start_time = time.time()
            original_title = title
            original_author = author
            normalized_query, normalized_title, normalized_author = normalize_search_terms(
                title,
                title,
                author,
            )
            search_title = normalized_title or original_title
            search_author = normalized_author or original_author

            self.logger.info(
                "Starting search",
                extra={
                    "search_type": "manual" if manual_search else "automatic",
                    "title": original_title,
                    "author": original_author,
                    "normalized_title": search_title,
                    "normalized_author": search_author,
                },
            )

            queries: List[str] = []
            seen_queries: set = set()

            def _add_query(candidate):
                text = (candidate or "").strip()
                if not text or text.lower() in seen_queries:
                    return
                seen_queries.add(text.lower())
                queries.append(text)

            title_seed = search_title or normalized_query or original_title

            series_titles = build_series_book_queries(series, sequence)
            if not series_titles:
                series_titles = (
                    derive_series_queries_from_title(title_seed)
                    or derive_series_queries_from_title(original_title)
                )
            for variant in series_titles:
                _add_query(variant)

            clean_series = clean_series_name(series) if series else ""
            if clean_series:
                _add_query(clean_series)

            _add_query(title_seed)
            _add_query(normalized_query)
            _add_query(original_title)

            if not queries and search_author:
                _add_query(search_author)

            if queries:
                self.logger.info(
                    "Search variants prepared",
                    extra={"variant_count": len(queries), "variants": queries},
                )
            else:
                self.logger.info(
                    "No title provided; using empty search query",
                    extra={"title": title, "author": author},
                )

            raw_results: List[Dict[str, Any]] = []
            seen_keys: set = set()

            if not self.indexer_operations:
                self.logger.warning(
                    "Indexer operations unavailable",
                    extra={"variant_count": len(queries)},
                )
            else:
                for idx, query_title in enumerate(queries or ['']):
                    if not query_title:
                        continue
                    self.logger.info(
                        "Running search variant",
                        extra={
                            "variant_index": idx + 1,
                            "variant_total": len(queries),
                            "query": query_title,
                        },
                    )
                    variant_results = self.indexer_operations.search_all_indexers(
                        query_title,
                        search_author,
                        manual_search
                    )
                    for result in variant_results:
                        key = result_key(result)
                        if key in seen_keys:
                            continue
                        seen_keys.add(key)
                        result_copy = result.copy()
                        result_copy.setdefault('_search_query_used', query_title)
                        raw_results.append(result_copy)
            
            if raw_results:
                self.logger.info(
                    "Assessing quality for search results",
                    extra={"result_count": len(raw_results), "title": title, "author": author},
                )
                scored_results = self.quality_assessor.rank_results_by_quality(
                    raw_results,
                    search_title=title,
                    search_author=author,
                    query_components=query_components,
                )
                self.logger.info(
                    "Quality assessment complete",
                    extra={"scored_count": len(scored_results)},
                )
            else:
                scored_results = []
            
            if manual_search:
                processed_results = self.result_processor.process_manual_search_results(
                    scored_results, title, author
                )
            else:
                book_info = {'id': 1, 'title': title, 'author': author}
                best_result = self.result_processor.process_automatic_search_results(
                    scored_results, book_info
                )
                processed_results = [best_result] if best_result else []
            
            search_time = time.time() - start_time
            
            return {
                'success': True,
                'search_type': 'manual' if manual_search else 'automatic',
                'query': {'title': title, 'author': author},
                'results': processed_results,
                'result_count': len(processed_results),
                'search_time': round(search_time, 2),
                'indexers_searched': len(self.indexer_operations.indexer_manager.indexers) if self.indexer_operations and self.indexer_operations.indexer_manager else 0,
                'timestamp': datetime.now().isoformat()
            }
            
        except Exception as e:
            self.logger.error(
                "Search failed",
                extra={"title": title, "author": author, "error": str(e)},
                exc_info=True,
            )
            return {
                'success': False,
                'error': str(e),
                'query': {'title': title, 'author': author},
                'timestamp': datetime.now().isoformat()
            }
    