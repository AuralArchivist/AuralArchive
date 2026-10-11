"""
Module Name: connection.py
Author: AuralArchivist
Created: July 1, 2026
Last Modified: October 9, 2026
Description:
    GraphQL client for the Hardcover API, token from ConfigService.

Location:
    /integrations/hardcover/connection.py
"""

import threading
import time
from typing import Any, Dict, Optional

import requests

from core.logging import get_module_logger
from core.http import HttpSession

HARDCOVER_ENDPOINT = "https://api.hardcover.app/v1/graphql"

STATUS_WANT_TO_READ = 1
STATUS_CURRENTLY_READING = 2
STATUS_READ = 3
STATUS_PAUSED = 4
STATUS_DID_NOT_FINISH = 5
STATUS_IGNORED = 6

STATUS_LABELS = {
    STATUS_WANT_TO_READ: "Want to Read",
    STATUS_CURRENTLY_READING: "Currently Reading",
    STATUS_READ: "Read",
    STATUS_PAUSED: "Paused",
    STATUS_DID_NOT_FINISH: "Did Not Finish",
    STATUS_IGNORED: "Ignored",
}

AUDIO_READING_FORMAT_IDS = (2, 3)

_LOGGER = get_module_logger("Integration.Hardcover.Connection")


class HardcoverAPIError(Exception):
    """Raised for any non-recoverable Hardcover API failure (auth, rate limit, transport)."""


class HardcoverAuthError(HardcoverAPIError):
    """Raised when the stored token is missing, expired, or rejected (HTTP 401)."""


class HardcoverRateLimitError(HardcoverAPIError):
    """Raised on HTTP 429 - caller should back off and retry later."""


def normalize_auth_header(token: str) -> str:
    token = (token or "").strip()
    if token.lower().startswith("bearer "):
        return token
    return f"Bearer {token}"


class HardcoverClient:
    """Minimal GraphQL client. All clients share one 1 request/second throttle
    (Hardcover allows 60/min), and a 429 is retried after a pause."""

    _throttle_lock = threading.Lock()
    _last_request_ts: float = 0.0
    RATE_LIMIT_RETRIES = 2

    def __init__(self, token: str, timeout: int = 30, *, logger=None):
        if not token or not token.strip():
            raise HardcoverAuthError("No Hardcover API token configured")

        self.logger = logger or _LOGGER
        self.session = HttpSession(headers={
            "authorization": normalize_auth_header(token),
            "Content-Type": "application/json",
            "User-Agent": "AuralArchive/1.0 (+https://github.com/; Hardcover integration)",
        })
        self.timeout = timeout

    def query(self, query: str, variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Execute a GraphQL query/mutation. Returns the parsed `data` dict.

        Raises HardcoverAuthError / HardcoverRateLimitError / HardcoverAPIError
        on failure (including a non-empty `errors` array in the response).
        """
        for attempt in range(self.RATE_LIMIT_RETRIES + 1):
            resp = self._post(query, variables)
            if resp.status_code != 429:
                break
            if attempt == self.RATE_LIMIT_RETRIES:
                raise HardcoverRateLimitError("Hardcover rate limit hit (429)")
            wait = self._retry_after(resp)
            self.logger.info("Hardcover rate limit hit; retrying in %ss", wait)
            time.sleep(wait)

        if resp.status_code == 401:
            raise HardcoverAuthError("Hardcover rejected the API token (401)")
        if resp.status_code >= 500:
            raise HardcoverAPIError(f"Hardcover server error (HTTP {resp.status_code})")

        try:
            payload = resp.json()
        except ValueError as exc:
            raise HardcoverAPIError(f"Non-JSON response from Hardcover (HTTP {resp.status_code})") from exc

        if payload.get("errors"):
            self.logger.warning("Hardcover GraphQL errors", extra={"errors": payload["errors"]})
            raise HardcoverAPIError(str(payload["errors"]))

        return payload.get("data") or {}

    def _post(self, query: str, variables: Optional[Dict[str, Any]]):
        with HardcoverClient._throttle_lock:
            elapsed = time.monotonic() - HardcoverClient._last_request_ts
            if elapsed < 1.0:
                time.sleep(1.0 - elapsed)
            HardcoverClient._last_request_ts = time.monotonic()
        try:
            return self.session.post(
                HARDCOVER_ENDPOINT,
                json={"query": query, "variables": variables or {}},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise HardcoverAPIError(f"Network error contacting Hardcover: {exc}") from exc

    @staticmethod
    def _retry_after(resp) -> float:
        try:
            return min(60.0, max(1.0, float(resp.headers.get("Retry-After", 10))))
        except (TypeError, ValueError):
            return 10.0

    def whoami(self) -> Dict[str, Any]:
        """Validate the token and return {'id': int, 'username': str}."""
        data = self.query("query { me { id username } }")
        me_list = data.get("me") or []
        if not me_list:
            raise HardcoverAuthError("Hardcover returned no user for this token")
        return me_list[0]


def get_client(config_service) -> HardcoverClient:
    """Build a HardcoverClient using the token stored via ConfigService.

    Raises HardcoverAuthError if no token has been saved yet.
    """
    hc_config = config_service.get_hardcover_config()
    token = (hc_config or {}).get("token", "")
    return HardcoverClient(token)
