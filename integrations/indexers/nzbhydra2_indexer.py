"""
Module Name: nzbhydra2_indexer.py
Author: AuralArchivist
Created: February 18, 2026
Last Modified: October 9, 2026
Description:
    NZBHydra2 indexer module. Responsible only for resolving the NZBHydra2
    Newznab API endpoint URL from configuration. All Newznab protocol logic
    lives in NewznabBaseIndexer.

Location:
    /integrations/indexers/nzbhydra2_indexer.py
"""

from __future__ import annotations

from typing import Any, Dict

from .newznab_base_indexer import NewznabBaseIndexer
from core.logging import get_module_logger


_LOGGER = get_module_logger("Service.Indexers.NZBHydra2")


class NZBHydra2Indexer(NewznabBaseIndexer):
    """
    NZBHydra2 Newznab indexer.

    Resolves the NZBHydra2 API endpoint from configuration.
    All Newznab protocol logic is handled by NewznabBaseIndexer.

    Config keys (in addition to base):
        indexer_name  – NZBHydra2 indexer name (string, optional).
                        When set, restricts searches to that specific indexer
                        via the ``indexers`` query parameter.
                        Omit or leave empty to search all configured indexers.

    NZBHydra2 endpoint format:
        {base_url}/api                              (all indexers)
        {base_url}/api?t=...&indexers={name}        (specific indexer, by name)
    """

    def __init__(self, config: Dict[str, Any], *, logger=None):
        config = dict(config)
        base_url = config.get("base_url", "").rstrip("/")
        config["base_url"] = base_url

        self.indexer_name: str = str(config.get("indexer_name") or "").strip()
        if not self.indexer_name:
            legacy_id = config.get("indexer_id", 0)
            if legacy_id:
                self.indexer_name = str(legacy_id)

        api_endpoint = f"{base_url}/api"

        super().__init__(config, api_endpoint, logger=logger or _LOGGER)
        self.logger.debug(
            "NZBHydra2 indexer ready",
            extra={"indexer_name": self.indexer_name or "(all)", "endpoint": self.api_endpoint},
        )

    def _inject_auth(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Inject auth and optionally scope to a specific NZBHydra2 indexer."""
        merged = super()._inject_auth(params)
        if self.indexer_name:
            merged.setdefault("indexers", self.indexer_name)
        return merged
