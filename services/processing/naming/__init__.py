"""
Module Name: __init__.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Package init for the file naming service. Provides AudioBookShelf-compatible
    file naming and path generation with template parsing and sanitization.

Location:
    /services/processing/naming/__init__.py
"""

from .file_naming_service import FileNamingService

__all__ = ['FileNamingService']
