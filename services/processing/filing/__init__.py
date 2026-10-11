"""
Module Name: __init__.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    The import service: moves a matched book's files into the library,
    names and tags them, and records the import.

Location:
    /services/processing/filing/__init__.py
"""

from .import_service import ImportService

__all__ = ['ImportService']
