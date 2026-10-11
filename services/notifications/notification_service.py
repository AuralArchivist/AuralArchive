"""
Module Name: notification_service.py
Author: AuralArchivist
Created: September 24, 2026
Last Modified: October 9, 2026
Description:
    Notification centre behind the header bell.

Location:
    /services/notifications/notification_service.py
"""

from typing import Any, Dict, List, Optional

from sqlalchemy import text

from core.logging import get_module_logger
from services.container import container

_LOGGER = get_module_logger("Service.Notifications")

TYPE_BOOK_MISSING = 'book_missing'
TYPE_BOOK_RELEASED = 'book_released'
TYPE_SERIES_NEW_TITLE = 'series_new_title'
TYPE_LIBRARY_REVIEW = 'library_review'
TYPE_LIBRARY_CHECK_BLOCKED = 'library_check_blocked'
TYPE_DOWNLOAD_FAILED = 'download_failed'
TYPE_DOWNLOAD_FOUND = 'download_found'
TYPE_DOWNLOAD_IMPORTED = 'download_imported'
TYPE_UPDATE_AVAILABLE = 'update_available'
TYPE_AUDIBLE_AUTH = 'audible_auth'
TYPE_STORAGE = 'storage'
TYPE_METADATA_FAILURES = 'metadata_failures'
TYPE_AUTHOR_NEW_TITLES = 'author_new_titles'
TYPE_RELEASE_CALENDAR = 'release_calendar'
TYPE_NEXT_IN_SERIES = 'next_in_series'
TYPE_AUDIOBOOKSHELF = 'audiobookshelf'

_COLUMNS = ('id', 'type', 'level', 'title', 'message', 'link', 'dedupe_key', 'created_at', 'read_at')


def _push() -> None:
    """Tell open pages the unread count changed (best effort)."""
    try:
        from app import app, socketio
        with app.app_context():
            socketio.emit('notifications:updated', {'unread': unread_count()})
    except Exception:
        _LOGGER.debug("Notification push skipped", exc_info=True)


def notify(ntype: str, title: str, message: Optional[str] = None, *, link: Optional[str] = None,
           level: str = 'info', dedupe_key: Optional[str] = None,
           reopen_dismissed: bool = False) -> Optional[int]:
    """Raise a notification. With a dedupe_key, an existing notification is
    updated in place and marked unread again only if its text changed. A
    dismissed one stays dismissed unless reopen_dismissed is set and the text
    changed (used for summaries like "3 folders need review" whose count
    moves). Returns the row id, or None."""
    try:
        with container.get_engine().begin() as conn:
            if dedupe_key:
                row = conn.execute(
                    text("SELECT id, title, message, dismissed_at FROM notifications WHERE dedupe_key = :k"),
                    {'k': dedupe_key},
                ).fetchone()
                if row:
                    changed = (row[1], row[2]) != (title, message)
                    dismissed = row[3] is not None
                    if not changed or (dismissed and not reopen_dismissed):
                        return row[0]
                    conn.execute(text(
                        "UPDATE notifications SET title = :t, message = :m, link = :l, level = :lv, "
                        "created_at = CURRENT_TIMESTAMP, read_at = NULL, dismissed_at = NULL WHERE id = :id"
                    ), {'t': title, 'm': message, 'l': link, 'lv': level, 'id': row[0]})
                    notification_id = row[0]
                else:
                    notification_id = None
            else:
                notification_id = None
            if notification_id is None:
                result = conn.execute(text(
                    "INSERT INTO notifications (type, level, title, message, link, dedupe_key) "
                    "VALUES (:type, :level, :title, :message, :link, :key)"
                ), {'type': ntype, 'level': level, 'title': title, 'message': message,
                    'link': link, 'key': dedupe_key})
                notification_id = result.lastrowid
        _LOGGER.info("Notification", extra={"type": ntype, "title": title})
        _push()
        return notification_id
    except Exception:
        _LOGGER.exception("Failed to raise notification", extra={"type": ntype, "title": title})
        return None


def resolve(dedupe_key: str) -> bool:
    """Delete the notification for a condition that no longer holds."""
    try:
        with container.get_engine().begin() as conn:
            removed = conn.execute(text("DELETE FROM notifications WHERE dedupe_key = :k"),
                                   {'k': dedupe_key}).rowcount
        if removed:
            _push()
        return bool(removed)
    except Exception:
        _LOGGER.exception("Failed to resolve notification", extra={"dedupe_key": dedupe_key})
        return False


def keys_with_prefix(prefix: str) -> List[str]:
    """dedupe_keys starting with prefix (dismissed ones too), for resolving
    a family of conditions that no longer hold - e.g. storage problems."""
    with container.get_engine().connect() as conn:
        return [row[0] for row in conn.execute(
            text("SELECT dedupe_key FROM notifications WHERE dedupe_key LIKE :p"),
            {'p': prefix.replace('%', '') + '%'},
        )]


def resolve_missing(prefix: str, current_keys) -> int:
    """Resolve every prefix-family notification whose key isn't current."""
    current = set(current_keys)
    stale = [key for key in keys_with_prefix(prefix) if key not in current]
    for key in stale:
        resolve(key)
    return len(stale)


def list_notifications(limit: int = 50) -> List[Dict[str, Any]]:
    """Open (not dismissed) notifications, unread first, newest first."""
    with container.get_engine().connect() as conn:
        rows = conn.execute(text(
            f"SELECT {', '.join(_COLUMNS)} FROM notifications WHERE dismissed_at IS NULL "
            "ORDER BY (read_at IS NULL) DESC, created_at DESC, id DESC LIMIT :limit"
        ), {'limit': max(1, min(200, int(limit)))}).fetchall()
    return [dict(zip(_COLUMNS, row)) for row in rows]


def unread_count() -> int:
    with container.get_engine().connect() as conn:
        return int(conn.execute(text(
            "SELECT COUNT(*) FROM notifications WHERE dismissed_at IS NULL AND read_at IS NULL"
        )).scalar() or 0)


def mark_read(notification_id: int) -> bool:
    with container.get_engine().begin() as conn:
        changed = conn.execute(text(
            "UPDATE notifications SET read_at = CURRENT_TIMESTAMP WHERE id = :id AND read_at IS NULL"
        ), {'id': int(notification_id)}).rowcount
    if changed:
        _push()
    return bool(changed)


def mark_all_read() -> int:
    with container.get_engine().begin() as conn:
        changed = conn.execute(text(
            "UPDATE notifications SET read_at = CURRENT_TIMESTAMP WHERE read_at IS NULL AND dismissed_at IS NULL"
        )).rowcount
    if changed:
        _push()
    return changed


def dismiss(notification_id: int) -> bool:
    with container.get_engine().begin() as conn:
        changed = conn.execute(text(
            "UPDATE notifications SET dismissed_at = CURRENT_TIMESTAMP, "
            "read_at = COALESCE(read_at, CURRENT_TIMESTAMP) WHERE id = :id AND dismissed_at IS NULL"
        ), {'id': int(notification_id)}).rowcount
    if changed:
        _push()
    return bool(changed)


def dismiss_all() -> int:
    with container.get_engine().begin() as conn:
        changed = conn.execute(text(
            "UPDATE notifications SET dismissed_at = CURRENT_TIMESTAMP, "
            "read_at = COALESCE(read_at, CURRENT_TIMESTAMP) WHERE dismissed_at IS NULL"
        )).rowcount
    if changed:
        _push()
    return changed
