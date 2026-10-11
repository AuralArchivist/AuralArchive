"""
Module Name: convert.py
Author: AuralArchivist
Created: October 9, 2026
Last Modified: October 9, 2026
Description:
    Shared converters for config values and identifiers: yes/no flags,
    integers and ASINs.

Location:
    /core/convert.py
"""

from typing import Any, Optional

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


def to_bool(value: Any, default: bool = False, *, empty_as_default: bool = False) -> bool:
    """Read a yes/no value; None (and "" when empty_as_default) gives the default."""
    if isinstance(value, bool):
        return value
    if value is None or (empty_as_default and value == ""):
        return default
    return str(value).strip().lower() in _TRUE_VALUES


def to_int(value: Any, default: Any = 0) -> Any:
    """int(value), or the default when it can't be converted."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_asin(value: Any, *, uppercase: bool = True) -> Optional[str]:
    """Clean an ASIN-like value (bytes, stray quotes, spaces); None when empty."""
    if value is None:
        return None
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="ignore")
    text = str(value).strip()
    if text.lower().startswith("b'") and text.endswith("'"):
        text = text[2:-1]
    for quote in ("'", '"'):
        if text.startswith(quote) and text.endswith(quote):
            text = text[1:-1]
    if not text:
        return None
    return text.upper() if uppercase else text
