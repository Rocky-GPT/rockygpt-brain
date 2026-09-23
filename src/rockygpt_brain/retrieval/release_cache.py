"""Structures derived from one published release, shared across development requests.

A published release is immutable, so its validated identity registry, identity hash
and graph index depend only on its artifacts. Each is built once per release and
reused until the active release or any artifact's content hash changes. Values that
depend on the clock, such as record freshness, are never cached here.

Without a live database connection there is no release to fingerprint (test
repositories inject artifacts directly), so nothing is cached and every value is
built on demand as before.
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable
from typing import Any, TypeVar

T = TypeVar("T")
# A few releases at most: the active one, plus any a deploy is switching between.
LIMIT = 4
_entries: OrderedDict[tuple[Any, ...], Any] = OrderedDict()
_lock = threading.Lock()


def cached(data: Any, name: str, build: Callable[[], T]) -> T:
    """`build()` once per release fingerprint; concurrent first builds may both run."""
    fingerprint = data.release_fingerprint()
    if fingerprint is None:
        return build()
    key = (name, *fingerprint)
    with _lock:
        if key in _entries:
            _entries.move_to_end(key)
            value: T = _entries[key]
            return value
    value = build()
    with _lock:
        _entries[key] = value
        _entries.move_to_end(key)
        while len(_entries) > LIMIT:
            _entries.popitem(last=False)
    return value


def clear() -> None:
    with _lock:
        _entries.clear()
