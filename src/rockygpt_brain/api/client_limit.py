"""Questions a minute per student, counted once for the whole student site (audit item 12).

The student site counts each tab's questions in its own server process's memory, and Vercel runs
it in many processes at once, so its limit held only within one of them (audit 11-17: "the limit
also lives in one process's memory"). The Brain runs as one process on Render, so a count kept
here is the whole site's. The site sends the keys it already derives with its own secret: the
tab's (x-rockygpt-client-key, the address alone when the page sends no tab value) and the network
address's (x-rockygpt-network-key). Neither says who anyone is, and neither is logged or stored.
Only the site can send them: every /v1/ request must carry the service token first.

The limits are the site's own: 12 a minute for a tab, 120 a minute for one network address, so a
dorm behind a handful of addresses is not held to one student's twelve.
"""

import re
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

CLIENT_HEADER = "x-rockygpt-client-key"
NETWORK_HEADER = "x-rockygpt-network-key"
WINDOW_SECONDS = 60.0
CLIENT_LIMIT = 12
NETWORK_LIMIT = 120
MAX_KEYS = 10_000
# The site's keys are hex HMAC-SHA256 digests. Anything else is not counted.
KEY = re.compile(r"^[0-9a-f]{64}$")


@dataclass
class _Window:
    count: int
    resets_at: float


class ClientLimit:
    """Fixed one-minute windows per key, in memory, at most MAX_KEYS of them (oldest go first)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._windows: OrderedDict[str, _Window] = OrderedDict()

    def _window(self, key: str, now: float) -> _Window:
        window = self._windows.get(key)
        if window is None or window.resets_at <= now:
            window = _Window(0, now + WINDOW_SECONDS)
        return window

    def take(self, client: str | None, network: str | None) -> int | None:
        """Counts one question, or returns the whole seconds until it may be asked."""
        now = self._clock()
        checks = [(f"{kind}:{key}", limit) for kind, key, limit in (
            ("client", client, CLIENT_LIMIT), ("network", network, NETWORK_LIMIT))
            if key and KEY.match(key)]
        windows = [(name, self._window(name, now), limit) for name, limit in checks]
        full = [window for _, window, limit in windows if window.count >= limit]
        if full:
            return max(1, int(max(window.resets_at for window in full) - now + 0.999))
        for name, window, _ in windows:
            window.count += 1
            self._windows[name] = window
            self._windows.move_to_end(name)
        while len(self._windows) > MAX_KEYS:
            self._windows.popitem(last=False)
        return None
