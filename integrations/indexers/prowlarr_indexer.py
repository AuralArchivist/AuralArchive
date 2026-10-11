"""
Module Name: prowlarr_indexer.py
Author: AuralArchivist
Created: February 18, 2026
Last Modified: October 9, 2026
Description:
    Prowlarr indexer using Torznab or Newznab based on the protocol setting.

Location:
    /integrations/indexers/prowlarr_indexer.py
"""

from __future__ import annotations

from typing import Any, Dict

from .torznab_base_indexer import TorznabBaseIndexer
from .newznab_base_indexer import NewznabBaseIndexer
from core.logging import get_module_logger


_LOGGER_TORZNAB = get_module_logger("Service.Indexers.Prowlarr.Torznab")
_LOGGER_NEWZNAB = get_module_logger("Service.Indexers.Prowlarr.Newznab")


def ProwlarrIndexer(config: Dict[str, Any], *, logger=None):
    """
    Factory that returns a Prowlarr indexer instance for the correct protocol.

    Reads `config['protocol']` to determine whether to use Torznab (default)
    or Newznab. All protocol logic is handled by the respective base class.

    Config keys:
        base_url    – Prowlarr root URL (required), e.g. http://prowlarr:9696
        api_key     – Prowlarr API key (required)
        protocol    – 'torznab' (default) | 'newznab'
        indexer_id  – Prowlarr indexer ID (default: 0 = all indexers)
    """
    config = dict(config)
    base_url = config.get("base_url", "").rstrip("/")
    if not base_url:
        raise ValueError("Prowlarr indexer requires 'base_url' to be set")
    config["base_url"] = base_url

    indexer_id = int(config.get("indexer_id") or 0)
    protocol = (config.get("protocol") or "torznab").lower()

    if protocol == "newznab":
        api_endpoint = f"{base_url}/api/v1/indexer/{indexer_id}/newznab"
        _logger = logger or _LOGGER_NEWZNAB
        instance = _ProwlarrNewznabIndexer(config, api_endpoint, indexer_id, logger=_logger)
    else:
        api_endpoint = f"{base_url}/{indexer_id}/api"
        _logger = logger or _LOGGER_TORZNAB
        instance = _ProwlarrTorznabIndexer(config, api_endpoint, indexer_id, logger=_logger)

    instance.logger.debug(
        "Prowlarr indexer ready",
        extra={
            "indexer_name": instance.name,
            "protocol": protocol,
            "indexer_id": indexer_id,
            "endpoint": api_endpoint,
        },
    )
    return instance


class _ProwlarrTorznabIndexer(TorznabBaseIndexer):
    """Prowlarr torrent (Torznab) indexer."""

    def __init__(
        self,
        config: Dict[str, Any],
        api_endpoint: str,
        indexer_id: int,
        *,
        logger=None,
    ):
        self.indexer_id = indexer_id
        super().__init__(config, api_endpoint, logger=logger or _LOGGER_TORZNAB)


class _ProwlarrNewznabIndexer(NewznabBaseIndexer):
    """Prowlarr Usenet/NZB (Newznab) indexer."""

    def __init__(
        self,
        config: Dict[str, Any],
        api_endpoint: str,
        indexer_id: int,
        *,
        logger=None,
    ):
        self.indexer_id = indexer_id
        super().__init__(config, api_endpoint, logger=logger or _LOGGER_NEWZNAB)
