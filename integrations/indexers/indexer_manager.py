"""
Module Name: indexer_manager.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    All configured indexers: loads them from the indexer settings and runs
    searches across them in parallel, by priority.

Location:
    /integrations/indexers/indexer_manager.py
"""

import threading
from typing import Dict, Any, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from .jackett_indexer import JackettIndexer
from .prowlarr_indexer import ProwlarrIndexer
from .nzbhydra2_indexer import NZBHydra2Indexer
from .direct_indexer import DirectIndexer
from core.logging import get_module_logger


_LOGGER = get_module_logger("Service.Indexers.Manager")


class IndexerManager:
    """All configured indexers, loaded from the indexer settings."""

    def __init__(self, config_service, *, logger=None):
        self.logger = logger or _LOGGER
        self.config_service = config_service
        self.indexers = {}
        self.indexer_configs = {}
        self._reload_lock = threading.Lock()
        
        self._load_indexers()

        self.logger.debug("IndexerManager initialized with %d indexer(s)", len(self.indexers))
    
    def _load_indexers(self, target_indexers: Optional[Dict[str, Any]] = None,
                       target_configs: Optional[Dict[str, Any]] = None):
        """Load and initialize indexers from configuration.

        Populates ``target_indexers``/``target_configs`` when given (used by
        reload_indexers to build a new set off to the side), otherwise the live
        ``self.indexers``/``self.indexer_configs``.
        """
        if target_indexers is None:
            target_indexers = self.indexers
        if target_configs is None:
            target_configs = self.indexer_configs
        indexer_configs = {}

        try:
            indexer_configs = self.config_service.list_indexers_config()
            if indexer_configs:
                self.logger.debug("Loaded indexer configuration from config.txt")
        except Exception as exc:
            self.logger.error("Failed to load indexers from config service: %s", exc, exc_info=True)

        if not indexer_configs:
            indexer_configs = self._load_from_config_py()
        
        if not indexer_configs:
            self.logger.warning("No indexers configured")
            return
        
        sorted_configs = sorted(
            indexer_configs.items(),
            key=lambda x: x[1].get('priority', 999)
        )
        
        for name, config in sorted_configs:
            if not config.get('enabled', False):
                self.logger.debug("Skipping disabled indexer", extra={"indexer": name})
                continue
            
            try:
                if 'name' not in config or not config['name']:
                    config['name'] = name
                
                indexer_type = config.get('type', 'jackett').lower()
                
                if indexer_type == 'jackett':
                    indexer = JackettIndexer(config)
                elif indexer_type in ('prowlarr', 'prowlarr_torznab', 'prowlarr_newznab'):
                    indexer = ProwlarrIndexer(config)
                elif indexer_type == 'nzbhydra2':
                    indexer = NZBHydra2Indexer(config)
                elif indexer_type == 'direct':
                    indexer = DirectIndexer(config)
                else:
                    self.logger.error("Unknown indexer type", extra={"indexer": name, "indexer_type": indexer_type})
                    continue
                
                target_indexers[name] = indexer
                target_configs[name] = config
                
                self.logger.debug(
                    "Loaded indexer",
                    extra={
                        "indexer": name,
                        "priority": config.get('priority'),
                        "protocol": config.get('protocol'),
                    },
                )
                
            except Exception as e:
                self.logger.exception("Failed to load indexer '%s': %s", name, e)
                continue
    
    def _load_from_config_py(self):
        """Load indexer config from config.py"""
        self.logger.debug("Loading indexers from config.py")
        from core.config.config import Config
        return getattr(Config, 'INDEXERS', {})
    
    def search(
        self,
        query: str,
        author: Optional[str] = None,
        title: Optional[str] = None,
        limit_per_indexer: int = 100,
        parallel: bool = True
    ) -> List[Dict[str, Any]]:
        """Search all available indexers for audiobooks."""
        if not self.indexers:
            self.logger.warning("No indexers available for search")
            return []
        
        available_indexers = [
            (name, indexer) for name, indexer in self.indexers.items()
            if indexer.is_available()
        ]
        
        if not available_indexers:
            self.logger.warning("No available indexers for search")
            return []
        
        self.logger.info(
            "Searching %d indexer(s) | title='%s' author='%s' query='%s'",
            len(available_indexers),
            title or "",
            author or "",
            query,
        )

        all_results = []

        if parallel and len(available_indexers) > 1:
            all_results = self._search_parallel(
                available_indexers, query, author, title, limit_per_indexer
            )
        else:
            for name, indexer in available_indexers:
                try:
                    with indexer.rate_limiter:
                        results = indexer.search(
                            query=query,
                            author=author,
                            title=title,
                            limit=limit_per_indexer
                        )
                    self.logger.debug(
                        "Indexer returned results (sequential)",
                        extra={"indexer": name, "result_count": len(results) if results is not None else 0},
                    )
                    all_results.extend(results)
                except Exception as e:
                    self.logger.error("Error searching indexer", extra={"indexer": name, "error": str(e)}, exc_info=True)
                    continue
        
        self.logger.info("Total results from all indexers", extra={"result_count": len(all_results)})
        return all_results
    
    @staticmethod
    def _rate_limited_search(indexer, query: str, author: Optional[str], title: Optional[str], limit: int):
        """Run one indexer's search under its own concurrency/pacing guard,
        so per-indexer limits hold even though several indexers are being
        searched in parallel here."""
        with indexer.rate_limiter:
            return indexer.search(query=query, author=author, title=title, limit=limit)

    def _search_parallel(
        self,
        indexers: List[tuple],
        query: str,
        author: Optional[str],
        title: Optional[str],
        limit: int
    ) -> List[Dict[str, Any]]:
        all_results = []

        with ThreadPoolExecutor(max_workers=min(len(indexers), 5)) as executor:
            future_to_indexer = {
                executor.submit(
                    self._rate_limited_search,
                    indexer,
                    query,
                    author,
                    title,
                    limit,
                ): name
                for name, indexer in indexers
            }
            
            for future in as_completed(future_to_indexer):
                indexer_name = future_to_indexer[future]
                try:
                    results = future.result(timeout=60)
                    all_results.extend(results)
                    self.logger.debug(
                        "Indexer returned results (parallel)",
                        extra={"indexer": indexer_name, "result_count": len(results)},
                    )
                except Exception as e:
                    self.logger.error("Error in parallel search", extra={"indexer": indexer_name, "error": str(e)}, exc_info=True)
        
        return all_results
    
    def reload_indexers(self):
        """
        Reload indexers from configuration.
        Useful for applying config changes without restarting.
        Atomic: builds new collections before swapping so in-flight searches
        always see a consistent snapshot.
        """
        with self._reload_lock:
            self.logger.info("Reloading indexers from configuration...")

            new_indexers: Dict[str, Any] = {}
            new_configs: Dict[str, Any] = {}

            try:
                self._load_indexers(new_indexers, new_configs)
            except Exception as exc:
                self.logger.error(
                    "reload_indexers failed, keeping previous indexers: %s", exc
                )
                raise

            self.indexer_configs = new_configs
            self.indexers = new_indexers

            self.logger.info("Reloaded %d indexer(s)", len(self.indexers))
