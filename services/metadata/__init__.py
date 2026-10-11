"""
Module Name: __init__.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Package initializer for metadata services. Exposes the metadata update
    service used to refresh book records.

Location:
    /services/metadata/__init__.py
"""

from .metadata_service import MetadataUpdateService

__all__ = ['MetadataUpdateService']
