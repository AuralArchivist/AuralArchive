"""
Module Name: library_status.py
Author: AuralArchivist
Created: June 25, 2026
Last Modified: October 9, 2026
Description:
    Transition guard for books.library_status (wanted / owned / missing).

Location:
    /core/database/repositories/library_status.py
"""

from typing import Dict, Optional, Set

from sqlalchemy import text, update as sa_update, func

from ..schema import books as books_table
from core.logging import get_module_logger

logger = get_module_logger("Core.Database.LibraryStatus")

VALID_STATUSES: Set[str] = {'wanted', 'owned', 'missing'}

CHANGED = 'changed'
UNCHANGED = 'unchanged'
REFUSED = 'refused'

ALLOWED_TRANSITIONS: Dict[str, Set[str]] = {
    'wanted': {'owned'},
    'owned': {'missing'},
    'missing': {'owned'},
}


def is_valid_transition(current_status: str, new_status: str) -> bool:
    """Check if a library_status transition is allowed."""
    return new_status in ALLOWED_TRANSITIONS.get(current_status, set())


def transition_library_status(
    conn,
    book_id: int,
    new_status: str,
    *,
    current_status: Optional[str] = None,
) -> str:
    """The single chokepoint for changing books.library_status.

    Callers should always go through this rather than a raw UPDATE - it's
    what actually enforces the write-authority rules above instead of just
    documenting them.

    Returns CHANGED, UNCHANGED (already that status) or REFUSED (unknown
    value, book not found, or disallowed transition). Callers publish
    BOOK_STATUS_CHANGED for CHANGED once their transaction has committed.
    """
    if new_status not in VALID_STATUSES:
        logger.warning(
            "Refusing library_status transition to unknown value '%s' for book %s",
            new_status, book_id,
        )
        return REFUSED

    if current_status is None:
        row = conn.execute(
            text("SELECT library_status FROM books WHERE id = :book_id"),
            {"book_id": book_id},
        ).fetchone()
        if not row:
            logger.warning("Refusing library_status transition: book %s not found", book_id)
            return REFUSED
        current_status = row[0]

    if current_status == new_status:
        return UNCHANGED

    if not is_valid_transition(current_status, new_status):
        logger.warning(
            "Refused invalid library_status transition %s -> %s for book %s",
            current_status, new_status, book_id,
        )
        return REFUSED

    conn.execute(
        sa_update(books_table)
        .where(books_table.c.id == book_id)
        .values(library_status=new_status, updated_at=func.current_timestamp())
    )

    return CHANGED
