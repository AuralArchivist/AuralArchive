"""
Module Name: events.py
Author: AuralArchivist
Created: September 24, 2026
Last Modified: October 9, 2026
Description:
    One function per notification event. Errors are logged, never raised.

Location:
    /services/notifications/events.py
"""

import os
from typing import Any, Dict, Optional
from urllib.parse import quote

from core.logging import get_module_logger

from . import notification_service as ns

_LOGGER = get_module_logger("Service.Notifications.Events")


def _safe(fn):
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:
            _LOGGER.exception("Notification event failed", extra={"event": fn.__name__})
            return None
    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


def _title_of(download: Dict[str, Any]) -> str:
    return download.get('book_title') or download.get('title') or download.get('book_asin') or 'A download'


# --- Download pipeline -------------------------------------------------------

@_safe
def download_found(download_id: int, title: Optional[str], source: Optional[str]) -> None:
    """A wanted book's release was found by an indexer search. Replaced by
    the 'imported' (or 'failed') notification once the download finishes."""
    ns.notify(ns.TYPE_DOWNLOAD_FOUND, f"Found: {title or 'a wanted book'}",
              f"Release found{f' on {source}' if source else ''} - downloading now.",
              link='/downloads', level='info', dedupe_key=f"download_found:{download_id}")


@_safe
def download_imported(download_id: int, title: Optional[str], destination: Optional[str]) -> None:
    ns.resolve(f"download_found:{download_id}")
    ns.resolve(f"download_failed:{download_id}")
    where = os.path.dirname(destination) if destination else None
    ns.notify(ns.TYPE_DOWNLOAD_IMPORTED, f"Imported: {title or 'a book'}",
              f"Added to your library{f' at {where}' if where else ''}.",
              link='/library', level='success', dedupe_key=f"download_imported:{download_id}")


@_safe
def download_failed(download: Dict[str, Any], failure_status: str, error_message: Optional[str]) -> None:
    """A download ran out of retries."""
    download_id = download.get('id')
    ns.resolve(f"download_found:{download_id}")
    stage = {
        'SEARCH_FAILED': 'No release found',
        'DOWNLOAD_FAILED': 'Download failed',
        'AUDIBLE_DOWNLOAD_FAILED': 'Audible download failed',
        'CONVERSION_FAILED': 'Conversion failed',
        'IMPORT_FAILED': 'Import failed',
    }.get(failure_status, 'Failed')
    detail = (error_message or '').strip()
    if len(detail) > 160:
        detail = detail[:157] + '...'
    ns.notify(ns.TYPE_DOWNLOAD_FAILED, f"{stage}: {_title_of(download)}",
              (detail.rstrip('.') + '. ' if detail else '') + "Retries used up - retry it from the Downloads page.",
              link='/downloads', level='error', dedupe_key=f"download_failed:{download_id}")


# --- App update --------------------------------------------------------------

@_safe
def update_available(result: Dict[str, Any]) -> None:
    latest = result.get('latest_version')
    if not result.get('updates_available') or not latest:
        return
    ns.notify(ns.TYPE_UPDATE_AVAILABLE, f"AuralArchive {latest} is available",
              f"You're on {result.get('current_version')}. See what's new in the release notes.",
              link=result.get('release_url') or None, level='info', dedupe_key=f"update:{latest}")


# --- Storage health ----------------------------------------------------------

@_safe
def check_storage() -> None:
    """Mirror storage errors and warnings (unreachable or unwritable folders,
    low space, unmounted Docker folders) into notifications; each resolves
    itself once fixed."""
    from services.library.storage_status import get_storage_report
    current = []
    for notice in get_storage_report()['notices']:
        if notice['level'] not in ('error', 'warning'):
            continue
        key = f"storage:{notice['folder']}:{notice.get('short') or notice['message']}"[:250]
        current.append(key)
        ns.notify(ns.TYPE_STORAGE, f"Storage: {notice['folder']}",
                  notice.get('short') or notice['message'],
                  link='/settings#storage', level=notice['level'], dedupe_key=key)
    ns.resolve_missing('storage:', current)


# --- Audible sign-in ---------------------------------------------------------

@_safe
def check_audible_auth() -> None:
    """Only for users who signed in to Audible before: alert when the saved
    sign-in no longer works, resolve when it does again."""
    from core.paths import resolve_audible_auth_file
    if not os.path.isfile(resolve_audible_auth_file()):
        ns.resolve('audible_auth')
        return
    from services.container import container
    manager = container.get_audible_account()
    try:
        ok, message = manager.test_connection() if manager.auth else (False, 'Saved sign-in could not be loaded')
    except Exception as exc:
        ok, message = False, str(exc)
    if ok:
        ns.resolve('audible_auth')
        return
    ns.notify(ns.TYPE_AUDIBLE_AUTH, "Audible needs attention",
              f"{(message or 'Audible rejected the saved sign-in')[:140]}. Sign in again on the Profile page - "
              f"series refresh and Audible downloads won't work until then.",
              link='/settings#connected-services', level='error', dedupe_key='audible_auth', reopen_dismissed=True)


# --- Scheduled metadata refresh ----------------------------------------------

@_safe
def metadata_job_finished(job: str, label: str, failed: int, total: int, stopped: bool) -> None:
    key = f"metadata_failures:{job}"
    if failed and total:
        ns.notify(ns.TYPE_METADATA_FAILURES, f"{label} refresh: {failed} of {total} failed",
                  "Some items couldn't be refreshed from Audible. They'll be retried on the next run; "
                  "check the logs if it keeps happening.",
                  link='/settings#metadata', level='warning', dedupe_key=key, reopen_dismissed=True)
    elif not stopped:
        ns.resolve(key)


@_safe
def author_titles_changed(name: str, before: int, after: int) -> None:
    """Audible lists more titles for an author than at the last refresh."""
    if not name or before <= 0 or after <= before:
        return
    added = after - before
    ns.notify(ns.TYPE_AUTHOR_NEW_TITLES, f"New from {name}",
              f"{added} new title{'s' if added != 1 else ''} on Audible ({after} in total).",
              link=f"/authors/{quote(name)}", level='info', dedupe_key=f"author_new:{name}:{after}")
