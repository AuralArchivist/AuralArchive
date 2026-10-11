"""
Module Name: http.py
Author: AuralArchivist
Created: October 9, 2026
Last Modified: October 9, 2026
Description:
    Shared HTTP plumbing for outside services: a pacer that spaces requests
    out, and a requests.Session with a default timeout, optional pacing and
    retries on connection errors and timeouts.

Location:
    /core/http.py
"""

import threading
import time
from typing import Any, Dict, Optional

import requests

from core.logging import get_module_logger
from core.retry import run_with_retry

_LOGGER = get_module_logger("Core.Http")

DEFAULT_TIMEOUT = 30
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_9_3) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/35.0.1916.47 Safari/537.36"
)


class RequestPacer:
    """Spaces request starts at least min_interval apart and optionally caps how
    many run at once. Thread-safe; use as a context manager around each request."""

    def __init__(self, min_interval: float = 0.0, max_concurrent: Optional[int] = None, *, logger=None):
        self.min_interval = max(0.0, float(min_interval or 0.0))
        self.logger = logger or _LOGGER
        self._slots = threading.Semaphore(max_concurrent) if max_concurrent else None
        self._lock = threading.Lock()
        self._last_start = 0.0

    def __enter__(self) -> "RequestPacer":
        if self._slots:
            self._slots.acquire()
        with self._lock:
            wait = self.min_interval - (time.monotonic() - self._last_start)
            if wait > 0:
                self.logger.debug("Pacing request", extra={"wait_seconds": round(wait, 2)})
                time.sleep(wait)
            self._last_start = time.monotonic()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if self._slots:
            self._slots.release()
        return False


def is_transient_network_error(exc: Exception) -> bool:
    return isinstance(exc, (requests.exceptions.ConnectionError, requests.exceptions.Timeout))


class HttpSession(requests.Session):
    """requests.Session with a default timeout, an optional pacer, and retries
    (attempts > 1) on connection errors and timeouts."""

    def __init__(self, *, timeout: float = DEFAULT_TIMEOUT, headers: Optional[Dict[str, str]] = None,
                 pacer: Optional[RequestPacer] = None, attempts: int = 1, retry_delay: float = 1.0,
                 logger=None) -> None:
        super().__init__()
        self.default_timeout = timeout
        self.pacer = pacer
        self.attempts = max(1, attempts)
        self.retry_delay = retry_delay
        self.logger = logger or _LOGGER
        if headers:
            self.headers.update(headers)

    def request(self, method: str, url: str, *args: Any, **kwargs: Any) -> requests.Response:
        kwargs.setdefault("timeout", self.default_timeout)
        send = super().request

        def attempt() -> requests.Response:
            if self.pacer is None:
                return send(method, url, *args, **kwargs)
            with self.pacer:
                return send(method, url, *args, **kwargs)

        return run_with_retry(attempt, attempts=self.attempts, delay=self.retry_delay,
                              retry_on=is_transient_network_error, on_retry=self._log_retry)

    def _log_retry(self, exc: Exception, attempt: int, wait: float) -> None:
        self.logger.warning("Network error; retrying",
                            extra={"error": type(exc).__name__, "attempt": attempt, "delay": wait})


def request(method: str, url: str, **kwargs: Any) -> requests.Response:
    """One-off request on a fresh HttpSession, like requests.request."""
    with HttpSession() as session:
        return session.request(method, url, **kwargs)


def get(url: str, params: Any = None, **kwargs: Any) -> requests.Response:
    return request("GET", url, params=params, **kwargs)


def post(url: str, data: Any = None, json: Any = None, **kwargs: Any) -> requests.Response:
    return request("POST", url, data=data, json=json, **kwargs)
