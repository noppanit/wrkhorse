"""Constant-time bearer check and a small in-process rate limit."""

from __future__ import annotations

import hmac
import time
from collections import deque


def tokens_equal(presented: str, expected: str) -> bool:
    left = presented.encode("utf-8")
    right = expected.encode("utf-8")
    if len(left) != len(right):
        return False
    return hmac.compare_digest(left, right)


def bearer_matches(header: str, expected: str) -> bool:
    if header.count(" ") != 1:
        return False
    scheme, presented = header.split(" ", 1)
    if not tokens_equal(scheme.lower(), "bearer"):
        return False
    return tokens_equal(presented, expected)


class RateLimiter:
    """Sliding-window log of request times per client. Not shared across processes.

    A client's entry is removed once its window empties, so memory is bounded by
    the clients seen in the last window rather than every client ever seen.
    """

    def __init__(self, max_calls: int, window_seconds: float) -> None:
        if max_calls < 1:
            raise ValueError("rate limit must be at least 1")
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = {}
        self._last_sweep = time.monotonic()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        self._evict_idle(now)
        hits = self._hits.setdefault(key, deque())
        while hits and now - hits[0] > self.window_seconds:
            hits.popleft()
        if len(hits) >= self.max_calls:
            return False
        hits.append(now)
        return True

    def _evict_idle(self, now: float) -> None:
        # At most one full sweep per window keeps allow() O(1) amortized.
        if now - self._last_sweep < self.window_seconds:
            return
        self._last_sweep = now
        stale = [k for k, h in self._hits.items() if not h or now - h[-1] > self.window_seconds]
        for k in stale:
            del self._hits[k]
