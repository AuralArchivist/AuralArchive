"""
Module Name: search_engine_service.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Coordinates audiobook search with fuzzy matching, quality assessment, and
    multi-indexer integration.

Location:
    /services/acquisition/search/search_engine_service.py
"""

from typing import Any, Dict, List

from core.logging import get_module_logger

from .search_operations import SearchOperations
from .indexer_operations import IndexerOperations
from .fuzzy_matcher import FuzzyMatcher
from .quality_assessor import QualityAssessor
from .result_processor import ResultProcessor


_LOGGER = get_module_logger("Service.SearchEngine.Service")


class SearchEngineService:
    """Main search engine service following DatabaseService singleton pattern."""
    
    def __init__(self, *, logger=None):
        self.logger = logger or _LOGGER

        self.search_operations = None
        self.indexer_operations = None
        self.fuzzy_matcher = None
        self.quality_assessor = None
        self.result_processor = None

        self.max_search_results = 50
        self.search_timeout = 60

        self.test_audiobooks = [
            {"title": "Anima", "author": "Blake Crouch"},
            {"title": "The Primal Hunter", "author": "Zogarth"}
        ]

        self._initialize_service()
    
    def _initialize_service(self):
        """Initialize the search engine service components."""
        try:
            self.logger.debug("Initializing SearchEngineService")
            
            self.fuzzy_matcher = FuzzyMatcher()
            self.quality_assessor = QualityAssessor()
            self.result_processor = ResultProcessor()
            
            self.indexer_operations = IndexerOperations()
            
            self.search_operations = SearchOperations(
                self.fuzzy_matcher, 
                self.quality_assessor, 
                self.result_processor
            )
            
            self.search_operations.indexer_operations = self.indexer_operations
            
            
            indexer_count = 0
            if self.indexer_operations and self.indexer_operations.indexer_manager:
                indexer_count = len(self.indexer_operations.indexer_manager.indexers)
            self.logger.success("Search engine started successfully", extra={"indexers": indexer_count})
            
        except Exception as e:
            self.logger.error(
                "Failed to initialize SearchEngineService",
                extra={"error": str(e)},
                exc_info=True,
            )
            raise
    
    def search_for_audiobook(self, title: str, author: str,
                           manual_search: bool = True, query_components=None,
                           series: str = None, sequence: str = None) -> Dict[str, Any]:
        """Search for audiobook across all indexers."""
        return self.search_operations.search_for_audiobook(
            title, author, manual_search, query_components, series=series, sequence=sequence
        )
    
    def search_all_indexers(self, title: str, author: str,
                           manual_search: bool = False) -> List[Dict[str, Any]]:
        """Search all active indexers for audiobook results."""
        return self.indexer_operations.search_all_indexers(title, author, manual_search)
    
    def process_manual_search_results(self, raw_results: List[Dict[str, Any]], 
                                    title: str, author: str) -> List[Dict[str, Any]]:
        """Process search results for manual user selection."""
        return self.result_processor.process_manual_search_results(raw_results, title, author)
    
    def get_processing_stats(self, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Get statistics about processed results."""
        return self.result_processor.get_processing_stats(results)
    
    def shutdown(self):
        """Shutdown the search engine service."""
        try:
            if self.indexer_operations:
                self.indexer_operations.shutdown()
            
            self.logger.info("SearchEngineService shutdown complete")
            
        except Exception as e:
            self.logger.error("Error during SearchEngineService shutdown", extra={"error": str(e)}, exc_info=True)