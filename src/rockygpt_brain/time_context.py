"""One immutable clock snapshot shared by the entire turn."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from rockygpt_brain.errors import ServiceError


@dataclass(frozen=True, slots=True)
class TimeContext:
    instant: datetime
    requested_timezone: str
    campus_timezone: str
    request_local: datetime
    campus_local: datetime
    request_date: date
    campus_date: date
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
    ) -> TimeContext:
        now = pinned_now or (clock() if clock is not None else datetime.now(UTC))
        if now.tzinfo is None or now.utcoffset() is None:
            raise ServiceError(400, "INVALID_REQUEST", "now must include an explicit timezone.")
        try:
            campus_zone = ZoneInfo(campus_timezone)
            request_zone = ZoneInfo(requested_timezone or campus_timezone)
        except ZoneInfoNotFoundError as exc:
            raise ServiceError(
                400, "INVALID_REQUEST", "timezone must be a valid IANA name."
            ) from exc
        instant = now.astimezone(UTC)
        campus_local = instant.astimezone(campus_zone)
        request_local = instant.astimezone(request_zone)
        # An omitted shuttle date defaults to the campus calendar. Explicit relative
        # dates are resolved by UNDERSTAND from request_date and travel in the intent.
        service_date = campus_local.date()
        weekday = service_date.weekday()
        service_day = "saturday" if weekday == 5 else "sunday" if weekday == 6 else "weekday"
        return cls(
            instant=instant,
            requested_timezone=requested_timezone or campus_timezone,
            campus_timezone=campus_timezone,
            request_local=request_local,
            campus_local=campus_local,
            request_date=request_local.date(),
            campus_date=campus_local.date(),
            service_date=service_date,
            service_day=service_day,
        )

    @property
    def as_of(self) -> str:
        return self.instant.isoformat().replace("+00:00", "Z")
