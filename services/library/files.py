"""
Module Name: files.py
Author: AuralArchivist
Created: October 9, 2026
Last Modified: October 9, 2026
Description:
    Records that a book's file is now in a library folder: assigns the book
    to that library and announces it (BOOK_FILE_ADDED).

Location:
    /services/library/files.py
"""

from core import events
from core.logging import get_module_logger
from services.container import container

_LOGGER = get_module_logger("Service.Libraries.Files")


def record_book_file(asin: str, path: str) -> None:
    """Assign the book to the library holding `path` and publish BOOK_FILE_ADDED."""
    try:
        from .router import load_book
        book = load_book(asin)
        if not book:
            return
        repo = container.get_library_repository()
        library = repo.library_for_path(path)
        if library and library['id'] != book.get('library_id'):
            repo.assign_book(book['id'], library['id'])
        events.publish(events.BOOK_FILE_ADDED, book_id=book['id'], path=path)
    except Exception:
        _LOGGER.warning("Could not record the library for a book file", extra={"asin": asin}, exc_info=True)
