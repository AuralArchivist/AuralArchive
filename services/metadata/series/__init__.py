"""
Module Name: __init__.py
Author: AuralArchivist
Created: October 9, 2026
Last Modified: October 9, 2026
Description:
    Series syncing: fetch a series from the Audible catalog and store its
    books. get_series_service() returns the shared, ready-to-use service.

Location:
    /services/metadata/series/__init__.py
"""

from .audible_series_service import AudibleSeriesService

__all__ = ["AudibleSeriesService", "get_series_service"]


def get_series_service(*, reinitialize: bool = False) -> AudibleSeriesService:
    """The series service, set up with the signed-in Audible client when there
    is one and the public catalog otherwise (series need no sign-in)."""
    from services.container import container
    service = container.get_audible_series_service()
    if reinitialize or service.fetcher is None:
        from integrations.audible.catalog.catalog_search import PublicCatalogClient
        manager = container.get_audible_account()
        client = (manager.get_client() if manager.auth else None) or PublicCatalogClient()
        service.initialize(client, container.get_database_service())
    return service
