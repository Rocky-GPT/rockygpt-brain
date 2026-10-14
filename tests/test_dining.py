"""Meal periods read from a dining venue's weekday text, and what is on at a time."""

import pytest

from rockygpt_brain.dining import after, at, clock_text, named, parse

WEEKDAY = ("Breakfast: 08:00 AM - 10:30 AM; Continental: 10:30 AM - 11:00 AM; Lunch: 11:00 AM - "
           "02:00 PM; Lite Lunch: 02:00 PM - 05:00 PM; Dinner: 05:00 PM - 08:00 PM; "
           "Late Night: 09:00 PM - 11:00 PM")


def minute(text: str) -> int:
    hour, rest = text.split(":")
    return (int(hour) % 12 + (12 if rest.endswith("pm") else 0)) * 60 + int(rest[:2])


def test_a_labelled_day_is_read_into_its_periods() -> None:
    periods = parse(WEEKDAY)
    assert periods is not None and [p.label for p in periods] == [
        "Breakfast", "Continental", "Lunch", "Lite Lunch", "Dinner", "Late Night"]
    assert named(periods)[0] == {"label": "Breakfast", "opens": "8:00am", "closes": "10:30am"}
    assert named(periods)[-1] == {"label": "Late Night", "opens": "9:00pm", "closes": "11:00pm"}


def test_a_single_unlabelled_range_and_closed_are_read() -> None:
    (period,) = parse("07:30 AM - 03:00 PM") or []
    assert (period.label, period.start, period.end) == (None, 450, 900)
    assert parse("Closed") == [] and parse("Closed (seasonal closure)") == []


@pytest.mark.parametrize("text", [
    "Hours unavailable", "", "By appointment", "Breakfast: 08:00 AM - 10:30 AM; Lunch",
    "08:00 AM - 10:30 AM, open late", "10:00 PM - 02:00 AM", "Dinner: 5-8 PM",
])
def test_text_that_is_not_wholly_periods_is_left_unread(text: str) -> None:
    assert parse(text) is None


def test_a_label_with_words_and_commas_is_kept_whole() -> None:
    (period,) = parse(
        "DINNER, ending service early for Festival of Frights event: 05:00 PM - 07:00 PM") or []
    assert period.label == "DINNER, ending service early for Festival of Frights event"


def test_what_is_on_at_each_minute_is_half_open() -> None:
    periods = parse(WEEKDAY) or []
    assert at(periods, minute("7:59am")) is None
    assert (at(periods, minute("8:00am")) or periods[0]).label == "Breakfast"
    assert (at(periods, minute("10:29am")) or periods[0]).label == "Breakfast"
    assert (at(periods, minute("10:30am")) or periods[0]).label == "Continental"
    # The unserved hour between dinner and late night.
    assert at(periods, minute("8:00pm")) is None
    gap = after(periods, minute("8:30pm"))
    assert gap is not None and gap.label == "Late Night"
    assert at(periods, minute("11:00pm")) is None and after(periods, minute("11:00pm")) is None


def test_midnight_as_a_closing_time_is_the_end_of_the_day() -> None:
    (period,) = parse("08:00 AM - 12:00 AM") or []
    assert period.end == 1440
    assert at([period], 1439) is period and at([period], 0) is None
    assert clock_text(0) == "12:00am" and clock_text(1440) == "12:00am"
    assert clock_text(12 * 60) == "12:00pm" and clock_text(13 * 60 + 5) == "1:05pm"
