"""
Module Name: path_mapping.py
Author: AuralArchivist
Created: April 14, 2026
Last Modified: October 9, 2026
Description:
    Helpers for translating remote download-client paths (as reported by
    SABnzbd/qBittorrent) to local filesystem paths, contained to the
    configured local base.

Location:
    /services/acquisition/path_mapping.py
"""

import os
from typing import Optional

from core.logging import get_module_logger

_LOGGER = get_module_logger("Service.Downloads.PathMapping")


def normalize_path(path: Optional[str]) -> str:
    """Forward slashes and no trailing slash, for comparing paths across hosts."""
    if not path:
        return ""
    normalized = str(path).replace("\\", "/").strip()
    while len(normalized) > 1 and normalized.endswith("/"):
        normalized = normalized[:-1]
    return normalized


def normalize_remote_path(path: Optional[str]) -> str:
    """normalize_path(), but a blank remote base means the root."""
    if not path:
        return ""
    return normalize_path(path) or "/"


def strip_trailing_separators(path: str) -> str:
    """Drop trailing / or \\ without changing the path's own separators."""
    if not path:
        return ''
    trimmed = path
    while len(trimmed) > 1 and trimmed.endswith(('/', '\\')):
        trimmed = trimmed[:-1]
    return trimmed


def join_remote_path(base: str, suffix: Optional[str]) -> str:
    """Append suffix to a client path using the separator style of base."""
    base = base or ''
    suffix = suffix or ''
    if not suffix:
        return base

    use_backslash = False
    if base.startswith('\\'):
        use_backslash = True
    elif base and '\\' in base and '/' not in base:
        use_backslash = True

    sep = '\\' if use_backslash else '/'

    formatted_suffix = (
        suffix.replace('/', sep)
        .replace('\\', sep)
        .strip('/\\')
    )
    if not formatted_suffix:
        return base

    if not base:
        return formatted_suffix

    if base.endswith(sep):
        return f"{base}{formatted_suffix}"

    return f"{base}{sep}{formatted_suffix}"


def remap_remote_to_local(path: Optional[str], remote_base: Optional[str], local_base: Optional[str]) -> Optional[str]:
    """Map a client-reported remote path to the equivalent local path.

    If mapping does not apply, returns the original path.
    """
    if not path:
        return path

    remote_norm = normalize_remote_path(remote_base)
    path_norm = normalize_remote_path(path)

    if not remote_norm or not local_base:
        return path

    if path_norm != remote_norm and not path_norm.startswith(remote_norm + "/"):
        return path

    local_base_abs = os.path.abspath(str(local_base))
    suffix = path_norm[len(remote_norm):].lstrip("/")
    if not suffix:
        return local_base_abs

    mapped = os.path.abspath(os.path.join(local_base_abs, suffix.replace("/", os.sep)))
    if os.path.commonpath([mapped, local_base_abs]) != local_base_abs:
        _LOGGER.warning("Refusing to map path outside the local base: %s", path)
        return path
    return mapped
