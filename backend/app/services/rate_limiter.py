"""Login throttling.

A fixed-size sliding window per key, held in memory. Two things it deliberately is
not: distributed (there is one process) and persistent (a restart clearing the
counters is not a meaningful bypass, because the per-account lockout in the ``users``
table survives restarts and is the durable control).

Keying is by *username and client address together*. Keying on username alone would
let anyone lock a known admin out of their own portal from the outside; keying on
address alone would let one host cycle usernames freely.
"""

from __future__ import annotations

import threading
from collections import defaultdict, deque
from datetime import timedelta

from app.utils.datetime_utils import utcnow

DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_WINDOW = timedelta(minutes=15)


class RateLimiter:
    def __init__(
        self,
        *,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        window: timedelta = DEFAULT_WINDOW,
    ) -> None:
        self._max_attempts = max_attempts
        self._window = window
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> bool:
        """True if another attempt is allowed. Does not consume an attempt."""
        with self._lock:
            self._evict(key)
            return len(self._hits[key]) < self._max_attempts

    def record_failure(self, key: str) -> None:
        """Only failures count. A correct password must never consume budget."""
        with self._lock:
            self._evict(key)
            self._hits[key].append(utcnow().timestamp())

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)

    def retry_after_seconds(self, key: str) -> int:
        with self._lock:
            self._evict(key)
            hits = self._hits[key]
            if len(hits) < self._max_attempts:
                return 0
            oldest = hits[0]
            remaining = self._window.total_seconds() - (utcnow().timestamp() - oldest)
            return max(1, int(remaining))

    def _evict(self, key: str) -> None:
        cutoff = utcnow().timestamp() - self._window.total_seconds()
        hits = self._hits[key]
        while hits and hits[0] < cutoff:
            hits.popleft()
