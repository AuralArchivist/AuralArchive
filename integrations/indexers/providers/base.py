"""
Module Name: base.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Base interfaces and request spec for direct provider adapters used by
    DirectIndexer.

Location:
    /integrations/indexers/providers/base.py
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Sequence
from urllib.parse import urlparse
from abc import ABC, abstractmethod


@dataclass
class ProviderRequestSpec:
    """Description of an HTTP request that the DirectIndexer should perform."""

    method: str = "GET"
    path: str = ""
    params: Optional[Dict[str, Any]] = None
    data: Optional[Any] = None
    json: Optional[Any] = None
    headers: Optional[Dict[str, str]] = None
    expects_json: bool = True
    allow_missing: bool = False


class DirectProviderAdapter(ABC):
    """Base adapter used to integrate provider-specific APIs."""

    key: str = "generic"
    domains: Sequence[str] = ()

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.base_url = config.get("base_url", "")
        self.indexer_name = config.get("name", "Direct Provider")
        self.session_id = config.get("session_id", "")
        self.search_path = config.get("search_path")
        self.health_path = config.get("health_path")

    @classmethod
    def matches(cls, base_url: str, config: Dict[str, Any]) -> bool:
        provider_key = (config.get("provider_key") or config.get("provider") or "").lower()
        if provider_key and provider_key == cls.key:
            return True

        if not base_url:
            return False

        hostname = urlparse(base_url).hostname or base_url
        hostname = hostname.lower()
        return any(hostname.endswith(domain.lower()) for domain in cls.domains)

    def build_health_request(self) -> Optional[ProviderRequestSpec]:
        """Return request spec for provider health check."""
        if not self.health_path:
            return None
        return ProviderRequestSpec(path=self.health_path, params={"ts": self._timestamp()})

    def parse_health_response(self, payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        payload = payload if isinstance(payload, dict) else {}
        return {
            "capabilities": {
                "search_available": True,
                "book_search_available": True,
                "author_search_available": True,
                "categories": payload.get("categories") or [
                    {"id": "direct", "name": "Direct Provider"}
                ],
                "limits": payload.get("limits", {"max": 100, "default": 50}),
            },
            "version": payload.get("version", self.indexer_name)
        }

    @abstractmethod
    def build_search_request(
        self,
        query: str,
        author: str,
        title: str,
        limit: int,
        offset: int,
    ) -> ProviderRequestSpec:
        """Return request spec for provider search API."""

    @abstractmethod
    def parse_search_results(self, payload: Any) -> Sequence[Dict[str, Any]]:
        """Convert provider payload into normalized search results."""

    # --- Helper utilities ---

    @staticmethod
    def _timestamp() -> str:
        from datetime import datetime

        return datetime.utcnow().isoformat()
