"""
Module Name: __init__.py
Author: AuralArchivist
Created: August 12, 2025
Last Modified: October 9, 2026
Description:
    Exposes Audible service entry points and managers.

Location:
    /integrations/audible/__init__.py
"""

from .catalog.catalog_client import AudibleCatalogClient
from .account import AudibleAccount

__all__ = [
    'AudibleCatalogClient',
    'AudibleAccount',
]
