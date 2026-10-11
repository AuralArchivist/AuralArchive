"""
Module Name: __init__.py
Author: AuralArchivist
Created: September 24, 2026
Last Modified: October 9, 2026
Description:
    Package exports for the notification centre (header bell).

Location:
    /services/notifications/__init__.py
"""

from .notification_service import (
    TYPE_AUDIBLE_AUTH,
    TYPE_AUTHOR_NEW_TITLES,
    TYPE_BOOK_MISSING,
    TYPE_BOOK_RELEASED,
    TYPE_DOWNLOAD_FAILED,
    TYPE_DOWNLOAD_FOUND,
    TYPE_DOWNLOAD_IMPORTED,
    TYPE_LIBRARY_CHECK_BLOCKED,
    TYPE_LIBRARY_REVIEW,
    TYPE_AUDIOBOOKSHELF,
    TYPE_METADATA_FAILURES,
    TYPE_NEXT_IN_SERIES,
    TYPE_RELEASE_CALENDAR,
    TYPE_SERIES_NEW_TITLE,
    TYPE_STORAGE,
    TYPE_UPDATE_AVAILABLE,
    dismiss,
    dismiss_all,
    keys_with_prefix,
    list_notifications,
    mark_all_read,
    mark_read,
    notify,
    resolve,
    resolve_missing,
    unread_count,
)

__all__ = [
    'TYPE_AUDIBLE_AUTH',
    'TYPE_AUTHOR_NEW_TITLES',
    'TYPE_BOOK_MISSING',
    'TYPE_BOOK_RELEASED',
    'TYPE_DOWNLOAD_FAILED',
    'TYPE_DOWNLOAD_FOUND',
    'TYPE_DOWNLOAD_IMPORTED',
    'TYPE_LIBRARY_CHECK_BLOCKED',
    'TYPE_LIBRARY_REVIEW',
    'TYPE_AUDIOBOOKSHELF',
    'TYPE_METADATA_FAILURES',
    'TYPE_NEXT_IN_SERIES',
    'TYPE_RELEASE_CALENDAR',
    'TYPE_SERIES_NEW_TITLE',
    'TYPE_STORAGE',
    'TYPE_UPDATE_AVAILABLE',
    'dismiss',
    'dismiss_all',
    'keys_with_prefix',
    'list_notifications',
    'mark_all_read',
    'mark_read',
    'notify',
    'resolve',
    'resolve_missing',
    'unread_count',
]
