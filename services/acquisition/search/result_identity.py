"""
Module Name: result_identity.py
Author: AuralArchivist
Created: October 1, 2026
Last Modified: November 7, 2026
Description:
    Stable identity for an indexer search result, used to merge the results
    of several query variants and repeat postings of one release.

Location:
    /services/acquisition/search/result_identity.py
"""

import re
from typing import Any, Dict, Hashable, List

from .book_matcher import BookMatcher


def result_key(result: Dict[str, Any]) -> Hashable:
    """Indexer's own ID first: Prowlarr signs a new download link on every
    search, so the same release never has the same download_url twice."""
    indexer = result.get('indexer') or ''
    if result.get('indexer_id'):
        return ('id', indexer, str(result['indexer_id']))
    info_hash = result.get('info_hash') or (result.get('raw_attributes') or {}).get('infohash')
    if info_hash:
        return ('hash', str(info_hash).lower())
    if result.get('title'):
        return ('title', indexer, result['title'], result.get('size_bytes') or result.get('size'))
    return ('url', result.get('download_url') or id(result))


def drop_duplicate_postings(ranked: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Keep the best-ranked of several uploads of one release on an indexer.
    The book number is part of the key because some indexers list rounded sizes,
    so two different books can show the same size."""
    matcher = BookMatcher()
    seen = set()
    kept = []
    for result in ranked:
        size = result.get('size_bytes') or result.get('size')
        author = (result.get('author') or '').strip().lower()
        if size and author:
            comps = matcher.extract_from_title(result.get('title') or '')
            book = (comps.book_number, comps.book_range)
            if book == (None, None):
                book = re.sub(r'\s*[\[(].*$', '', (result.get('title') or '').lower())
            key = (result.get('indexer') or '', author, size, book)
            if key in seen:
                continue
            seen.add(key)
        kept.append(result)
    return kept
