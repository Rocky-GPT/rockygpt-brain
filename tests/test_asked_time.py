"""The read's time picks turned into campus days by code (asked_time.py), and where used."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from rockygpt_brain import asked_time, events, triage
from rockygpt_brain.academic_calendar import kind_asked, kind_named, term_asked, term_spans
from rockygpt_brain.asked_time import DEFAULT_LABEL, AskedTime, of, vote, window
from rockygpt_brain.teams import answer
from test_academic_calendar import DATES, TERMS
from test_varsity_teams import TEAMS, game, team

EASTERN = ZoneInfo("America/New_York")
WEDNESDAY = datetime(2026, 11, 18, 15, 0, tzinfo=EASTERN)
SUNDAY = datetime(2026, 11, 22, 10, 0, tzinfo=EASTERN)
BREAKS = [("Thanksgiving Recess (No Classes)", date(2026, 11, 25), date(2026, 11, 29)),
          ("Spring Break", date(2027, 3, 15), date(2027, 3, 21))]


def days(asked: AskedTime, now: datetime = WEDNESDAY) -> tuple[str, str, str]:
    start, end, label = window(asked, now, BREAKS)
    return f"{start:%m-%d %H:%M}", f"{end:%m-%d %H:%M}", label


def test_no_time_named_is_the_next_two_weeks() -> None:
    assert days(AskedTime()) == ("11-18 15:00", "12-02 23:59", DEFAULT_LABEL)
    # A weekday or date pick without its day, or a break the calendar does not date, falls back.
    assert days(AskedTime(when="weekday"))[2] == DEFAULT_LABEL
    assert days(AskedTime(when="date", month=2, day=30))[2] == DEFAULT_LABEL
    assert days(AskedTime(when="break", break_name="fall"))[2] == "the rest of the semester"


def test_each_pick_is_its_own_span_of_days() -> None:
    assert days(AskedTime(when="tonight")) == ("11-18 17:00", "11-18 23:59", "tonight")
    assert days(AskedTime(when="today")) == ("11-18 00:00", "11-18 23:59", "today")
    assert days(AskedTime(when="tomorrow")) == ("11-19 00:00", "11-19 23:59", "tomorrow")
    assert days(AskedTime(when="weekday", weekday="friday")) == (
        "11-20 00:00", "11-20 23:59", "Friday, November 20")
    assert days(AskedTime(when="weekday", weekday="wednesday"))[0] == "11-18 00:00"
    assert days(AskedTime(when="next_weekday", weekday="wednesday"))[0] == "11-25 00:00"
    assert days(AskedTime(when="this_weekend")) == (
        "11-20 17:00", "11-22 23:59", "this weekend")
    assert days(AskedTime(when="next_weekend"))[:2] == ("11-27 17:00", "11-29 23:59")
    assert days(AskedTime(when="this_week")) == ("11-18 15:00", "11-22 23:59", "this week")
    assert days(AskedTime(when="next_week")) == ("11-23 00:00", "11-29 23:59", "next week")
    assert days(AskedTime(when="this_month"))[1:] == ("11-30 23:59", "this month")
    assert days(AskedTime(when="next_month")) == ("12-01 00:00", "12-31 23:59", "in December")
    assert days(AskedTime(when="month", month=12)) == ("12-01 00:00", "12-31 23:59",
                                                       "in December")
    assert days(AskedTime(when="date", month=12, day=3)) == (
        "12-03 00:00", "12-03 23:59", "Thursday, December 3")
    assert days(AskedTime(when="rest_of_term"))[1:] == ("12-31 23:59",
                                                        "the rest of the semester")


def test_sunday_december_into_january_and_a_month_already_gone() -> None:
    # On a Sunday "this week" is the week ahead, and "this weekend" is the one under way.
    assert days(AskedTime(when="this_week"), SUNDAY)[1] == "11-29 23:59"
    assert days(AskedTime(when="this_weekend"), SUNDAY)[:2] == ("11-22 10:00", "11-22 23:59")
    december = datetime(2026, 12, 20, 12, 0, tzinfo=EASTERN)
    start, end, label = window(AskedTime(when="next_month"), december)
    assert (start.date(), end.date(), label) == (date(2027, 1, 1), date(2027, 1, 31),
                                                 "in January")
    start, _, _ = window(AskedTime(when="month", month=3), december)
    assert start.date() == date(2027, 3, 1)
    start, _, _ = window(AskedTime(when="date", month=1, day=5), december)
    assert start.date() == date(2027, 1, 5)


def test_a_break_its_days_before_it_after_it_and_one_that_has_ended() -> None:
    over = AskedTime(when="break", break_name="thanksgiving")
    assert days(over) == ("11-25 00:00", "11-29 23:59", "over Thanksgiving Recess")
    assert days(AskedTime(when="break", break_name="thanksgiving", side="before")) == (
        "11-18 15:00", "11-24 23:59", "before Thanksgiving Recess")
    assert days(AskedTime(when="break", break_name="thanksgiving", side="after")) == (
        "11-30 00:00", "12-13 23:59", "after Thanksgiving Recess")
    # After Thanksgiving has ended, the next break by that name is not on the calendar.
    later = datetime(2026, 12, 1, 9, 0, tzinfo=EASTERN)
    assert window(over, later, BREAKS)[2] == "the rest of the semester"
    assert window(AskedTime(when="break", break_name="spring"), later, BREAKS)[2] == (
        "over Spring Break")
    # A time the picks cannot place alone is placed by the break beside it: "next mon and tues
    # before thanksgiving break" picks next_weekday with no one weekday (11-18 measure).
    assert days(AskedTime(when="next_weekday", break_name="thanksgiving", side="before")) == (
        "11-18 15:00", "11-24 23:59", "before Thanksgiving Recess")
    assert days(AskedTime(when="next_weekday"))[2] == DEFAULT_LABEL


def test_a_part_of_the_day_narrows_one_day_only() -> None:
    assert days(AskedTime(when="weekday", weekday="saturday", part_of_day="evening")) == (
        "11-21 17:00", "11-21 23:59", "Saturday, November 21, evening")
    assert days(AskedTime(when="tomorrow", part_of_day="morning")) == (
        "11-19 00:00", "11-19 12:00", "tomorrow, morning")
    assert days(AskedTime(when="this_week", part_of_day="evening"))[2] == "this week"
    assert days(AskedTime(when="tonight", part_of_day="evening"))[2] == "tonight"


def test_picks_off_their_lists_are_dropped_and_months_out_of_range_not_named() -> None:
    good = {"when": "date", "weekday": "none", "month": 12, "day": 3, "break_name": "none",
            "side": "during", "part_of_day": "none", "date_kind": "none", "term": "none",
            "home_away": "any"}
    assert of(good) == AskedTime(when="date", month=12, day=3)
    assert of({**good, "when": "yesterday"}) is None
    assert of({**good, "month": "12"}) is None
    assert of({**good, "month": 13, "day": 0}) == AskedTime(when="date")
    assert of("tonight") is None


def test_most_reads_win_and_a_split_names_no_time() -> None:
    friday = AskedTime(when="weekday", weekday="friday")
    friday_night = AskedTime(when="weekday", weekday="friday", part_of_day="evening")
    home = AskedTime(home_away="home")
    assert vote([friday, friday, friday_night]) == friday
    # The window is voted whole, so no read's day mixes with another's part of the day.
    assert vote([friday, friday_night, AskedTime(when="tonight")]) == AskedTime()
    assert vote([home, home, AskedTime()]).home_away == "home"  # type: ignore[union-attr]
    assert vote([]) is None


def test_the_read_carries_its_picks_and_a_bad_time_drops_only_the_time() -> None:
    assert "time" in triage.READ_TOOL["parameters"]["required"]
    assert triage.READ_TOOL["parameters"]["properties"]["time"] is asked_time.SCHEMA
    assert "- time:" in triage.READ_PROMPT
    base = {"social": "none", "danger": "none", "needs": [], "problem_words": "",
            "limit": "none", "question": "What is on tonight?", "general": "", "topic": "event"}
    picks = {"when": "tonight", "weekday": "none", "month": 0, "day": 0, "break_name": "none",
             "side": "during", "part_of_day": "none", "date_kind": "none", "term": "none",
             "home_away": "any"}
    reading = triage.reading_of({**base, "time": picks}, "wats on 2nite")
    assert reading is not None and reading.time == AskedTime(when="tonight")
    assert reading.as_trace()["time"] == {"when": "tonight"}
    broken = triage.reading_of({**base, "time": {**picks, "when": "later"}}, "wats on 2nite")
    assert broken is not None and broken.time is None and broken.topic == "event"
    voted = triage.majority([reading, reading, broken])
    assert voted is not None and voted.time == AskedTime(when="tonight")
    assert triage.majority([reading, broken, broken]).time is None  # type: ignore[union-attr]


def test_events_follow_the_picks_and_without_a_read_the_usual_two_weeks() -> None:
    picked = events.window(WEDNESDAY, BREAKS, AskedTime(when="weekday", weekday="friday"))
    assert picked[2] == "Friday, November 20"
    # No read: no time is guessed from the words.
    assert events.window(WEDNESDAY, BREAKS)[2] == DEFAULT_LABEL


def test_the_calendar_takes_the_picked_kind_and_term() -> None:
    # The pick names the kind; the question must still ask only when it is.
    assert kind_asked("When is the deadline?", "withdrawal_deadline") == "withdrawal_deadline"
    assert kind_asked("When are finals for transfer students?", "finals") is None
    assert kind_asked("When are finals?", "none") is None
    assert kind_named("when do I have to withdraw by", "withdrawal_deadline") == (
        "withdrawal_deadline")
    spans = term_spans(DATES)
    today = date(2026, 11, 2)

    def name(message: str, picked: str) -> str | None:
        found = term_asked(message, TERMS, spans, today, picked)
        return found["name"] if found else None
    assert name("When are finals?", "next") == "Spring 2027"
    assert name("When are finals?", "spring") == "Spring 2027"
    assert name("When are finals?", "none") == "Fall 2026"
    assert name("When are fall 2026 finals?", "fall") == "Fall 2026"


def test_home_or_away_keeps_only_those_games() -> None:
    swim = team("womens-swimming", "Women's Swimming", "2026-2027", [
        game("2026-11-20", "Nov 20 (Fri)", "away", "Misericordia Invitational"),
        game("2026-12-05", "Dec 5 (Sat)", "home", "Stockton", time="2:00 PM",
             venue="Bradley Center Pool")])
    today = date(2026, 11, 18)
    said = answer("when is the next swim meet", [*TEAMS, swim], today, "home")
    assert said is not None
    assert said[0].startswith("Women's Swimming's next home game: Dec 5 (Sat), 2:00 PM: vs "
                              "Stockton")
    assert "Misericordia" not in said[0]
    away = answer("when is the next swim meet", [*TEAMS, swim], today, "away")
    assert away is not None and "Misericordia" in away[0] and "Stockton" not in away[0]
    none_home = answer("next mens soccer home game", TEAMS, today, "home")
    assert none_home is not None and "has no more home games" in none_home[0]


def test_the_words_naming_the_picked_time_say_when_not_what() -> None:
    assert asked_time.said_words(None) == frozenset()
    assert asked_time.said_words(AskedTime()) == frozenset()
    assert {"december", "dec"} <= asked_time.said_words(AskedTime(when="month", month=12))
    over = asked_time.said_words(AskedTime(when="break", break_name="thanksgiving"))
    assert {"thanksgiving", "break", "recess"} <= over and "none" not in over
    assert "finals" in asked_time.said_words(AskedTime(when="rest_of_term"))
    # A break named beside another time ("next mon and tues before thanksgiving break") still says
    # when, not what.
    assert "thanksgiving" in asked_time.said_words(
        AskedTime(when="next_weekday", break_name="thanksgiving", side="before"))


def test_what_the_events_are_about_is_the_reads_and_none_asked_is_none() -> None:
    picks = {"when": "weekday", "weekday": "saturday", "month": 0, "day": 0,
             "break_name": "none", "side": "during", "part_of_day": "evening",
             "date_kind": "none", "term": "none", "home_away": "any"}
    assert "about" in asked_time.SCHEMA["required"] and "about:" in asked_time.PROMPT
    assert of({**picks, "about": "  comedy   shows "}) == AskedTime(
        when="weekday", weekday="saturday", part_of_day="evening", about="comedy shows")
    assert of({**picks, "about": "x" * 200}).about is None  # type: ignore[union-attr]
    assert of(picks).about is None  # type: ignore[union-attr]
    comedy, nothing = AskedTime(about="comedy"), AskedTime(about="")
    assert vote([comedy, comedy, nothing]).about == "comedy"  # type: ignore[union-attr]
    assert vote([nothing, nothing, comedy]).about == ""  # type: ignore[union-attr]
    assert vote([comedy, nothing, AskedTime()]).about is None  # type: ignore[union-attr]
    # 11-18 measure: the leftover words were the topic, and no event matched them.
    lonely = ("freshman here stuck in the dorms all weekend, anything happening on campus sat "
              "night??")
    assert events.asked_topic(lonely)
    token = asked_time.ASKED.set(AskedTime(when="weekday", weekday="saturday", about=""))
    try:
        assert events.asked_topic(lonely) == set()
        asked_time.ASKED.set(AskedTime(about="comedy shows"))
        assert events.asked_topic("any comdy stuff this wk") == events.asked_topic("comedy shows")
    finally:
        asked_time.ASKED.reset(token)
