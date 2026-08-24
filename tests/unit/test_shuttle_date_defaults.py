from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from conftest import FakeData
from rockygpt_brain.capabilities import ShuttleCapability
from rockygpt_brain.evidence import EvidenceRegistry
from rockygpt_brain.planning import ShuttleIntent, ShuttleSelection, ShuttleTimeScope
from rockygpt_brain.time_context import TimeContext


def _split_calendar_context() -> TimeContext:
    # Sunday just after midnight on campus is still Saturday for a Los Angeles caller.
    return TimeContext.create(
        pinned_now=datetime(2026, 8, 23, 4, 30, tzinfo=UTC),
        requested_timezone="America/Los_Angeles",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("selection", "scope"),
    [
        (ShuttleSelection.NEXT, ShuttleTimeScope.REMAINING),
        (ShuttleSelection.CURRENT, ShuttleTimeScope.AT_TIME),
    ],
)
async def test_unqualified_current_time_query_defaults_to_campus_calendar(
    selection: ShuttleSelection,
    scope: ShuttleTimeScope,
) -> None:
    time = _split_calendar_context()
    data = FakeData()
    capability = ShuttleCapability(data)

    await capability.execute(
        ShuttleIntent(selection=selection, timeScope=scope),
        time,
        EvidenceRegistry(),
    )

    assert time.request_date == date(2026, 8, 22)
    assert time.campus_date == date(2026, 8, 23)
    assert data.queries[-1].service_date == date(2026, 8, 23)
    assert data.queries[-1].service_day == "sunday"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("relative_days", "expected_date", "expected_day"),
    [
        (0, date(2026, 8, 22), "saturday"),
        (1, date(2026, 8, 23), "sunday"),
    ],
)
async def test_explicit_relative_date_uses_caller_calendar(
    relative_days: int,
    expected_date: date,
    expected_day: str,
) -> None:
    time = _split_calendar_context()
    data = FakeData()
    capability = ShuttleCapability(data)
    explicit_date = date.fromordinal(time.request_date.toordinal() + relative_days)

    await capability.execute(
        ShuttleIntent(
            serviceDate=explicit_date,
            selection=ShuttleSelection.NEXT,
            timeScope=ShuttleTimeScope.REMAINING,
        ),
        time,
        EvidenceRegistry(),
    )

    assert data.queries[-1].service_date == expected_date
    assert data.queries[-1].service_day == expected_day
