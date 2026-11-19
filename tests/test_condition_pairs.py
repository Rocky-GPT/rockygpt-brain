"""Pair tests: a student's conditions survive from the read's record to the answer (11-19 plan).

Each test asks two (or more) questions and checks the answers against each other or against the
condition, never against a written answer key: change one condition and the answer must change;
say the same thing in other words (the same record) and it must not; ask for something nothing
fits and the answer must say none, and show nothing else as if it fit. Every check reads what the
answer lists (the games' opponents, the events' titles) and holds each item to the condition.
The schedules and calendars are made up: no word of a test set is in them. The 12 mistakes the
plan predicted are marked "predicted #n".
"""

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from rockygpt_brain import asked_time, conditions, events, teams
from rockygpt_brain.asked_time import AskedTime, Clock, Day, Stretch, asked_window, of, vote
from test_varsity_teams import game, team

EASTERN = ZoneInfo("America/New_York")
# Wednesday, November 18, 2026, 3 PM on campus.
NOW = datetime(2026, 11, 18, 15, 0, tzinfo=EASTERN)
BREAKS = [("Thanksgiving Recess (no classes)", date(2026, 11, 25), date(2026, 11, 29))]

# A mixed schedule: tonight away, this weekend home then away, a home game in Thanksgiving week,
# then December with a game whose time is not listed yet.
WBB = team("womens-basketball", "Women's Basketball", "2026-2027", [
    game("2026-11-18", "Nov 18 (Wed)", "away", "Away U", time="7:00 PM"),
    game("2026-11-21", "Nov 21 (Sat)", "home", "Home College", time="1:00 PM"),
    game("2026-11-22", "Nov 22 (Sun)", "away", "Road State", time="3:00 PM"),
    game("2026-11-25", "Nov 25 (Wed)", "home", "Hall Tech", time="5:30 PM"),
    game("2026-12-01", "Dec 1 (Tue)", "home", "Dec One", time="7:30 PM"),
    game("2026-12-03", "Dec 3 (Thu)", "away", "Dec Three", time="6:00 PM"),
    game("2026-12-05", "Dec 5 (Sat)", "home", "Unlisted Uni", time="TBA")])
# Away first, then home: "the next home game" is not the next game (predicted #1).
MLAX = team("mens-lacrosse", "Men's Lacrosse", "2027", [
    game("2027-03-03", "Mar 3 (Wed)", "away", "Early Away", time="4:00 PM"),
    game("2027-03-06", "Mar 6 (Sat)", "away", "Second Away", time="1:00 PM"),
    game("2027-03-10", "Mar 10 (Wed)", "away", "Third Away", time="4:00 PM"),
    game("2027-03-13", "Mar 13 (Sat)", "home", "Thirteenth Home", time="12:00 PM"),
    game("2027-03-17", "Mar 17 (Wed)", "home", "Later Home", time="4:00 PM")])
# All away: a home question has nothing to list.
MSWIM = team("mens-swimming-and-diving", "Men's Swimming and Diving", "2026-2027", [
    game("2026-11-20", "Nov 20 (Fri)", "away", "Pool A", time="6:00 PM"),
    game("2026-11-21", "Nov 21 (Sat)", "away", "Pool B", time="11:00 AM"),
    game("2026-12-05", "Dec 5 (Sat)", "away", "Pool C", time="10:00 AM")])
# A season over: nothing is left at all.
MSOC = team("mens-soccer", "Men's Soccer", "2026", [
    game("2026-10-28", "Oct 28 (Wed)", "away", "Done Away", "L, 1-4"),
    game("2026-10-31", "Oct 31 (Sat)", "home", "Done Home", "W, 2-0")], {"Overall": "8-7-3"})
# A tournament round still to be drawn, home, then away.
WVB = team("womens-volleyball", "Women's Volleyball", "2026", [
    game("2026-11-19", "Nov 19 (Thu)", "home", "NJAC Tournament Quarterfinal", time="TBA"),
    game("2026-11-21", "Nov 21 (Sat)", "away", "NJAC Championship Match", time="TBA")])
# A game the schedule marks neither home nor away.
FH = team("field-hockey", "Field Hockey", "2026", [
    game("2026-11-20", "Nov 20 (Fri)", "", "Mystery College", time="4:00 PM"),
    game("2026-11-22", "Nov 22 (Sun)", "home", "Known Home", time="1:00 PM")])
SCHEDULES = [WBB, MLAX, MSWIM, MSOC, WVB, FH]


def said(message: str, schedule: Mapping[str, Any], **time: Any) -> str:
    """The team answer for a question whose read picked `time` (none: no read)."""
    picked = AskedTime(**time) if time else None
    found = teams.answer(message, [schedule], NOW.date(), picked=picked, now=NOW, breaks=BREAKS)
    assert found is not None, message
    return found[0]


def listed(text: str, schedule: Mapping[str, Any]) -> list[str]:
    """The opponents an answer gives as its answer: those in its first sentence, which says the
    games that fit (a later sentence may give the next game outside them, labelled)."""
    head = text.split(" [1].")[0]
    return [g["opponent"] for g in schedule["games"] if g["opponent"] in head]


def site_of(schedule: Mapping[str, Any], opponent: str) -> str:
    return next(str(g["site"]) for g in schedule["games"] if g["opponent"] == opponent)


def upcoming(schedule: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [g for g in schedule["games"]
            if date.fromisoformat(g["date"]) >= NOW.date() and not g.get("result")]


def test_home_or_away_lists_only_those_games_on_every_schedule() -> None:
    for schedule in SCHEDULES:
        message = f"whens the next {schedule['name'].casefold()} game"
        sites = {str(g["site"]) for g in upcoming(schedule)}
        texts = {}
        for side in ("home", "away"):
            text = said(message, schedule, home_away=side)
            texts[side] = text
            shown = listed(text, schedule)
            assert all(site_of(schedule, o) == side for o in shown), (schedule["name"], side, text)
            if side not in sites:
                # Nothing fits: say none, never show another site's game as if it fit.
                assert f"no more {side} games" in text, text
                assert not shown or "home or away" in text, text
        if {"home", "away"} <= sites:
            assert texts["home"] != texts["away"], schedule["name"]  # Change one: answers differ.


def test_predicted_1_the_next_home_game_is_not_the_next_game() -> None:
    text = said("whens the next home mens lax game", MLAX, home_away="home")
    assert listed(text, MLAX) == ["Thirteenth Home", "Later Home"]


def test_predicted_2_the_next_away_game_comes_first() -> None:
    text = said("whens the next away womens basketball game", WBB, home_away="away")
    assert listed(text, WBB)[0] == "Away U"
    assert all(site_of(WBB, o) == "away" for o in listed(text, WBB))


def test_a_game_with_no_home_or_away_mark_is_said_apart() -> None:
    text = said("next home field hockey game", FH, home_away="home")
    assert listed(text, FH) == ["Known Home"]
    assert "Not marked home or away on the schedule: Nov 20 (Fri), 4:00 PM: vs Mystery College" \
        in text


def test_predicted_3_a_date_lists_that_days_game_only() -> None:
    text = said("mens lax game on march 13?", MLAX, when="date", month=3, day=13)
    assert listed(text, MLAX) == ["Thirteenth Home"]
    assert "on Saturday, March 13" in text


def test_predicted_4_no_game_tonight_says_so_then_the_next() -> None:
    text = said("is there a mens swim meet tonight", MSWIM, when="tonight")
    assert text.startswith("Men's Swimming and Diving has no game listed tonight, Wednesday, "
                           "November 18 [1]. Its next game: Nov 20 (Fri), 6:00 PM: at Pool A")
    assert listed(text, MSWIM) == []


def test_predicted_5_home_games_this_weekend_are_only_the_weekends_home_games() -> None:
    text = said("any home womens basketball games this weekend", WBB, when="this_weekend",
                home_away="home")
    assert listed(text, WBB) == ["Home College"]
    assert "this weekend, Friday, November 20 to Sunday, November 22" in text


def test_change_one_day_changes_the_games() -> None:
    tonight = said("womens bball game tonight?", WBB, when="tonight")
    weekend = said("womens bball game this weekend?", WBB, when="this_weekend")
    assert listed(tonight, WBB) == ["Away U"]
    assert listed(weekend, WBB) == ["Home College", "Road State"]
    # A range and more days: "wed thru sun", "wed or sat".
    ranged = said("womens bball wed thru sun", WBB, when="weekday", weekday="wednesday",
                  through=Day(weekday="sunday"))
    assert listed(ranged, WBB) == ["Away U", "Home College", "Road State"]
    two = said("womens bball wed or sat", WBB, when="weekday", weekday="wednesday",
               also=(Stretch(when="weekday", weekday="saturday"),))
    assert listed(two, WBB) == ["Away U", "Home College"]
    assert "on Wednesday, November 18 and Saturday, November 21" in two


def test_change_one_clock_limit_changes_the_games() -> None:
    after = said("womens bball game after 7 tonight", WBB, when="tonight",
                 clock=Clock("after", 7))
    before = said("womens bball game before 7 tonight", WBB, when="tonight",
                  clock=Clock("before", 7))
    assert listed(after, WBB) == ["Away U"] and "after 7 PM" in after
    assert listed(before, WBB) == [] and "has no game listed tonight" in before
    assert "before 7 PM" in before
    midday = said("womens bball sat between 12 and 3", WBB, when="weekday", weekday="saturday",
                  clock=Clock("between", 12, to_hour=3))
    evening = said("womens bball sat between 5 and 8", WBB, when="weekday", weekday="saturday",
                   clock=Clock("between", 5, to_hour=8))
    assert listed(midday, WBB) == ["Home College"]
    assert "between noon and 3 PM" in midday
    assert listed(evening, WBB) == [] and "between 5 PM and 8 PM" in evening


def test_a_game_with_no_time_yet_is_shown_and_said_to_have_none() -> None:
    text = said("home bball dec 5 after 6", WBB, when="date", month=12, day=5,
                clock=Clock("after", 6), home_away="home")
    assert listed(text, WBB) == ["Unlisted Uni"]
    assert "no time listed yet, so it may not fit the time asked" in text


def test_the_same_record_gives_the_same_answer() -> None:
    # Same meaning in other words is the read's to make the same record (the texted pair set
    # measures that); from one record, the answer never depends on the words around it.
    picks = {"when": "this_weekend", "home_away": "away"}
    assert listed(said("any away womens bball this wknd", WBB, **picks), WBB) == listed(
        said("is womens basketball traveling this weekend", WBB, **picks), WBB)


def test_a_condition_no_schedule_holds_is_named_and_results_say_days_were_not_used() -> None:
    text = said("any womens bball games w free admission this wk", WBB, when="this_week",
                other=("free admission",))
    assert text.endswith('I can\'t check "free admission" from the team schedules, so this '
                         "isn't narrowed by it.")
    found = teams.answer("how did mens soccer do this weekend", [MSOC], NOW.date(),
                         picked=AskedTime(when="this_weekend"), now=NOW)
    assert found is not None
    assert found[3].not_used == ("days",)
    assert found[0].endswith("This isn't narrowed to the days you asked.")


def test_unsure_home_or_away_lists_both_and_says_so() -> None:
    text = said("next womens bball game", WBB, unsure=("home_away",))
    assert {site_of(WBB, o) for o in listed(text, WBB)} == {"home", "away"}
    assert text.endswith("I wasn't sure if you meant home or away games, so both are listed.")


def test_a_team_question_naming_a_condition_is_read_first() -> None:
    # The read was skipped for every team question, so "home" never reached the answer (18b
    # blind run, and predicted #1). Only a question with no word of a condition skips it.
    for plain in ("who coaches mens soccer", "how did field hockey do this season",
                  "When is the next women's basketball game?", "mens soccer record"):
        assert teams.plain_ask(plain), plain
    for conditioned in ("whens the next home mens lax game", "womens bball tonight",
                        "any bball games after 7", "mens lax game on march 13",
                        "is soccer playing at home this wknd", "swim meets this weekend"):
        assert not teams.plain_ask(conditioned), conditioned
    # Days alone ask for the next games, once the read has picked them.
    assert teams.asked("mens soccer this weekend?") == []
    assert teams.asked("mens soccer this weekend?", AskedTime(when="this_weekend")) == ["next"]


# Calendars: an event at the given day of November 2026, hour and minute on campus, with an end.
def event(title: str, day: int, hour: int, minute: int = 0, hours: float = 1.0,
          start_time: str | None = None) -> dict[str, Any]:
    begins = datetime(2026, 11, day, hour, minute, tzinfo=EASTERN)
    ends = begins + timedelta(hours=hours)

    def clock(at: datetime) -> str:
        return f"{at.hour % 12 or 12}:{at.minute:02d} {'PM' if at.hour >= 12 else 'AM'}"
    return {"_id": title, "title": title, "starts_at": begins.isoformat(),
            "start_time": clock(begins) if start_time is None else start_time,
            "end_time": clock(ends) if start_time is None else "", "date": f"Nov {day}",
            "organizer": "CPB", "location": "SC", "tags": [], "url": f"https://e/{title}"}


# A week of events: today (Wednesday) morning, lunch, afternoon and late night; Thursday;
# Friday; the weekend; next week; and one with no time listed.
CALENDAR = [
    event("Wed Breakfast", 18, 9), event("Wed Lunch Talk", 18, 12, 30),
    event("Wed Study Hall", 18, 15, 30, 2), event("Wed Movie", 18, 19),
    event("Wed Late Show", 18, 21, 30), event("Thu Yoga", 19, 10), event("Thu Trivia", 19, 20),
    event("Fri Fair", 20, 13), event("Sat Gala", 21, 18), event("Sun Brunch", 22, 11),
    event("Mon Club Day", 23, 12), event("Tue Workshop", 24, 14), event("Wed Next", 25, 16),
    event("Nov 30 Mixer", 30, 17), event("All Day Wed", 18, 0, start_time=""),
]
DECEMBER = [{**event("Dec Event", 1, 12), "starts_at": f"2026-12-0{d}T12:00:00-05:00",
             "_id": f"Dec {d}", "title": f"Dec {d} Event"} for d in range(1, 7)]


def titles(**time: Any) -> list[str]:
    """The events code lists for a record (asked_time.asked_window, events.in_window)."""
    window = asked_window(AskedTime(**time), NOW, BREAKS)
    return [v["title"] for v in events.in_window([*CALENDAR, *DECEMBER], window.start,
                                                  window.end, window)]


def test_predicted_6_today_before_2_lists_only_what_starts_before_2() -> None:
    found = titles(when="today", clock=Clock("before", 2))
    assert found == ["All Day Wed", "Wed Breakfast", "Wed Lunch Talk"]
    window = asked_window(AskedTime(when="today", clock=Clock("before", 2)), NOW)
    assert window.label == "today, before 2 PM"
    assert asked_time.phrase(window.label, window.start, window.end) == (
        "today, Wednesday, November 18, before 2 PM")
    # Same meaning: "til 2pm", "gotta leave at 2" are the same record.
    assert titles(when="today", clock=Clock("before", 2, half="pm")) == found
    # Change one: after 4 is another list.
    assert titles(when="today", clock=Clock("after", 4)) == [
        "All Day Wed", "Wed Study Hall", "Wed Movie", "Wed Late Show"]


def test_predicted_7_monday_and_tuesday_are_both_listed() -> None:
    found = titles(when="next_weekday", weekday="monday",
                   also=(Stretch(when="next_weekday", weekday="tuesday"),))
    assert found == ["Mon Club Day", "Tue Workshop"]


def test_predicted_8_a_range_of_dates_is_every_day_in_it() -> None:
    found = titles(when="date", month=12, day=1, through=Day(month=12, day=4))
    assert found == ["Dec 1 Event", "Dec 2 Event", "Dec 3 Event", "Dec 4 Event"]
    window = asked_window(AskedTime(when="date", month=12, day=1, through=Day(day=4)), NOW)
    assert window.label == "Tuesday, December 1 to Friday, December 4"


def test_predicted_9_this_week_or_next_is_both_weeks() -> None:
    found = titles(when="this_week", also=(Stretch(when="next_week"),))
    assert "Wed Next" in found and "Nov 30 Mixer" not in found and "Sun Brunch" in found


def test_predicted_10_tonight_after_9_starts_at_9() -> None:
    assert titles(when="tonight", clock=Clock("after", 9)) == ["Wed Late Show"]
    # "tonight" says PM for a bare 9; "after 9" alone is the morning.
    assert asked_window(AskedTime(when="tonight", clock=Clock("after", 9)), NOW).label == (
        "tonight, after 9 PM")


def test_predicted_11_next_week_on_wednesday_is_next_wednesday() -> None:
    assert titles(when="next_weekday", weekday="wednesday") == ["Wed Next"]


def test_predicted_12_the_next_3_days_are_three_days() -> None:
    found = titles(when="coming_days", days_ahead=3)
    assert "Sat Gala" in found and "Sun Brunch" not in found and "Wed Late Show" in found
    assert asked_window(AskedTime(when="coming_days", days_ahead=3), NOW).label == (
        "the next 3 days")


def test_days_apart_are_said_apart_and_the_days_between_are_not_listed() -> None:
    window = asked_window(AskedTime(when="weekday", weekday="thursday",
                                    also=(Stretch(when="weekday", weekday="saturday"),)), NOW)
    assert len(window.pieces) == 2 and window.days_said == (
        "Thursday, November 19 and Saturday, November 21")
    assert asked_time.phrase(window.label, window.start, window.end, window.days_said) == (
        "on Thursday, November 19 and Saturday, November 21")
    assert titles(when="weekday", weekday="thursday",
                  also=(Stretch(when="weekday", weekday="saturday"),)) == [
        "Thu Yoga", "Thu Trivia", "Sat Gala"]


def test_a_part_of_the_day_holds_on_every_day_asked() -> None:
    assert titles(when="this_weekend", part_of_day="evening") == ["Sat Gala"]
    assert titles(when="weekday", weekday="thursday", part_of_day="morning") == ["Thu Yoga"]


def test_nothing_fits_says_none_in_the_words_of_the_condition() -> None:
    window = asked_window(AskedTime(when="tomorrow", clock=Clock("before", 6, half="am")), NOW)
    found = events.in_window(CALENDAR, window.start, window.end, window)
    assert found == []
    assert events.listing_text(window.label, window.start, window.end, found, 0) == (
        "The calendar lists no events tomorrow, Thursday, November 19, before 6 AM [1].")


def test_an_event_with_no_time_is_kept_on_its_day_and_said_to_have_none() -> None:
    window = asked_window(AskedTime(when="today", clock=Clock("after", 4)), NOW)
    found = events.in_window(CALENDAR, window.start, window.end, window)
    text = events.listing_text(window.label, window.start, window.end, found, len(found))
    assert text is not None and "All Day Wed (CPB), at SC (no time listed, so it may not fit " \
        "the time asked)" in text


def test_the_calendar_receipt_names_what_it_cannot_check() -> None:
    picked = AskedTime(when="tomorrow", other=("for commuters",), home_away="home")
    receipt = events.receipt(picked, asked_window(picked, NOW))
    assert receipt.used == ("days",)
    assert receipt.not_used == ("home_away", "other:for commuters")
    assert "for commuters" in receipt.said and "home or away" in receipt.said
    kept = events.receipt(AskedTime(when="today", clock=Clock("before", 2)),
                          asked_window(AskedTime(when="today", clock=Clock("before", 2)), NOW))
    assert kept.kept and kept.said == "" and kept.window == (
        "today, Wednesday, November 18, before 2 PM")


def test_a_range_that_cannot_be_placed_is_unsure_not_dropped() -> None:
    picked = AskedTime(when="weekday", weekday="friday", through=Day(month=2, day=30))
    window = asked_window(picked, NOW)
    assert window.unplaced == ("through",) and window.label == "Friday, November 20"
    receipt = conditions.receipt(picked, ("days",), window)
    assert receipt.unsure == ("days",) and "covers Friday, November 20" in receipt.said


@contextmanager
def asked(**time: Any) -> Iterator[None]:
    token = asked_time.ASKED.set(AskedTime(**time))
    try:
        yield
    finally:
        asked_time.ASKED.reset(token)


def test_a_question_with_a_condition_beyond_the_window_is_not_answered_by_code_alone() -> None:
    # Dev 11-19: "anything happening today before 2" was "plain", and code listed the whole day.
    assert not events.plain_window("anything happening today before 2")
    with asked(when="tomorrow", other=("for commuters",)):
        assert not events.plain_window("whats happening tomorrow")
    with asked(when="tomorrow", unsure=("days",)):
        assert not events.plain_window("whats happening tomorrow")
    with asked(when="tomorrow", about=""):
        assert events.plain_window("whats happening tomorrow")


def test_the_reads_new_conditions_are_checked_and_voted() -> None:
    picks: dict[str, Any] = {"when": "weekday", "weekday": "monday", "month": 0, "day": 0, "break_name": "none",
             "side": "during", "part_of_day": "none", "date_kind": "none", "term": "none",
             "home_away": "any", "about": "", "days_ahead": 0,
             "through": {"weekday": "none", "month": 0, "day": 0},
             "also": [{"when": "weekday", "weekday": "tuesday", "month": 0, "day": 0}],
             "clock": {"relation": "before", "hour": 14, "minute": 0, "half": "none",
                       "to_hour": 0, "to_minute": 0, "to_half": "none"},
             "other": ["for commuters"]}
    read = of(picks)
    assert read == AskedTime(when="weekday", weekday="monday", about="",
                             also=(Stretch(when="weekday", weekday="tuesday"),),
                             clock=Clock("before", 2, half="pm"), other=("for commuters",))
    # A read without the new picks (the merged time picks) is read as before.
    old = {k: v for k, v in picks.items() if k not in ("through", "also", "clock", "other",
                                                         "days_ahead")}
    assert of(old) == AskedTime(when="weekday", weekday="monday", about="")
    # A between with no end is "after"; no hour is no clock limit.
    assert of({**picks, "clock": {**picks["clock"], "relation": "between"}}).clock == Clock(  # type: ignore[union-attr]
        "after", 2, half="pm")
    assert of({**picks, "clock": {**picks["clock"], "hour": 0}}).clock == Clock()  # type: ignore[union-attr]
    # Most reads give the clock: it holds; split, it is unsure.
    two = Clock("before", 2)
    assert vote([AskedTime(clock=two), AskedTime(clock=two), AskedTime()]).clock == two  # type: ignore[union-attr]
    split = vote([AskedTime(clock=two), AskedTime(clock=Clock("after", 2)), AskedTime()])
    assert split is not None and split.clock == Clock() and split.unsure == ("clock",)
    # Another condition counts when most reads give it or a phrase within it.
    others = vote([AskedTime(other=("for commuters",)), AskedTime(other=("commuters",)),
                   AskedTime()])
    assert others is not None and others.other == ("commuters",)


def test_the_read_keeps_only_other_conditions_in_the_students_own_words() -> None:
    from rockygpt_brain import triage
    from test_triage import read
    message = "anything for commuters tmrw"
    picks: dict[str, Any] = {
        "when": "tomorrow", "weekday": "none", "month": 0, "day": 0, "break_name": "none",
        "side": "during", "part_of_day": "none", "date_kind": "none", "term": "none",
        "home_away": "any", "about": "", "other": ["for commuters", "for grad students"]}
    arguments = read(question=message, topic="event", time=picks).tool_calls[0].arguments
    reading = triage.reading_of(arguments, message)
    assert reading is not None and reading.time is not None
    assert reading.time.other == ("for commuters",)


def test_the_read_schema_holds_every_condition_and_stays_strict() -> None:
    def strict(schema: Mapping[str, Any]) -> Sequence[str]:
        problems = []
        if schema.get("type") == "object":
            if schema.get("additionalProperties") is not False or set(
                    schema.get("required", [])) != set(schema["properties"]):
                problems.append(str(sorted(schema["properties"])))
            for value in schema["properties"].values():
                problems += strict(value)
        if schema.get("type") == "array":
            problems += strict(schema["items"])
        return problems
    assert strict(asked_time.SCHEMA) == []
    assert {"through", "also", "days_ahead", "clock", "other", "about"} <= set(
        asked_time.SCHEMA["required"])
    for slot in ("through:", "also:", "clock:", "other:", "days_ahead:"):
        assert slot in asked_time.PROMPT, slot


def run(facts: Any, message: str, time: dict[str, Any], topic: str, now: datetime) -> Any:
    """The whole bot on one message, its read scripted with these picks; any other model call
    would fail, as none is scripted."""
    import asyncio
    from dataclasses import asdict

    from rockygpt_brain.contract import ChatRequest
    from rockygpt_brain.engine import ChatEngine
    from rockygpt_brain.turn import intake
    from test_engine import ScriptedGateway
    from test_triage import read
    gateway = ScriptedGateway(read(question=message, topic=topic,
                                   time=asdict(AskedTime(**time))))
    request = ChatRequest.model_validate({"messages": [{"role": "user", "content": message}]})
    engine = ChatEngine(gateway, facts, output="written", read_first=True, reads=1)
    result = asyncio.run(engine.answer(intake(request, now=now), request))
    assert len(gateway.inputs) == 1  # The read alone.
    return result


def test_a_team_question_with_home_or_away_is_read_and_answered_with_that_site() -> None:
    from test_varsity_teams import TUESDAY
    from test_varsity_teams import facts as team_facts
    result = run(team_facts(), "whens the next away womens bball game", {"home_away": "away"},
                 "team", TUESDAY)
    answer = result.body["answer"]
    assert answer.startswith("Women's Basketball has no more away games on its 2026-2027 "
                             "schedule [1]. Its next game, home or away: Nov 7 (Sat)")
    assert result.trace["decidedBy"] == "code"
    assert result.trace["conditions"] == {"asked": ["home_away"], "used": ["home_away"],
                                          "not_used": [], "unsure": [], "by": "code"}


def test_an_event_question_with_a_clock_limit_lists_only_what_fits_and_says_the_limit() -> None:
    from test_events import WEDNESDAY, calendar
    # Trivia Night is Friday at 8 PM: "before 7" leaves nothing, "after 7" has it.
    before = run(calendar(), "anything happening fri before 7",
                 {"when": "weekday", "weekday": "friday", "clock": Clock("before", 7),
                  "about": ""}, "event", WEDNESDAY)
    assert before.body["answer"].startswith(
        "The calendar lists no events on Friday, October 23, before 7 PM [1].")
    after = run(calendar(), "anything happening fri after 7",
                {"when": "weekday", "weekday": "friday", "clock": Clock("after", 7),
                 "about": ""}, "event", WEDNESDAY)
    assert after.body["answer"].startswith(
        "The calendar lists 1 event on Friday, October 23, after 7 PM [1]:")
    assert "Trivia Night" in after.body["answer"]
    assert after.trace["conditions"]["used"] == ["days", "clock"]


def test_an_event_condition_the_calendar_cannot_check_is_said_in_the_answer() -> None:
    from test_events import WEDNESDAY, calendar
    result = run(calendar(), "any events for commuters fri",
                 {"when": "weekday", "weekday": "friday", "about": "",
                  "other": ("for commuters",)}, "event", WEDNESDAY)
    assert result.body["answer"].split("\n\nSources:")[0].endswith(
        "I can't check \"for commuters\" from the calendar listings, so this isn't narrowed "
        "by it.")
    assert result.trace["conditions"]["not_used"] == ["other:for commuters"]
