"""
Module Name: mover.py
Author: AuralArchivist
Created: October 3, 2026
Last Modified: October 9, 2026
Description:
    Moves a book's folder into another library, named with that library's
    template, then updates the book and announces the move.

Location:
    /services/library/mover.py
"""

import os
import shutil
from typing import Any, Dict

from sqlalchemy import text

from core.logging import get_module_logger
from core import events

from .repository import LibraryError, _real
from services.container import container

_LOGGER = get_module_logger("Service.Libraries.Mover")


def _is_inside(path: str, root: str) -> bool:
    real, base = _real(path), _real(root)
    return real != base and real.startswith(base.rstrip(os.sep) + os.sep)


def plan_move(asin: str, library_id: int) -> Dict[str, Any]:
    """Work out a move without touching files. Raises LibraryError."""
    repo = container.get_library_repository()
    target = repo.get(library_id)
    if not target:
        raise LibraryError('That library no longer exists')
    if not target['enabled']:
        raise LibraryError(f"The {target['name']} library is disabled")
    if not os.path.isdir(target['root']):
        raise LibraryError(f"The {target['name']} library folder isn't reachable")

    book = container.get_database_service().books.get_book_by_asin(asin)
    if not book:
        raise LibraryError('Book not found')
    source_file = (book.get('file_path') or book.get('File Path') or '').strip()
    if not source_file or not os.path.isfile(source_file):
        raise LibraryError("This book's file can't be found, so it can't be moved")
    source = repo.library_for_path(source_file)
    if source and source['id'] == target['id']:
        raise LibraryError(f"This book is already in {target['name']}")

    source_dir = os.path.dirname(source_file)
    if not source or not _is_inside(source_dir, source['root']):
        raise LibraryError("The book's folder isn't inside a library, so it won't be moved automatically")

    naming = container.get_file_naming_service()
    template = target.get('naming_template') or container.get_import_service().naming_template
    extension = os.path.splitext(source_file)[1].lstrip('.') or 'm4b'
    generated = naming.generate_file_path(book_data=book, base_path=target['root'], template_name=template,
                                          file_extension=extension)
    dest_dir = os.path.dirname(generated)
    if os.path.exists(dest_dir) and os.listdir(dest_dir):
        raise LibraryError(f'The destination folder already has files in it: {dest_dir}')

    # Other books' files in the same folder would be dragged along
    with container.get_engine().connect() as conn:
        rows = conn.execute(text(
            "SELECT file_path FROM books WHERE file_path IS NOT NULL AND UPPER(asin) != UPPER(:a)"
        ), {'a': asin}).fetchall()
    for (other,) in rows:
        if other and os.path.dirname(other.strip()) == source_dir:
            raise LibraryError("Another book shares this folder, so it can't be moved on its own")

    return {
        'book_id': book.get('id') or book.get('ID'),
        'asin': asin,
        'source_dir': source_dir,
        'source_file': source_file,
        'source_library': source,
        'target': target,
        'dest_dir': dest_dir,
        'dest_file': os.path.join(dest_dir, os.path.basename(source_file)),
    }


def move_book(asin: str, library_id: int) -> Dict[str, Any]:
    plan = plan_move(asin, library_id)
    dest_dir, source_dir = plan['dest_dir'], plan['source_dir']
    os.makedirs(os.path.dirname(dest_dir), exist_ok=True)
    if os.path.exists(dest_dir):
        os.rmdir(dest_dir)
    shutil.move(source_dir, dest_dir)

    with container.get_engine().begin() as conn:
        conn.execute(text(
            "UPDATE books SET file_path = :p, library_id = :l, updated_at = CURRENT_TIMESTAMP WHERE id = :id"
        ), {'p': plan['dest_file'], 'l': plan['target']['id'], 'id': plan['book_id']})

    _remove_empty_parents(source_dir, plan['source_library']['root'])
    _LOGGER.info("Book moved to another library",
                 extra={"asin": asin, "to": plan['target']['name'], "path": plan['dest_file']})
    events.publish(events.BOOK_FILE_MOVED, book_id=plan['book_id'], old_path=plan['source_file'], new_path=plan['dest_file'])
    return {'success': True, 'file_path': plan['dest_file'], 'library': plan['target']['name']}


def _remove_empty_parents(folder: str, root: str) -> None:
    """Remove now-empty folders left behind, never the library root itself."""
    current = os.path.dirname(folder)
    while _is_inside(current, root):
        try:
            if os.listdir(current):
                break
            os.rmdir(current)
        except OSError:
            break
        current = os.path.dirname(current)
