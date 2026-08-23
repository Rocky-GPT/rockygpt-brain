"""Pinned "now" resolution.

Per spec/acceptance.md: "Pinned now and timezone values control hours and
shuttle calculations." When the caller supplies `now`, it — not wall-clock
time — is authoritative for the rest of the turn (system prompt wording and
the `at` parameter passed to hours/shuttle tools).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True, slots=True)
class TimeContext:
    now: datetime
    timezone_name: str | None

    def as_at_param(self) -> str:
        return self.now.astimezone(UTC).isoformat().replace("+00:00", "Z")

    def local_description(self) -> str:
        if not self.timezone_name:
            return self.as_at_param()
        try:
            local = self.now.astimezone(ZoneInfo(self.timezone_name))
        except ZoneInfoNotFoundError:
            return self.as_at_param()
        return f"{local.isoformat()} ({self.timezone_name})"


def resolve_time_context(*, now: datetime | None, timezone_name: str | None) -> TimeContext:
    resolved_now = now if now is not None else datetime.now(UTC)
    if resolved_now.tzinfo is None:
        resolved_now = resolved_now.replace(tzinfo=UTC)
    return TimeContext(now=resolved_now, timezone_name=timezone_name)
