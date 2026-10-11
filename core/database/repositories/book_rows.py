"""
Module Name: book_rows.py
Author: AuralArchivist
Created: September 23, 2026
Last Modified: October 9, 2026
Description:
    Maps a books row to the app's book dict by column name.

Location:
    /core/database/repositories/book_rows.py
"""

from typing import Any, Dict

BOOK_COLUMN_KEYS = {
    "id": "ID",
    "title": "Title",
    "author": "Author",
    "series": "Series",
    "sequence": "Sequence",
    "narrator": "Narrator",
    "runtime": "Runtime",
    "release_date": "Release Date",
    "language": "Language",
    "publisher": "Publisher",
    "overall_rating": "Overall Rating",
    "rating": "Rating",
    "status": "Status",
    "asin": "ASIN",
    "summary": "Summary",
    "cover_image": "Cover Image",
    "created_at": "Created At",
    "updated_at": "Updated At",
}


def book_row_to_dict(row: Any) -> Dict[str, Any]:
    """Convert a SQLAlchemy row from `SELECT * FROM books ...` into a book dict."""
    return {BOOK_COLUMN_KEYS.get(column, column): value for column, value in row._mapping.items()}
