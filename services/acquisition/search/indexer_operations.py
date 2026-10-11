"""
Module Name: indexer_operations.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Manage indexer connections, distribute searches, and coordinate health
    monitoring for the search engine. Provides configuration reload, failover,
    and retry logic for indexer operations.

Location:
    /services/acquisition/search/indexer_operations.py
"""

from typing import Dict, List, Any
import time

from core.logging import get_module_logger


_LOGGER = get_module_logger("Service.SearchEngine.IndexerOperations")


class IndexerOperations:
    """Handles indexer management for the SearchEngineService."""
    
    def __init__(self, *, logger=None):
        self.logger = logger or _LOGGER
        
        self.indexer_manager = None
        self._init_indexer_manager()
        
    
    def _init_indexer_manager(self):
        """Initialize connection to IndexerManager."""
        try:
            from services.container import container
            self.indexer_manager = container.get_indexer_manager()
            self.logger.debug(
                "Connected to IndexerManager",
                extra={"indexer_count": len(self.indexer_manager.indexers)},
            )
        except Exception as e:
            self.logger.error(
                "Failed to initialize IndexerManager",
                extra={"error": str(e)},
                exc_info=True,
            )
            self.indexer_manager = None
    
    def refresh_indexers(self) -> bool:
        """Refresh indexer list and perform health check."""
        try:
            self.logger.info("Refreshing indexer list")
            
            if not self.indexer_manager:
                self.logger.warning("IndexerManager not available, reinitializing")
                self._init_indexer_manager()
                return self.indexer_manager is not None
            
            self.indexer_manager.reload_indexers()
            
            self.logger.info(
                "Indexer refresh complete",
                extra={"indexer_count": len(self.indexer_manager.indexers)},
            )
            return True
            
        except Exception as e:
            self.logger.error(
                "Failed to refresh indexers",
                extra={"error": str(e)},
                exc_info=True,
            )
            return False
    
    def test_indexer_search(self, indexer_id: str, title: str = "Anima", 
                           author: str = "Blake Crouch") -> Dict[str, Any]:
        """Test search functionality for a specific indexer."""
        try:
            if not self.indexer_manager:
                return {
                    'success': False,
                    'error': 'IndexerManager not available'
                }
            
            if indexer_id not in self.indexer_manager.indexers:
                return {
                    'success': False,
                    'error': f'Indexer {indexer_id} not found'
                }
            
            indexer = self.indexer_manager.indexers[indexer_id]
            
            self.logger.info("Testing search for indexer", extra={"indexer_id": indexer_id, "indexer_name": indexer.name})
            
            start_time = time.time()
            
            results = indexer.search(
                query=f"{title} {author}",
                title=title,
                author=author,
                limit=10
            )
            
            search_time = time.time() - start_time
            
            return {
                'success': True,
                'indexer_name': indexer.name,
                'search_time': round(search_time, 2),
                'result_count': len(results),
                'sample_results': results[:3],
                'test_query': f"{title} by {author}"
            }
            
        except Exception as e:
            self.logger.error(
                "Indexer test failed",
                extra={"indexer_id": indexer_id, "error": str(e)},
                exc_info=True,
            )
            return {
                'success': False,
                'error': str(e)
            }
    
    def search_all_indexers(self, title: str, author: str,
                           manual_search: bool = False) -> List[Dict[str, Any]]:
        """Search all active indexers for audiobook results."""
        try:
            if not self.indexer_manager:
                self.logger.error("IndexerManager not available for search")
                return []

            indexer_count = len(self.indexer_manager.indexers)
            self.logger.info(
                "Searching indexers",
                extra={"indexer_count": indexer_count, "title": title, "author": author},
            )

            results = self.indexer_manager.search(
                query=f"{title} {author}".strip(),
                author=author,
                title=title,
                limit_per_indexer=50,
                parallel=True,
            )
            
            self.logger.info(
                "Indexer search complete",
                extra={"result_count": len(results)},
            )
            return results
            
        except Exception as e:
            self.logger.error(
                "Failed to search indexers",
                extra={"error": str(e)},
                exc_info=True,
            )
            return []
    
    def shutdown(self):
        """Shutdown indexer operations."""
        try:
            self.logger.debug("IndexerOperations shutdown complete")
        except Exception as e:
            self.logger.error(
                "Error during IndexerOperations shutdown",
                extra={"error": str(e)},
                exc_info=True,
            )