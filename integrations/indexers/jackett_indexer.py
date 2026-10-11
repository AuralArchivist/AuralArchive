"""
Module Name: jackett_indexer.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Jackett-specific indexer module. Responsible only for resolving the
    Jackett Torznab API endpoint URL from configuration. All Torznab
    protocol logic (XML parsing, result shaping, caps, etc.) lives in
    TorznabBaseIndexer.

Location:
    /integrations/indexers/jackett_indexer.py
"""

from __future__ import annotations

from typing import Any, Dict

from .torznab_base_indexer import TorznabBaseIndexer
from core.logging import get_module_logger


_LOGGER = get_module_logger("Service.Indexers.Jackett")


class JackettIndexer(TorznabBaseIndexer):
    """
    Jackett Torznab indexer.

    Resolves the Torznab endpoint from either a full `feed_url` or a
    `base_url` + optional `indexer_id`. All Torznab protocol logic is
    handled by TorznabBaseIndexer.

    Config keys (in addition to base):
        feed_url    – full Torznab feed URL (takes priority over base_url)
        indexer_id  – Jackett indexer ID (default: "all")
    """

    def __init__(self, config: Dict[str, Any], *, logger=None):
        config = dict(config)

        feed_url = config.get("feed_url")
        if feed_url:
            api_endpoint = feed_url.rstrip("/")
            parts = api_endpoint.split("/api/")
            config["base_url"] = parts[0] if parts else api_endpoint
            try:
                self.indexer_id = api_endpoint.split("/indexers/")[1].split("/")[0]
            except (IndexError, AttributeError):
                self.indexer_id = "all"
        else:
            base_url = config.get("base_url", "http://172.18.0.1:9117").rstrip("/")
            config["base_url"] = base_url
            self.indexer_id = config.get("indexer_id", "all")
            api_endpoint = (
                f"{base_url}/api/v2.0/indexers/{self.indexer_id}/results/torznab"
            )

        super().__init__(config, api_endpoint, logger=logger or _LOGGER)
        self.logger.debug(
            "Jackett indexer ready",
            extra={"indexer_id": self.indexer_id, "endpoint": self.api_endpoint},
        )
