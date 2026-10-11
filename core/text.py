"""
Module Name: text.py
Author: AuralArchivist
Created: October 9, 2026
Last Modified: October 9, 2026
Description:
    Text helpers: runtime strings ("12 hrs 5 mins") and normalizing title,
    author and query strings for search.

Location:
    /core/text.py
"""

import re
from typing import Tuple

from core.logging import get_module_logger

# --- Runtime ------------------------------------------------------------------

_RUNTIME = re.compile(r'^\s*(?:(\d+)\s*hrs?)?\s*(?:(\d+)\s*mins?)?\s*$', re.I)


def runtime_to_minutes(value) -> int:
    """Minutes in a runtime string, or 0 when it isn't in that format."""
    if not isinstance(value, str):
        return 0
    match = _RUNTIME.match(value)
    if not match or not any(match.groups()):
        return 0
    hours, minutes = match.groups()
    return int(hours or 0) * 60 + int(minutes or 0)


def format_runtime(minutes) -> str:
    """Runtime string for a number of minutes, e.g. 725 -> "12 hrs 5 mins"."""
    return f"{int(minutes // 60)} hrs {int(round(minutes % 60))} mins"


# --- Search terms -------------------------------------------------------------

_LOGGER = get_module_logger("Utils.SearchNormalization")


def split_title_author(text: str) -> Tuple[str, str]:
    if not text:
        return "", ""
    parts = re.split(r"\s+by\s+", text, maxsplit=1, flags=re.IGNORECASE)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return text.strip(), ""


def strip_subtitle(text: str) -> str:
    if not text:
        return ""
    if ":" in text:
        return text.split(":", 1)[0].strip()
    return text.strip()


def normalize_search_terms(query: str, title: str, author: str) -> Tuple[str, str, str]:
    original_query = query
    original_title = title
    original_author = author

    query = (query or "").strip()
    title = (title or "").strip()
    author = (author or "").strip()

    if not title and query:
        title = query

    title_base, title_author = split_title_author(title)
    query_base, query_author = split_title_author(query)

    normalized_author = (author or title_author or query_author).strip()
    normalized_title = strip_subtitle(title_base)
    normalized_query_base = strip_subtitle(query_base)

    if normalized_title and normalized_author:
        normalized_query = f"{normalized_title} {normalized_author}".strip()
    elif normalized_title:
        normalized_query = normalized_title
    elif normalized_query_base:
        normalized_query = normalized_query_base
    else:
        normalized_query = normalized_author

    if not normalized_query:
        normalized_query = normalized_title or normalized_query_base or ""

    normalized_query = normalized_query.strip()

    _LOGGER.debug(
        "Normalized search terms",
        extra={
            "query": original_query,
            "title": original_title,
            "author": original_author,
            "normalized_query": normalized_query,
            "normalized_title": normalized_title,
            "normalized_author": normalized_author,
        },
    )

    return normalized_query, normalized_title, normalized_author
