"""
Module Name: watchers.py
Author: AuralArchivist
Created: September 24, 2026
Last Modified: October 9, 2026
Description:
    Detectors that raise notifications.

Location:
    /services/notifications/watchers.py
"""

from typing import Any, Dict, Iterable, List, Set

from sqlalchemy import text

from core.logging import get_module_logger

from .notification_service import TYPE_BOOK_RELEASED, TYPE_SERIES_NEW_TITLE, notify
from services.container import container

_LOGGER = get_module_logger("Service.Notifications.Watchers")

RELEASE_LOOKBACK_DAYS = 30


def check_new_releases() -> int:
    """Alert once for each book added before its release that has now come
    out. Returns the number of books considered (each alert de-duplicates)."""
    with container.get_engine().connect() as conn:
        rows = conn.execute(text("""
            SELECT id, asin, title, author, release_date, series_asin, library_status
            FROM books
            WHERE release_date IS NOT NULL AND TRIM(release_date) != ''
              AND date(release_date) <= date('now', 'localtime')
              AND date(release_date) >= date('now', 'localtime', :lookback)
              AND created_at IS NOT NULL
              AND date(release_date) > date(created_at)
        """), {'lookback': f'-{RELEASE_LOOKBACK_DAYS} days'}).fetchall()
    for book_id, asin, title, author, release_date, series_asin, status in rows:
        detail = f"by {author}" if author else ""
        detail = f"{detail} · released {release_date}".strip(" ·")
        if (status or '').lower() == 'wanted':
            detail += " · on your wanted list"
        notify(
            TYPE_BOOK_RELEASED,
            f"Released: {title}",
            detail,
            link=f"/series/{series_asin}" if series_asin else "/library",
            level='success',
            dedupe_key=f"book_released:{asin or book_id}",
        )
    return len(rows)


def series_book_asins(series_asin: str) -> Set[str]:
    """Book ASINs currently stored for a series (call before syncing it)."""
    with container.get_engine().connect() as conn:
        return {row[0] for row in conn.execute(
            text("SELECT book_asin FROM series_books WHERE series_asin = :s"), {'s': series_asin}
        ) if row[0]}


def notify_new_series_titles(series_asin: str, series_title: str, before: Set[str],
                             processed_books: Iterable[Dict[str, Any]]) -> int:
    """Alert for books that joined a series since the last sync.

    Skipped on a series' first sync (nothing to compare with), for books
    already owned, and for titles the user hid. Returns alerts raised.
    """
    if not before:
        return 0
    new_books: List[Dict[str, Any]] = [
        b for b in processed_books
        if b.get('book_asin') and b['book_asin'] not in before and not b.get('in_library')
    ]
    if not new_books:
        return 0
    with container.get_engine().connect() as conn:
        hidden = {row[0] for row in conn.execute(text("SELECT asin FROM title_visibility"))}
    raised = 0
    for book in new_books:
        if book['book_asin'] in hidden:
            continue
        sequence = str(book.get('sequence') or '').strip()
        name = book.get('book_title') or 'New title'
        label = f"Book {sequence}: {name}" if sequence else name
        release = str(book.get('release_date') or '').strip()
        notify(
            TYPE_SERIES_NEW_TITLE,
            f"New in {series_title or 'a series'}",
            label + (f" · releases {release}" if release else ""),
            link=f"/series/{series_asin}",
            level='info',
            dedupe_key=f"series_new:{series_asin}:{book['book_asin']}",
        )
        raised += 1
    return raised
