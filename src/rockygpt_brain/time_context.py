"""One immutable clock snapshot shared by the entire turn."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from rockygpt_brain.errors import ServiceError


@dataclass(frozen=True, slots=True)
class TimeContext:
    instant: datetime
    requested_timezone: str
    campus_timezone: str
    request_local: datetime
    campus_local: datetime
    service_date: date
    service_day: str

    @classmethod
    def create(
        cls,
        *,
        pinned_now: datetime | None,
        requested_timezone: str | None,
        campus_timezone: str = "America/New_York",
        clock: Callable[[], datetime] | None = None,
    ) -> "TimeContext":
        now = pinned_now or (clock() if clock is not None else datetime.now(timezone.utc))
        if now.tzinfo is None or now.utcoffset() is None:
            raise ServiceError(400, "INVALID_REQUEST", "now must include an explicit timezone.")
        try:
            campus_zone = ZoneInfo(campus_timezone)
            request_zone = ZoneInfo(requested_timezone or campus_timezone)
        except ZoneInfoNotFoundError as exc:
            raise ServiceError(400, "INVALID_REQUEST", "timezone must be a valid IANA name.") from exc
        instant = now.astimezone(timezone.utc)
        campus_local = instant.astimezone(campus_zone)
        weekday = campus_local.weekday()
        service_day = "saturday" if weekday == 5 else "sunday" if weekday == 6 else "weekday"
        return cls(
            instant=instant,
            requested_timezone=requested_timezone or campus_timezone,
            campus_timezone=campus_timezone,
            request_local=instant.astimezone(request_zone),
            campus_local=campus_local,
            service_date=campus_local.date(),
            service_day=service_day,
        )

    @property
    def as_of(self) -> str:
        return self.instant.isoformat().replace("+00:00", "Z")
