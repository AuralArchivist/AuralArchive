"""
Module Name: base_indexer.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Common abstract base class for all indexer implementations.
    Defines the shared interface, health tracking, and enums.
    Protocol-specific logic lives in torznab_base_indexer.py,
    newznab_base_indexer.py, and direct_base_indexer.py.

Location:
    /integrations/indexers/base_indexer.py
"""

from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

from core.logging import get_module_logger
from core.http import RequestPacer


_LOGGER = get_module_logger("Service.Indexers.Base")


def _pacer_from_config(config: Dict[str, Any], logger) -> RequestPacer:
    """Request pacing and concurrency cap from the indexer's rate_limit settings."""
    settings = (config or {}).get("rate_limit") or {}
    per_second = settings.get("requests_per_second", 1)
    per_second = per_second if per_second and per_second > 0 else 1.0
    return RequestPacer(min_interval=1.0 / per_second,
                        max_concurrent=max(1, int(settings.get("max_concurrent", 1) or 1)), logger=logger)


_FAILURE_THRESHOLD = 3

_BACKOFF_STEPS_MINUTES = [15, 60, 240, 1440]


class IndexerProtocol(Enum):
    """Supported indexer protocols."""
    TORZNAB = "torznab"
    NEWZNAB = "newznab"
    DIRECT = "direct"


class IndexerType(Enum):
    """Accessibility type of an indexer."""
    PUBLIC = "public"
    PRIVATE = "private"
    SEMI_PRIVATE = "semi-private"


class BaseIndexer(ABC):
    """
    Common abstract base for all indexer implementations.

    Subclass from a protocol base (TorznabBaseIndexer, NewznabBaseIndexer,
    DirectBaseIndexer) rather than this class directly.
    """

    CATEGORY_AUDIOBOOK = "3030"
    CATEGORY_ALL = "8000"

    def __init__(self, config: Dict[str, Any], *, logger=None):
        """
        Initialise common indexer state.

        Config keys:
            name        – human-readable label
            base_url    – root URL of the indexer application
            api_key     – authentication key
            protocol    – 'torznab' | 'newznab' | 'direct'
            categories  – list of category IDs (default: [CATEGORY_AUDIOBOOK])
            timeout     – request timeout in seconds (default: 30)
            verify_ssl  – verify TLS certificates (default: True)
        """
        self.config = config
        self.name = config.get("name", self.__class__.__name__)
        self.base_url = config["base_url"].rstrip("/")
        self.api_key = config.get("api_key", "")
        self.timeout = config.get("timeout", 30)
        self.verify_ssl = config.get("verify_ssl", True)
        self.categories = config.get("categories", [self.CATEGORY_AUDIOBOOK])

        protocol_value = config.get("protocol", "torznab")
        try:
            self.protocol = IndexerProtocol(protocol_value)
        except ValueError:
            self.protocol = IndexerProtocol.TORZNAB

        self.rate_limiter = _pacer_from_config(config, logger or _LOGGER)

        self.available: bool = True
        self.last_error: Optional[str] = None
        self.last_success: Optional[datetime] = None
        self.consecutive_failures: int = 0
        self.backoff_until: Optional[datetime] = None
        self.backoff_count: int = 0

        self.capabilities: Dict[str, Any] = {}

        self.logger = logger or _LOGGER
        self.logger.debug("Initializing indexer", extra={"indexer_name": self.name, "base_url": self.base_url, "protocol": self.protocol.value})

    # --- Abstract interface - implemented by each concrete indexer ---

    @abstractmethod
    def connect(self) -> bool:
        """Verify connectivity and API key. Return True on success."""

    @abstractmethod
    def test_connection(self) -> Dict[str, Any]:
        """
        Test connection and retrieve capabilities.

        Returns dict with keys:
            success       – bool
            capabilities  – dict
            version       – str (optional)
            error         – str (on failure)
        """

    @abstractmethod
    def search(
        self,
        query: str,
        author: Optional[str] = None,
        title: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        Search for audiobooks and return normalised result dicts.

        Every result must include at minimum:
            indexer       – str  (this indexer's name)
            title         – str
            download_url  – str
            size_bytes    – int
            publish_date  – str  (ISO-8601)
            protocol      – 'torrent' | 'nzb'
            result_type   – 'torrent' | 'nzb'
            indexer_id    – str  (unique ID from indexer)
        """

    @abstractmethod
    def get_capabilities(self) -> Dict[str, Any]:
        """
        Return capabilities dict (search types, categories, limits).

        Keys:
            search_available        – bool
            book_search_available   – bool
            author_search_available – bool
            categories              – list[dict]
            limits                  – dict
        """

    # --- Health tracking (shared by all protocols) ---

    def is_available(self) -> bool:
        """Return True if the indexer isn't currently backed off.

        A backoff clears itself automatically once it elapses (rather than
        needing a manual re-enable or process restart) - the indexer gets
        one retry attempt after the cooldown, and a fresh failure there just
        starts the next (longer) backoff step.
        """
        if self.backoff_until is not None:
            if datetime.now() >= self.backoff_until:
                self.logger.info(
                    "Indexer backoff expired; eligible for retry",
                    extra={"indexer_name": self.name, "backoff_count": self.backoff_count},
                )
                self.backoff_until = None
                self.consecutive_failures = 0
                self.available = True
            else:
                return False
        return self.available

    def mark_failure(self, error: str) -> None:
        """Record a failed request; back off once consecutive failures cross
        the threshold, escalating the backoff duration on repeat offenses."""
        self.last_error = error
        self.consecutive_failures += 1
        if self.consecutive_failures >= _FAILURE_THRESHOLD:
            self.available = False
            step_index = min(self.backoff_count, len(_BACKOFF_STEPS_MINUTES) - 1)
            backoff_minutes = _BACKOFF_STEPS_MINUTES[step_index]
            self.backoff_until = datetime.now() + timedelta(minutes=backoff_minutes)
            self.backoff_count += 1
            self.logger.warning(
                "Indexer backed off after consecutive failures",
                extra={
                    "indexer_name": self.name,
                    "failures": self.consecutive_failures,
                    "backoff_minutes": backoff_minutes,
                    "backoff_until": self.backoff_until.isoformat(),
                },
            )
        self.logger.error(f"Indexer failure: {self.name} - {error}", extra={"indexer_name": self.name, "error": error})

    def mark_success(self) -> None:
        """Reset failure counters after a successful request. A real success
        also resets backoff_count, so the next failure streak starts back at
        the shortest backoff step rather than continuing to escalate."""
        self.last_error = None
        self.consecutive_failures = 0
        self.backoff_until = None
        self.backoff_count = 0
        self.available = True
        self.last_success = datetime.now()
        self.logger.debug("Indexer request successful", extra={"indexer_name": self.name})

    # --- Info / introspection ---

    def get_indexer_info(self) -> Dict[str, Any]:
        """Return a status snapshot for this indexer."""
        return {
            "name": self.name,
            "protocol": self.protocol.value,
            "base_url": self.base_url,
            "available": self.available,
            "consecutive_failures": self.consecutive_failures,
            "backoff_until": self.backoff_until.isoformat() if self.backoff_until else None,
            "backoff_count": self.backoff_count,
            "last_error": self.last_error,
            "capabilities": self.capabilities,
        }

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"name={self.name!r}, "
            f"protocol={self.protocol.value!r}, "
            f"available={self.available})"
        )
