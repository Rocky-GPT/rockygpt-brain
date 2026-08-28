from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

ScheduleStatusReason = Literal[
    "open",
    "before_first_open",
    "between_windows",
    "after_last_close",
    "closed_all_day",
    "unknown",
]


@dataclass
class Window:
    start: int
    end: int


@dataclass
class ScheduleStatus:
    status_reason: ScheduleStatusReason
    open_now: bool | None = None
    opens_at: str | None = None
    closes_at: str | None = None

    def to_dict(self) -> dict[str, object]:
        res: dict[str, object] = {"statusReason": self.status_reason}
        if self.open_now is not None:
            res["openNow"] = self.open_now
        if self.opens_at is not None:
            res["opensAt"] = self.opens_at
        if self.closes_at is not None:
            res["closesAt"] = self.closes_at
        return res


MINUTES_PER_DAY = 24 * 60
TIME_RE = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s*m\.?", re.IGNORECASE)
SEPARATOR_RE = re.compile(r"\s+and\s+|;|,", re.IGNORECASE)


def to_minutes(hour: int, minute: int, meridiem: str) -> int:
    base = hour % 12
    return ((base + 12) if meridiem.lower() == "p" else base) * 60 + minute


def format_minutes(minutes: int) -> str:
    normalized = ((minutes % MINUTES_PER_DAY) + MINUTES_PER_DAY) % MINUTES_PER_DAY
    hour24 = normalized // 60
    minute = normalized % 60
    hour12 = 12 if hour24 % 12 == 0 else hour24 % 12
    meridiem = "AM" if hour24 < 12 else "PM"
    return f"{hour12}:{minute:02d} {meridiem}"


def parse_schedule(schedule: str) -> list[Window] | None:
    text = (schedule or "").strip()
    if not text:
        return None
    if re.match(r"^closed\b", text, re.IGNORECASE):
        return []
    if re.match(r"^unknown\b", text, re.IGNORECASE):
        return None

    windows: list[Window] = []
    for segment in SEPARATOR_RE.split(text):
        matches = list(TIME_RE.finditer(segment))
        if len(matches) != 2:
            continue
        open_time = to_minutes(
            int(matches[0].group(1)), int(matches[0].group(2) or 0), matches[0].group(3)
        )
        close_time = to_minutes(
            int(matches[1].group(1)), int(matches[1].group(2) or 0), matches[1].group(3)
        )
        windows.append(Window(start=open_time, end=close_time))

    if not windows:
        return None
    return sorted(windows, key=lambda w: w.start)


def covers(window: Window, minutes: int) -> bool:
    if window.end > window.start:
        return window.start <= minutes < window.end
    return minutes >= window.start or minutes < window.end


def schedule_status_at(schedule: str, minutes: int) -> ScheduleStatus:
    windows = parse_schedule(schedule)
    if windows is None:
        return ScheduleStatus(status_reason="unknown")
    if len(windows) == 0:
        return ScheduleStatus(status_reason="closed_all_day", open_now=False)

    for w in windows:
        if covers(w, minutes):
            return ScheduleStatus(
                status_reason="open",
                open_now=True,
                closes_at=format_minutes(w.end),
            )

    upcoming = next((w for w in windows if w.start > minutes), None)
    if not upcoming:
        return ScheduleStatus(status_reason="after_last_close", open_now=False)

    reason: ScheduleStatusReason = (
        "before_first_open" if upcoming == windows[0] else "between_windows"
    )
    return ScheduleStatus(
        status_reason=reason,
        open_now=False,
        opens_at=format_minutes(upcoming.start),
    )
