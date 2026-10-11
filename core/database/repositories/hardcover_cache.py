"""
Module Name: hardcover_cache.py
Author: AuralArchivist
Created: July 1, 2026
Last Modified: October 9, 2026
Description:
    Accessors for the Hardcover tables. Callers own the connection and
    transaction.

Location:
    /core/database/repositories/hardcover_cache.py
"""

import json
from typing import Any, Dict, List, Optional

from sqlalchemy import text


def get_link_for_book(conn, book_id: int) -> Optional[Dict[str, Any]]:
    """Return the hardcover_link row for a local book, or None if unmatched."""
    row = conn.execute(
        text("SELECT * FROM hardcover_link WHERE book_id = :book_id"),
        {"book_id": book_id},
    ).mappings().first()
    return dict(row) if row else None


def upsert_link(
    conn,
    book_id: int,
    hardcover_book_id: int,
    *,
    hardcover_edition_id: Optional[int] = None,
    asin: Optional[str] = None,
    match_confidence: str = "unverified",
) -> None:
    """Create or update the Hardcover match for a local book."""
    existing = get_link_for_book(conn, book_id)
    if existing:
        conn.execute(
            text(
                """
                UPDATE hardcover_link
                SET hardcover_book_id = :hardcover_book_id,
                    hardcover_edition_id = :hardcover_edition_id,
                    asin = :asin,
                    match_confidence = :match_confidence,
                    matched_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE book_id = :book_id
                """
            ),
            {
                "book_id": book_id,
                "hardcover_book_id": hardcover_book_id,
                "hardcover_edition_id": hardcover_edition_id,
                "asin": asin,
                "match_confidence": match_confidence,
            },
        )
    else:
        conn.execute(
            text(
                """
                INSERT INTO hardcover_link
                    (book_id, hardcover_book_id, hardcover_edition_id, asin,
                     match_confidence, matched_at, created_at, updated_at)
                VALUES
                    (:book_id, :hardcover_book_id, :hardcover_edition_id, :asin,
                     :match_confidence, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """
            ),
            {
                "book_id": book_id,
                "hardcover_book_id": hardcover_book_id,
                "hardcover_edition_id": hardcover_edition_id,
                "asin": asin,
                "match_confidence": match_confidence,
            },
        )


def delete_link(conn, book_id: int) -> None:
    """Remove a local book's Hardcover match."""
    conn.execute(text("DELETE FROM hardcover_link WHERE book_id = :book_id"), {"book_id": book_id})


def record_match_attempt(conn, book_id: int) -> None:
    """Remember that automatic matching found nothing for this book."""
    conn.execute(
        text(
            "INSERT INTO hardcover_match_attempts (book_id, attempted_at) VALUES (:book_id, CURRENT_TIMESTAMP) "
            "ON CONFLICT(book_id) DO UPDATE SET attempted_at = CURRENT_TIMESTAMP"
        ),
        {"book_id": book_id},
    )


def get_unmatched_book_ids(conn, retry_days: int = 30) -> List[int]:
    """Books with no Hardcover link, skipping ones tried within retry_days."""
    rows = conn.execute(
        text(
            """
            SELECT b.id FROM books b
            LEFT JOIN hardcover_link l ON l.book_id = b.id
            LEFT JOIN hardcover_match_attempts a ON a.book_id = b.id
            WHERE l.book_id IS NULL
              AND b.title IS NOT NULL AND b.title != ''
              AND (a.attempted_at IS NULL OR a.attempted_at < datetime('now', :cutoff))
            ORDER BY b.id
            """
        ),
        {"cutoff": f"-{int(retry_days)} days"},
    ).fetchall()
    return [int(row[0]) for row in rows]


def enqueue_push(conn, book_id: int, action: str, payload: Dict[str, Any], *, max_retries: int = 5) -> int:
    """Add a pending push to the queue. Returns the new row id."""
    result = conn.execute(
        text(
            """
            INSERT INTO hardcover_push_queue
                (book_id, action, payload, status, retry_count, max_retries, created_at, updated_at)
            VALUES
                (:book_id, :action, :payload, 'pending', 0, :max_retries, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """
        ),
        {
            "book_id": book_id,
            "action": action,
            "payload": json.dumps(payload),
            "max_retries": max_retries,
        },
    )
    return result.lastrowid


def get_pending_pushes(conn, limit: int = 25) -> List[Dict[str, Any]]:
    rows = conn.execute(
        text(
            """
            SELECT * FROM hardcover_push_queue
            WHERE status IN ('pending', 'failed') AND retry_count < max_retries
            ORDER BY created_at ASC
            LIMIT :limit
            """
        ),
        {"limit": limit},
    ).mappings().all()
    return [dict(r) for r in rows]


def get_sync_queue(conn, limit: int = 50) -> List[Dict[str, Any]]:
    """Return recent push-queue rows joined with book title/author for the UI."""
    rows = conn.execute(
        text(
            """
            SELECT q.*, b.title AS book_title, b.author AS book_author
            FROM hardcover_push_queue q
            LEFT JOIN books b ON b.id = q.book_id
            ORDER BY q.created_at DESC
            LIMIT :limit
            """
        ),
        {"limit": limit},
    ).mappings().all()
    return [dict(r) for r in rows]


def mark_push_sent(conn, push_id: int) -> None:
    conn.execute(
        text(
            "UPDATE hardcover_push_queue SET status = 'sent', updated_at = CURRENT_TIMESTAMP WHERE id = :id"
        ),
        {"id": push_id},
    )


def mark_push_failed(conn, push_id: int, error: str) -> None:
    conn.execute(
        text(
            """
            UPDATE hardcover_push_queue
            SET status = 'failed',
                retry_count = retry_count + 1,
                last_error = :error,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :id
            """
        ),
        {"id": push_id, "error": error[:500] if error else None},
    )


def reset_push_for_retry(conn, push_id: int) -> None:
    """Manual "retry now" - put a failed row back to pending regardless of retry_count."""
    conn.execute(
        text(
            """
            UPDATE hardcover_push_queue
            SET status = 'pending', retry_count = 0, updated_at = CURRENT_TIMESTAMP
            WHERE id = :id
            """
        ),
        {"id": push_id},
    )


def replace_trending_cache(conn, window: str, entries: List[Dict[str, Any]]) -> None:
    """Atomically swap the cached trending list for one window."""
    conn.execute(text("DELETE FROM hardcover_trending_cache WHERE window = :window"), {"window": window})
    for entry in entries:
        conn.execute(
            text(
                """
                INSERT INTO hardcover_trending_cache
                    (window, rank, hardcover_book_id, title, author, cover_url,
                     stat_label, rating, ratings_count, asin, narrator, audio_seconds, refreshed_at)
                VALUES
                    (:window, :rank, :hardcover_book_id, :title, :author, :cover_url,
                     :stat_label, :rating, :ratings_count, :asin, :narrator, :audio_seconds, CURRENT_TIMESTAMP)
                """
            ),
            {"window": window, **entry},
        )


def get_trending_cache(conn, window: str) -> List[Dict[str, Any]]:
    rows = conn.execute(
        text(
            "SELECT * FROM hardcover_trending_cache WHERE window = :window ORDER BY rank ASC"
        ),
        {"window": window},
    ).mappings().all()
    return [dict(r) for r in rows]


def get_trending_refreshed_at(conn, window: str) -> Optional[str]:
    row = conn.execute(
        text(
            "SELECT MAX(refreshed_at) AS refreshed_at FROM hardcover_trending_cache WHERE window = :window"
        ),
        {"window": window},
    ).mappings().first()
    return row["refreshed_at"] if row else None


def replace_new_releases_cache(conn, entries: List[Dict[str, Any]]) -> None:
    conn.execute(text("DELETE FROM hardcover_new_releases_cache"))
    for entry in entries:
        conn.execute(
            text(
                """
                INSERT INTO hardcover_new_releases_cache
                    (hardcover_book_id, title, author, cover_url, release_date,
                     asin, narrator, audio_seconds, refreshed_at)
                VALUES
                    (:hardcover_book_id, :title, :author, :cover_url, :release_date,
                     :asin, :narrator, :audio_seconds, CURRENT_TIMESTAMP)
                """
            ),
            entry,
        )


def get_new_releases_cache(conn) -> List[Dict[str, Any]]:
    rows = conn.execute(
        text("SELECT * FROM hardcover_new_releases_cache ORDER BY release_date ASC")
    ).mappings().all()
    return [dict(r) for r in rows]


def get_new_releases_refreshed_at(conn) -> Optional[str]:
    row = conn.execute(
        text("SELECT MAX(refreshed_at) AS refreshed_at FROM hardcover_new_releases_cache")
    ).mappings().first()
    return row["refreshed_at"] if row else None
