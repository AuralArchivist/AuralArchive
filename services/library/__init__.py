"""
Module Name: __init__.py
Author: AuralArchivist
Created: October 3, 2026
Last Modified: October 9, 2026
Description:
    What you have: the libraries and their routing rules, the on-disk
    matcher, the library check, Library Import and manual import.

Location:
    /services/library/__init__.py
"""

from .files import record_book_file
from .repository import LibraryError

__all__ = ['LibraryError', 'record_book_file']
