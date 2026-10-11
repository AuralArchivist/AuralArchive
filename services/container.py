"""
Module Name: container.py
Author: AuralArchivist
Created: October 9, 2026
Last Modified: October 9, 2026
Description:
    The composition root: creates each shared service once, on first use,
    and passes in the services it depends on. Use the module's `container`.

Location:
    /services/container.py
"""

import threading
from typing import Any, Callable, Dict, Optional

from core.logging import get_module_logger

_LOGGER = get_module_logger("Service.Container")


class Container:
    """One instance of each shared service, created on first use."""

    def __init__(self, *, logger=None):
        self.logger = logger or _LOGGER
        self._services: Dict[str, Any] = {}
        # Re-entrant: a service's factory asks for the services it needs
        self._lock = threading.RLock()

    def _get(self, name: str, build: Callable[[], Any], *, optional: bool = False) -> Optional[Any]:
        """The named service, built on first use. An optional service that
        fails to build is logged and returned as None (and retried next time)."""
        service = self._services.get(name)
        if service is not None:
            return service
        with self._lock:
            if name not in self._services:
                try:
                    self._services[name] = build()
                except Exception as exc:
                    if not optional:
                        raise
                    self.logger.exception("Service initialization failed",
                                          extra={"service": name, "error": str(exc)})
                    return None
                self.logger.success("Service initialized", extra={"service": name})
            return self._services[name]

    def created(self, name: str) -> Optional[Any]:
        """The named service if it has been created, without creating it."""
        return self._services.get(name)

    def get_service_status(self) -> Dict[str, bool]:
        """Whether each service has been created yet."""
        with self._lock:
            return {name: name in self._services for name in sorted(_SERVICE_NAMES)}

    # --- Core ---

    def get_database_service(self):
        from core.database import DatabaseService
        return self._get('database', DatabaseService)

    def get_engine(self):
        """The shared SQLAlchemy engine of the database service."""
        return self.get_database_service().connection_manager.get_engine()

    def get_config_service(self):
        from services.settings import ConfigService
        return self._get('config', ConfigService)

    def get_status_service(self):
        from services.notifications.status_feed import StatusService
        return self._get('status', StatusService, optional=True)

    # --- Library ---

    def get_library_repository(self):
        from services.library.repository import LibraryRepository
        return self._get('library_repository', LibraryRepository)

    def get_library_router(self):
        from services.library.router import LibraryRouter
        return self._get('library_router', LibraryRouter)

    def get_library_check_service(self):
        from services.library.library_check import LibraryCheckService
        return self._get('library_check', LibraryCheckService)

    def get_library_import_manager(self):
        from services.library.library_import import LibraryImportManager
        return self._get('library_import', LibraryImportManager)

    # --- Background work ---

    def get_metadata_refresh_scheduler(self):
        from services.metadata.refresh.scheduler import MetadataRefreshScheduler
        return self._get('metadata_refresh', MetadataRefreshScheduler)

    def get_audiobookshelf_runner(self):
        from services.sync.audiobookshelf.runner import AbsRunner
        return self._get('audiobookshelf_runner', AbsRunner)

    # --- Audible ---

    def get_audible_catalog_client(self):
        from integrations.audible.catalog.catalog_client import AudibleCatalogClient
        return self._get('audible_catalog', AudibleCatalogClient)

    def get_audible_account(self):
        from integrations.audible.account import AudibleAccount
        return self._get('audible_account',
                         lambda: AudibleAccount(config_service=self.get_config_service()))

    def get_audible_wishlist_service(self):
        from services.sync.audible_wishlist import AudibleWishlistService
        return self._get('audible_wishlist', lambda: AudibleWishlistService(
            config_service=self.get_config_service(),
            audible_manager=self.get_audible_account(),
            sync_interval_minutes=15,
        ))

    def get_audible_recommendations_service(self):
        from services.metadata.recommendations import AudibleRecommendationsService
        return self._get('audible_recommendations', lambda: AudibleRecommendationsService(
            config_service=self.get_config_service(),
            audible_manager=self.get_audible_account(),
        ))

    def get_audible_metadata_sync_service(self):
        from services.metadata.audible_sync import AudibleMetadataSyncService
        return self._get('audible_metadata_sync', AudibleMetadataSyncService)

    def get_audible_series_service(self):
        from services.metadata.series.audible_series_service import AudibleSeriesService
        return self._get('audible_series', AudibleSeriesService)

    # --- Metadata ---

    def get_metadata_update_service(self):
        from services.metadata import MetadataUpdateService
        return self._get('metadata_update', MetadataUpdateService)

    def get_audnexus_client(self):
        from integrations.audnexus import AudnexusClient
        return self._get('audnexus', AudnexusClient, optional=True)

    def get_hybrid_audiobook_service(self):
        from services.metadata.hybrid_lookup import HybridAudiobookService
        return self._get('hybrid_audiobook', lambda: HybridAudiobookService(
            audnexus=self.get_audnexus_client(),
            audible=self.get_audible_catalog_client(),
        ), optional=True)

    def get_image_cache_service(self):
        from services.metadata.covers.image_cache_service import ImageCacheService
        return self._get('image_cache', lambda: ImageCacheService(cache_type="local", max_age_days=7))

    def get_hardcover_service(self):
        from services.sync.hardcover import HardcoverService
        return self._get('hardcover', lambda: HardcoverService(
            config_service=self.get_config_service(),
            db_service=self.get_database_service(),
        ))

    # --- Acquisition and processing ---

    def get_indexer_manager(self):
        from integrations.indexers import IndexerManager
        return self._get('indexer_manager',
                         lambda: IndexerManager(config_service=self.get_config_service()),
                         optional=True)

    def get_search_engine_service(self):
        from services.acquisition.search.search_engine_service import SearchEngineService
        return self._get('search_engine', SearchEngineService, optional=True)

    def get_download_management_service(self):
        from services.acquisition import DownloadManagementService
        return self._get('download_management', DownloadManagementService, optional=True)

    def get_automatic_download_service(self):
        from services.acquisition.automatic_download_service import AutomaticDownloadService
        return self._get('automatic_download', AutomaticDownloadService, optional=True)

    def get_conversion_service(self):
        from services.processing.conversion import ConversionService
        return self._get('conversion', lambda: ConversionService(config_service=self.get_config_service()),
                         optional=True)

    def get_file_naming_service(self):
        from services.processing.naming import FileNamingService
        return self._get('file_naming', FileNamingService, optional=True)

    def get_import_service(self):
        from services.processing.filing import ImportService
        return self._get('import', ImportService, optional=True)


_SERVICE_NAMES = (
    'audible_account', 'audible_catalog', 'audible_metadata_sync', 'audible_recommendations',
    'audible_series', 'audible_wishlist', 'audiobookshelf_runner', 'audnexus',
    'automatic_download', 'config',
    'conversion', 'database', 'download_management', 'file_naming', 'hardcover',
    'hybrid_audiobook', 'image_cache', 'import', 'indexer_manager', 'library_check', 'library_import',
    'library_repository', 'library_router', 'metadata_refresh', 'metadata_update', 'search_engine', 'status',
)

container = Container()
