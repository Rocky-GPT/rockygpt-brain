"""Picking the asked day out of a schedule is done by code, with no judgement left for a writer."""

from datetime import date

import pytest

from rockygpt_brain.hours_on import DAY_CHOICES, covers, hours_on, target_date, window

WEEK = {"schedule": "Testing Center",
        "days": [{"day": d, "hours": "8:30am-4:30pm"} for d in
                 ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")]
        + [{"day": "Saturday", "hours": "Hours unavailable"},
           {"day": "Sunday", "hours": "Hours unavailable"}],
        "notes": ["Fall and Spring Monday to Friday, 8:30 a.m. to 4:30 p.m."]}
FALL = [{"valid_from": "2026-08-26", "valid_until": "2026-12-16"}]
WEDNESDAY = date(2026, 10, 7)  # the day this was built


def test_a_day_word_becomes_a_date_on_the_campus_calendar() -> None:
    assert target_date("today", WEDNESDAY) == WEDNESDAY
    assert target_date("tomorrow", WEDNESDAY) == date(2026, 10, 8)
    assert target_date("saturday", WEDNESDAY) == date(2026, 10, 10)
    assert target_date("monday", WEDNESDAY) == date(2026, 10, 12)
    # A weekday asked about on that weekday is today, not next week.
    assert target_date("wednesday", WEDNESDAY) == WEDNESDAY
    assert target_date("tuesday", WEDNESDAY) == date(2026, 10, 13)
    assert target_date("tomorrow", date(2026, 12, 31)) == date(2027, 1, 1)
    assert len(DAY_CHOICES) == 9 and DAY_CHOICES[:2] == ("today", "tomorrow")


@pytest.mark.parametrize("day", DAY_CHOICES)
def test_every_day_word_lands_within_the_next_seven_days(day: str) -> None:
    for offset in range(14):
        start = date(2026, 10, 1 + offset)
        found = target_date(day, start)
        assert 0 <= (found - start).days <= 7
        if day not in {"today", "tomorrow"}:
            assert found.strftime("%A").lower() == day


def test_the_hours_for_the_day_are_read_from_the_schedule_as_published() -> None:
    friday = hours_on(WEEK, FALL, date(2026, 10, 9))
    assert friday == {"day": "Friday", "date": "2026-10-09", "applies": True, "value": {
        "schedule": "Testing Center", "hours": "8:30am-4:30pm",
        "notes": ["Fall and Spring Monday to Friday, 8:30 a.m. to 4:30 p.m."]}}
    saturday = hours_on(WEEK, FALL, date(2026, 10, 10))
    # What the source says, not "closed".
    assert saturday["value"]["hours"] == "Hours unavailable" and saturday["applies"] is True


def test_a_weekday_the_schedule_does_not_list_has_no_hours_here() -> None:
    partial = {"schedule": "Lab", "days": [{"day": "Monday", "hours": "9-5"}]}
    result = hours_on(partial, FALL, date(2026, 10, 10))
    assert result["value"] == {"schedule": "Lab", "hours": None} and result["applies"] is True


def test_a_date_outside_the_published_window_gets_the_window_and_no_hours() -> None:
    result = hours_on(WEEK, FALL, date(2026, 12, 17))
    assert result["applies"] is False
    assert result["value"]["hours"] is None
    assert result["value"]["window"] == {"from": "2026-08-26", "until": "2026-12-16"}
    assert hours_on(WEEK, FALL, date(2026, 8, 25))["applies"] is False
    assert hours_on(WEEK, FALL, date(2026, 8, 26))["applies"] is True
    assert hours_on(WEEK, FALL, date(2026, 12, 16))["applies"] is True


def test_open_ended_and_unpublished_windows() -> None:
    assert covers([{"valid_from": "2026-08-26"}], date(2030, 1, 1))
    assert not covers([{"valid_from": "2026-08-26"}], date(2026, 8, 1))
    assert covers([{"valid_until": "2026-12-16"}], date(2020, 1, 1))
    assert covers([{}], date(2026, 10, 7)) and covers([{"valid_from": None}], date(2026, 10, 7))
    # A source whose window is malformed publishes no window, so it speaks for every day.
    assert covers([{"valid_from": "soon", "valid_until": "later"}], date(2026, 10, 7))
    # Several sources: any one that covers the day is enough.
    assert covers([*FALL, {"valid_from": "2027-01-01"}], date(2027, 2, 1))
    assert covers([{"valid_from": "2026-08-26T00:00:00-04:00",
                    "valid_until": "2026-12-16T00:00:00-05:00"}], date(2026, 12, 16))
    assert window([{}]) == {} and window(FALL) == {"from": "2026-08-26", "until": "2026-12-16"}


def test_notes_are_passed_on_only_when_the_schedule_has_them() -> None:
    plain = {"schedule": "Lab", "days": [{"day": "Friday", "hours": "9-5"}]}
    assert "notes" not in hours_on(plain, FALL, date(2026, 10, 9))["value"]
    assert hours_on(WEEK, FALL, date(2026, 10, 9))["value"]["notes"] == WEEK["notes"]


SEASONAL = {"schedule": "Testing Center", "season": "Fall and Spring",
            "days": [{"day": d, "hours": "8:30am-4:30pm"} for d in
                     ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")]
            + [{"day": "Saturday", "hours": None, "status": "not_published",
                "absence": {"checks": [], "current": True}},
               {"day": "Sunday", "hours": None}]}
NO_DATES = [{"valid_from": None, "valid_until": None}]


def test_a_seasonal_schedule_with_no_dates_lists_its_weekday_with_no_hours_for_the_date() -> None:
    friday = hours_on(SEASONAL, NO_DATES, date(2026, 10, 9))
    assert friday["applies"] is False and friday["applicability"] == "unverified"
    # Nothing says the season covers this date, so there are no hours for it...
    assert friday["value"]["hours"] is None
    # ...but the student can be told what the schedule lists for that weekday.
    assert friday["value"]["listed_hours"] == "8:30am-4:30pm"
    assert friday["value"]["season"] == "Fall and Spring" and "window" not in friday["value"]


def test_a_weekday_a_seasonal_schedule_does_not_list_is_said_so_whatever_the_dates() -> None:
    saturday = hours_on(SEASONAL, NO_DATES, date(2026, 10, 10))
    assert saturday["value"]["hours"] is None and saturday["value"]["listed_hours"] is None
    # A reviewed omission is a statement about the schedule, not about a date.
    assert saturday["value"]["status"] == "not_published"
    assert saturday["value"]["absence"] == {"checks": [], "current": True}
    sunday = hours_on(SEASONAL, NO_DATES, date(2026, 10, 11))
    assert sunday["value"]["listed_hours"] is None and "status" not in sunday["value"]


def test_a_date_known_to_be_outside_the_window_never_lists_hours() -> None:
    fall = {**SEASONAL, "days": [{"day": "Friday", "hours": "8:30am-4:30pm"}]}
    out = hours_on(fall, FALL, date(2027, 1, 8))
    assert out["applies"] is False and "applicability" not in out
    assert "listed_hours" not in out["value"] and out["value"]["window"]
    # With both dates published and the date inside them, the hours are the answer.
    inside = hours_on(fall, FALL, date(2026, 10, 9))
    assert inside["applies"] is True and inside["value"]["hours"] == "8:30am-4:30pm"
    assert "listed_hours" not in inside["value"]
