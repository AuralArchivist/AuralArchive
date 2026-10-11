"""
Module Name: __init__.py
Author: AuralArchivist
Created: August 13, 2025
Last Modified: October 9, 2026
Description:
    Exports Audible catalog service helpers.

Location:
    /integrations/audible/catalog/__init__.py
"""

from .catalog_client import AudibleCatalogClient

__all__ = ["AudibleCatalogClient"]
