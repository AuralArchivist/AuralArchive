"""
Module Name: audible_series_service.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Sync Audible series metadata and books into the database using the shared Audible client.

Location:
    /services/metadata/series/audible_series_service.py
"""


from core.logging import get_module_logger
from integrations.audible.series.series_data_fetcher import SeriesDataFetcher
from integrations.audible.series.series_relationship_extractor import SeriesRelationshipExtractor
from .series_book_processor import SeriesBookProcessor
from .series_database_sync import SeriesDatabaseSync


class AudibleSeriesService:
    """
    Main service for Audible series operations
    Orchestrates extraction, fetching, processing, and database sync
    """
    
    def __init__(self, logger=None):
        self.logger = logger or get_module_logger("Service.Audible.Series")
        self.extractor = SeriesRelationshipExtractor()
        self.fetcher = None
        self.processor = None
        self.sync = None

        self.logger.debug("Series service instantiated", extra={"instance_id": id(self)})
    
    def initialize(self, audible_client, db_service):
        """Initialize with required dependencies"""
        self.fetcher = SeriesDataFetcher(audible_client)
        self.processor = SeriesBookProcessor(db_service)
        self.sync = SeriesDatabaseSync(db_service)
        self.logger.debug(
            "Series service dependencies initialized",
            extra={"audible_client": bool(audible_client), "db_service": bool(db_service)},
        )
    
    def _known_book_asins(self, series_asin):
        """Book ASINs stored for the series before this sync (for new-title alerts)."""
        try:
            from services.notifications.watchers import series_book_asins
            return series_book_asins(series_asin)
        except Exception as exc:
            self.logger.debug("Could not read existing series books", extra={"series_asin": series_asin, "error": str(exc)})
            return set()

    def _notify_new_titles(self, series_asin, series_data, before, processed_books):
        """Raise a notification for each title that joined the series."""
        try:
            from services.notifications.watchers import notify_new_series_titles
            notify_new_series_titles(series_asin, (series_data or {}).get('series_title'), before, processed_books)
        except Exception as exc:
            self.logger.debug("New series title notification failed", extra={"series_asin": series_asin, "error": str(exc)})

    def refresh_series(self, series_asin):
        """Refresh data for a specific series"""
        try:
            self.logger.info("Refreshing series", extra={"series_asin": series_asin})
            
            series_product = self.fetcher.get_series_product(series_asin)
            series_data = self.fetcher.fetch_series_metadata(series_asin, series_product)
            if not series_data:
                return {'success': False, 'error': 'Failed to fetch series data'}
            
            books_data = self.fetcher.fetch_series_books(series_asin, series_product)
            processed_books = self.processor.process_series_books(series_asin, books_data)
            
            before = self._known_book_asins(series_asin)
            self.sync.sync_series_metadata(series_data)
            books_synced = self.sync.sync_series_books(processed_books)
            self._notify_new_titles(series_asin, series_data, before, processed_books)
            
            stats = self.processor.calculate_series_stats(processed_books)
            
            return {
                'success': True,
                'series_title': series_data.get('series_title'),
                'books_synced': books_synced,
                'stats': stats
            }
            
        except Exception as e:
            self.logger.error(
                "Error refreshing series",
                extra={"series_asin": series_asin, "error": str(e)},
                exc_info=True,
            )
            return {'success': False, 'error': str(e)}
