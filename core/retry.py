"""
Module Name: retry.py
Author: AuralArchivist
Created: October 9, 2026
Last Modified: October 9, 2026
Description:
    The one retry loop: run a call up to N times, waiting delay x attempt
    between tries, for errors the caller says are worth retrying.

Location:
    /core/retry.py
"""

import time
from typing import Callable, Optional, TypeVar

T = TypeVar("T")


def run_with_retry(
    call: Callable[[], T],
    *,
    attempts: int,
    delay: float,
    retry_on: Callable[[Exception], bool],
    on_retry: Optional[Callable[[Exception, int, float], None]] = None,
) -> T:
    """Return call(); re-raise the last error once attempts run out or retry_on says no."""
    for attempt in range(1, attempts + 1):
        try:
            return call()
        except Exception as exc:
            if attempt >= attempts or not retry_on(exc):
                raise
            wait = delay * attempt
            if on_retry:
                on_retry(exc, attempt, wait)
            time.sleep(wait)
    raise RuntimeError("run_with_retry needs at least one attempt")
