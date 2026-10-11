"""
Module Name: __init__.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Indexer package exposing Torznab (Jackett), Prowlarr, NZBHydra2, and
    direct provider implementations for audiobook searches.

Location:
    /integrations/indexers/__init__.py
"""

from .base_indexer import BaseIndexer, IndexerProtocol, IndexerType
from .jackett_indexer import JackettIndexer
from .prowlarr_indexer import ProwlarrIndexer
from .nzbhydra2_indexer import NZBHydra2Indexer
from .direct_indexer import DirectIndexer
from .indexer_manager import IndexerManager

__all__ = [
    'BaseIndexer',
    'IndexerProtocol',
    'IndexerType',
    'JackettIndexer',
    'ProwlarrIndexer',
    'NZBHydra2Indexer',
    'DirectIndexer',
    'IndexerManager',
]
