"""
Module Name: result_identity.py
Author: AuralArchivist
Created: October 1, 2026
Last Modified: October 9, 2026
Description:
    Stable identity for an indexer search result, used to merge the results
    of several query variants without listing a release twice.

Location:
    /services/acquisition/search/result_identity.py
"""

from typing import Any, Dict, Hashable


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
