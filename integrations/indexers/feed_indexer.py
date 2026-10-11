"""
Module Name: feed_indexer.py
Author: AuralArchivist
Created: September 29, 2026
Last Modified: October 9, 2026
Description:
    Shared base for Newznab and Torznab indexers: HTTP, capabilities and
    their disk cache, and XML item parsing. Subclasses set the protocol and
    handle search parameters and download links.

Location:
    /integrations/indexers/feed_indexer.py
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from typing import Any, Dict, List, Optional
import xml.etree.ElementTree as ET

import requests

from .base_indexer import BaseIndexer
from core.logging import get_module_logger
from core import http

_CAPS_CACHE_TTL = 7 * 24 * 3600


class FeedIndexer(BaseIndexer):
    """Newznab/Torznab behaviour shared by both protocols."""

    PROTOCOL = ""
    PROTOCOL_LABEL = ""
    NS: Dict[str, str] = {}

    def __init__(
        self,
        config: Dict[str, Any],
        api_endpoint: str,
        *,
        logger=None,
    ):
        config = dict(config)
        config["protocol"] = self.PROTOCOL
        self.api_endpoint = api_endpoint.rstrip("/")
        super().__init__(config, logger=logger or get_module_logger(f"Service.Indexers.{self.PROTOCOL_LABEL}"))
        if not self.capabilities:
            cached = self._load_caps_cache()
            if cached:
                self.capabilities = cached
                self.logger.debug(
                    "Loaded capabilities from disk cache",
                    extra={"indexer_name": self.name},
                )
        self.logger.debug(
            "%s indexer ready",
            self.PROTOCOL_LABEL,
            extra={"indexer_name": self.name, "endpoint": self.api_endpoint},
        )

    # --- BaseIndexer interface ---

    def connect(self) -> bool:
        try:
            return self.test_connection().get("success", False)
        except Exception as exc:
            self.mark_failure(f"Connection failed: {exc}")
            return False

    def test_connection(self) -> Dict[str, Any]:
        try:
            response = self._request({"t": "caps"})
            root = ET.fromstring(response.content)
            capabilities = self._parse_capabilities(root)
            self.capabilities = capabilities
            self.mark_success()
            self._save_caps_cache(capabilities)
        except requests.exceptions.Timeout:
            error = f"Connection timeout after {self.timeout}s"
            self.mark_failure(error)
            return {"success": False, "error": error}
        except requests.exceptions.ConnectionError as exc:
            error = f"Connection error: {exc}"
            self.mark_failure(error)
            return {"success": False, "error": error}
        except ET.ParseError as exc:
            error = f"Invalid XML response: {exc}"
            self.mark_failure(error)
            return {"success": False, "error": error}
        except Exception as exc:
            error = f"Unexpected error: {exc}"
            self.logger.error("Capabilities check for %s failed", self.name, exc_info=True)
            self.mark_failure(error)
            return {"success": False, "error": error}

        search_warning = self._probe_search_endpoint()

        result: Dict[str, Any] = {
            "success": True,
            "capabilities": capabilities,
            "version": self.PROTOCOL_LABEL,
        }
        if search_warning:
            result["search_warning"] = search_warning
        return result

    def _probe_search_endpoint(self) -> Optional[str]:
        """
        Fire a minimal search request (t=search q=audiobook limit=1) to verify
        that the server handles real queries, not just capability fetches.

        Returns a warning string if the probe fails, or None on success.
        This is intentionally non-fatal: caps are sufficient for indexer
        operation; a failed probe is a yellow flag, not a red one.
        """
        try:
            response = self._request({"t": "search", "q": "audiobook", "limit": 1})
            root = ET.fromstring(response.content)
            channel = root.find("channel")
            if channel is None:
                return "Search probe returned XML with no <channel> element"
            self.logger.debug(
                "%s search probe succeeded",
                self.PROTOCOL_LABEL,
                extra={"indexer_name": self.name},
            )
            return None
        except requests.exceptions.Timeout:
            return f"Search probe timed out after {self.timeout}s"
        except requests.exceptions.ConnectionError as exc:
            return f"Search probe connection error: {exc}"
        except ET.ParseError as exc:
            return f"Search probe returned invalid XML: {exc}"
        except PermissionError:
            return "Search probe rejected (API key may lack search permission)"
        except Exception as exc:
            self.logger.debug("Search probe for %s failed", self.name, exc_info=True)
            return f"Search probe failed: {exc}"

    def get_capabilities(self) -> Dict[str, Any]:
        if self.capabilities:
            return self.capabilities
        return self.test_connection().get("capabilities", {})

    # --- Capabilities persistence helpers ---

    def _caps_cache_path(self) -> Optional[str]:
        """Return the filesystem path for this indexer's capabilities cache file."""
        try:
            from core.paths import get_path_resolver
            cache_root = get_path_resolver().get_cache_dir()
            caps_dir = os.path.join(cache_root, "indexer_caps")
            os.makedirs(caps_dir, exist_ok=True)
            key = f"{self.name}:{self.api_endpoint}"
            safe_key = hashlib.sha1(key.encode()).hexdigest()[:16]
            return os.path.join(caps_dir, f"{safe_key}.json")
        except OSError:
            self.logger.debug("Capabilities cache folder unavailable", exc_info=True)
            return None

    def _load_caps_cache(self) -> Optional[Dict[str, Any]]:
        """Load cached capabilities from disk if the entry is still fresh."""
        path = self._caps_cache_path()
        if not path or not os.path.isfile(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            saved_at = float(data.get("_saved_at", 0))
            if time.time() - saved_at > _CAPS_CACHE_TTL:
                self.logger.debug(
                    "Capabilities cache expired for %s; will re-fetch",
                    self.name,
                )
                return None
            caps = data.get("capabilities")
            return caps if isinstance(caps, dict) else None
        except (OSError, ValueError, AttributeError) as exc:
            self.logger.debug(
                "Failed to read capabilities cache for %s: %s", self.name, exc
            )
            return None

    def _save_caps_cache(self, capabilities: Dict[str, Any]) -> None:
        """Persist capabilities to disk with a timestamp for TTL checks."""
        path = self._caps_cache_path()
        if not path:
            return
        try:
            data = {"_saved_at": time.time(), "capabilities": capabilities}
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
        except (OSError, TypeError) as exc:
            self.logger.debug(
                "Failed to write capabilities cache for %s: %s", self.name, exc
            )

    # --- HTTP ---

    def _request(self, params: Dict[str, Any]) -> requests.Response:
        response = http.get(
            self.api_endpoint,
            params=self._inject_auth(params),
            timeout=self.timeout,
            verify=self.verify_ssl,
        )
        if response.status_code == 403:
            raise PermissionError("Invalid API key")
        if response.status_code == 404:
            raise FileNotFoundError(
                f"{self.PROTOCOL_LABEL} endpoint not found: {self.api_endpoint}"
            )
        if response.status_code != 200:
            raise RuntimeError(
                f"HTTP {response.status_code}: {response.text[:160]}"
            )
        return response

    def _inject_auth(self, params: Dict[str, Any]) -> Dict[str, Any]:
        merged = dict(params)
        merged.setdefault("apikey", self.api_key)
        if merged.get("t") in {"search", "book"} and self.categories:
            merged.setdefault("cat", ",".join(self.categories))
        return merged

    def _base_params(self, search_type: str) -> Dict[str, Any]:
        return {"t": search_type}

    # --- XML parsing ---

    def _parse_items(self, root: ET.Element) -> List[Any]:
        items: List[Any] = []
        for element in root.findall(".//item"):
            try:
                parsed = self._parse_single_item(element)
                if parsed:
                    items.append(parsed)
            except Exception as exc:
                self.logger.debug(
                    "Skipping malformed %s item: %s", self.PROTOCOL_LABEL, exc, exc_info=True
                )
        return items

    # --- Attribute extraction ---

    def _extract_attributes(self, element: ET.Element) -> Dict[str, str]:
        return {
            attr.get("name"): attr.get("value")
            for attr in element.findall(f"{self.PROTOCOL}:attr", self.NS)
            if attr.get("name") and attr.get("value") is not None
        }

    # --- Shared helpers ---

    def _get_text(self, parent: ET.Element, tag: str, default: str) -> str:
        child = parent.find(tag)
        return child.text.strip() if (child is not None and child.text) else default

    def _normalize_date(self, value: str) -> str:
        if not value:
            return ""
        try:
            from email.utils import parsedate_to_datetime
            return parsedate_to_datetime(value).isoformat()
        except (TypeError, ValueError):
            return value

    def _clean_query(self, query: str) -> str:
        if not query:
            return ""
        cleaned = re.sub(r"[^\w\s-]", " ", query.replace("'", ""))
        stopwords = {
            "the", "and", "or", "but", "in", "on", "at", "to", "for", "of",
            "with", "by", "from", "up", "out", "if", "about", "as", "into",
            "through", "over", "after", "before", "a", "an", "am", "is",
            "im", "its", "it",
        }
        filtered = [
            w for w in cleaned.split()
            if len(w) > 2 and w.lower() not in stopwords
        ]
        return " ".join(filtered).strip()

    def _extract_author(self, title: str) -> Optional[str]:
        dash = re.search(r"\s+-\s+([A-Z][A-Za-z\s\.,&]+?)\s*[\[(]", title)
        if dash:
            candidate = dash.group(1).strip()
            blocked = {
                "progression", "fantasy", "litrpg", "epic", "series",
                "book", "volume", "vol", "audiobook", "unabridged",
            }
            if len(candidate) < 50 and not any(
                w in candidate.lower() for w in blocked
            ):
                return candidate
        by = re.search(
            r"\s+by\s+([A-Z][a-zA-Z\s\.]+?)(?:\s+[\[(]|$)", title, re.IGNORECASE
        )
        return by.group(1).strip() if by else None
