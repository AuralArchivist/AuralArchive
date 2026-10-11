"""
Module Name: rate_limit.py
Author: AuralArchivist
Created: September 23, 2026
Last Modified: October 9, 2026
Description:
    Minimal in-memory sliding-window rate limiter for sensitive endpoints
    (login, setup, Audible authentication). Single-process only, which matches
    how AuralArchive runs (one server process).

Location:
    /core/rate_limit.py
"""

import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict


class SlidingWindowLimiter:
    """Allow at most ``max_attempts`` hits per ``key`` within ``window_seconds``."""

    def __init__(self, max_attempts: int, window_seconds: int):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, hits: Deque[float], now: float) -> None:
        cutoff = now - self.window_seconds
        while hits and hits[0] < cutoff:
            hits.popleft()

    def is_limited(self, key: str) -> bool:
        """True if ``key`` has already used up its attempts in the current window."""
        now = time.time()
        with self._lock:
            hits = self._hits[key]
            self._prune(hits, now)
            if not hits:
                self._hits.pop(key, None)
                return False
            return len(hits) >= self.max_attempts

    def hit(self, key: str) -> None:
        """Record one attempt for ``key``."""
        now = time.time()
        with self._lock:
            hits = self._hits[key]
            self._prune(hits, now)
            hits.append(now)

    def reset(self, key: str) -> None:
        """Forget all attempts for ``key`` (e.g. after a successful login)."""
        with self._lock:
            self._hits.pop(key, None)

    def retry_after(self, key: str) -> int:
        """Seconds until the oldest recorded attempt for ``key`` leaves the window."""
        with self._lock:
            hits = self._hits.get(key)
            if not hits:
                return 0
            return max(0, int(hits[0] + self.window_seconds - time.time()) + 1)
