"""
Module Name: status_labels.py
Author: AuralArchivist
Created: June 25, 2026
Last Modified: October 9, 2026
Description:
    UI labels for library_status.

Location:
    /core/status_labels.py
"""

LIBRARY_STATUS_TO_UI: dict[str, str] = {
    'wanted':  'Wanted',
    'owned':   'Owned',
    'missing': 'Missing',
}


def library_status_to_ui(value: str) -> str:
    """Map a library_status DB value to a human-readable UI label."""
    return LIBRARY_STATUS_TO_UI.get((value or '').lower(), 'Wanted')

