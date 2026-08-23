"""In-process fixed-window rate limiter.

Single-instance-correct by design; see DESIGN.md §7 for the documented
multi-instance limitation. Two layers are applied by callers: a per-identity
window (keyed by the trusted client key, or a coarse fallback identity for
unsigned callers) and a global window as a defense-in-depth ceiling.

The per-key window map is bounded by `max_keys` with least-recently-used
eviction, so a high-cardinality stream of distinct (signed or fallback)
identities cannot grow the map without bound.
"""

from __future__ import annotations

import math
import time
from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    allowed: bool
    retry_after_seconds: int


class FixedWindowRateLimiter:
    def __init__(self, *, limit: int, window_seconds: int, max_keys: int = 50_000) -> None:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        if window_seconds < 1:
            raise ValueError("window_seconds must be >= 1")
        if max_keys < 1:
            raise ValueError("max_keys must be >= 1")
        self._limit = limit
        self._window_seconds = window_seconds
        self._max_keys = max_keys
        self._lock = Lock()
        # key -> (window_start_epoch, count_in_window), ordered by recency.
        self._windows: OrderedDict[str, tuple[float, int]] = OrderedDict()

    def check(self, key: str, *, now: float | None = None) -> RateLimitResult:
        current_time = now if now is not None else time.monotonic()
        with self._lock:
            existing = self._windows.get(key)
            window_start, count = existing if existing is not None else (current_time, 0)
            elapsed = current_time - window_start
            if elapsed >= self._window_seconds:
                window_start, count = current_time, 0
                elapsed = 0.0

            if count >= self._limit:
                if existing is not None:
                    self._windows.move_to_end(key)
                retry_after = max(1, math.ceil(self._window_seconds - elapsed))
                return RateLimitResult(allowed=False, retry_after_seconds=retry_after)

            if existing is not None:
                self._windows.move_to_end(key)
            elif len(self._windows) >= self._max_keys:
                self._windows.popitem(last=False)
            self._windows[key] = (window_start, count + 1)
            return RateLimitResult(allowed=True, retry_after_seconds=0)

    def reset(self) -> None:
        with self._lock:
            self._windows.clear()
