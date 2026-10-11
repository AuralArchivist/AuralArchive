"""
Module Name: hints.py
Author: AuralArchivist
Created: October 3, 2026
Last Modified: October 9, 2026
Description:
    Match hints: when a library folder can't be identified, the ASIN
    Audiobookshelf has for the same folder is used as a suggestion. It still
    goes through AuralArchive's normal Audible lookup and is only trusted when
    the title or author agrees with the folder.

Location:
    /services/sync/audiobookshelf/hints.py
"""

import re
from difflib import SequenceMatcher
from typing import Any, Dict, Optional

from core.logging import get_module_logger

from . import connection
from . import mapping as abs_mapping
from services.container import container

_LOGGER = get_module_logger("Service.Audiobookshelf.Hints")

AGREEMENT_RATIO = 0.8


def asin_for_folder(folder_path: str) -> Optional[str]:
    """ASIN Audiobookshelf has for this folder, from the last link sync."""
    try:
        if not connection.is_enabled() or not connection.get_settings()['hints']:
            return None
        from . import linker
        library = container.get_library_repository().library_for_path(folder_path)
        if not library or not abs_mapping.mapping_for_library(library['id']):
            return None
        rel = abs_mapping.relative_to_root(library['root'], folder_path)
        return linker.hint_for(library['id'], rel) if rel else None
    except Exception:
        _LOGGER.debug("Match hint lookup failed", extra={"folder": folder_path}, exc_info=True)
        return None


def _norm(value: Optional[str]) -> str:
    return re.sub(r'[^a-z0-9]+', ' ', (value or '').lower()).strip()


def agrees(metadata: Dict[str, Any], title_hint: Optional[str], author_hint: Optional[str]) -> bool:
    """True when the matched book's title or author looks like the folder's."""
    title = _norm(metadata.get('Title') or metadata.get('title'))
    author = _norm(metadata.get('Author') or metadata.get('author'))
    for ours, theirs in ((title, _norm(title_hint)), (author, _norm(author_hint))):
        if ours and theirs and (ours in theirs or theirs in ours
                                or SequenceMatcher(None, ours, theirs).ratio() >= AGREEMENT_RATIO):
            return True
    return False
