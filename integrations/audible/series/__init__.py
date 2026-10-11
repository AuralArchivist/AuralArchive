"""
Module Name: __init__.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Fetching and parsing series data from the Audible catalog.

Location:
    /integrations/audible/series/__init__.py
"""

from .series_data_fetcher import SeriesDataFetcher
from .series_relationship_extractor import SeriesRelationshipExtractor

__all__ = ["SeriesDataFetcher", "SeriesRelationshipExtractor"]
