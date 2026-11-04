"""Campus events and clubs: the window a question asks about, the events in it, and the clubs it
names (events.py), read through the shared reader."""

import asyncio
import json
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from rockygpt_brain.contract import ChatRequest
from rockygpt_brain.engine import ChatEngine
from rockygpt_brain.events import (
    DEFAULT_LABEL,
    _stems,
    asked_topic,
    asks_clubs,
    asks_events,
    club_category,
    club_meets,
    club_named,
    club_text,
    clubs_by_meaning,
    events_in,
    find_clubs,
    in_window,
    interests,
    listing_text,
    plain_window,
    rank_events,
    window,
)
from rockygpt_brain.provider import Completion, ToolCall, Usage
from rockygpt_brain.retrieval.entity_facts import MemoryEntityFacts
from rockygpt_brain.turn import intake
from test_engine import ScriptedGateway

EASTERN = ZoneInfo("America/New_York")
# Wednesday, October 21, 2026, 6 PM on campus.
WEDNESDAY = datetime(2026, 10, 21, 18, 0, tzinfo=EASTERN)


def span(message: str) -> tuple[str, str, str]:
    start, end, label = window(message, WEDNESDAY)
    return start.strftime("%a %m-%d %H:%M"), end.strftime("%a %m-%d %H:%M"), label


def test_the_time_words_of_a_question_are_a_window_on_the_campus_clock() -> None:
    assert span("anything going on tonight") == ("Wed 10-21 18:00", "Wed 10-21 23:59", "tonight")
    assert span("events tomorrow")[:2] == ("Thu 10-22 00:00", "Thu 10-22 23:59")
    assert span("what's happening this weekend") == (
        "Fri 10-23 17:00", "Sun 10-25 23:59", "this weekend")
    assert span("next weekend")[:2] == ("Fri 10-30 17:00", "Sun 11-01 23:59")
    assert span("games next week")[:2] == ("Mon 10-26 00:00", "Sun 11-01 23:59")
    assert span("any shows friday")[:2] == ("Fri 10-23 00:00", "Fri 10-23 23:59")
    assert span("what's on oct 30")[:2] == ("Fri 10-30 00:00", "Fri 10-30 23:59")
    assert span("events on 11/2")[:2] == ("Mon 11-02 00:00", "Mon 11-02 23:59")
    assert span("upcoming events")[2] == "the next 14 days"
    assert span("what's happening friday night") == (
        "Fri 10-23 17:00", "Fri 10-23 23:59", "Friday, October 23, evening")
    assert asks_events("is there a basketball game this weekend")
    assert asks_clubs("is there a chess club") and not asks_events("how do i get a permit")
    assert asks_events("anything tonight?") and asks_events("free food tonight")
    for other in ("What's for dinner at the Birch tonight?", "when is the last shuttle tonight",
                  "is the library open this weekend"):
        assert not asks_events(other)  # A time beside a menu, shuttle or hours question.


def event(title: str, day: str, hour: int, tags: list[str], **more: Any) -> dict[str, Any]:
    return {"title": title, "starts_at": f"2026-10-{day}T{hour:02d}:00:00-04:00",
            "date": f"Oct {day}", "start_time": f"{hour} PM", "end_time": None, "tags": tags,
            "organizer": more.get("organizer", "CSI"), "location": more.get("location", "SC"),
            "ticket_status": "FREE", "url": f"https://archway.ramapo.edu/e/{title}"}


def test_events_in_the_window_come_by_date_and_by_the_questions_topic() -> None:
    identities = [{"id": "a", "name": "Volleyball vs. Lehman (2026-10-24)"},
                  {"id": "b", "name": "Trivia Night (2026-10-23)"},
                  {"id": "c", "name": "Career Fair (2026-11-05)"}]
    start, end, _ = window("this weekend", WEDNESDAY)
    # By date: Friday's events are this weekend's (their start time is checked when ranked).
    assert [e["id"] for e in events_in(identities, start, end)] == ["a", "b"]
    values = [event("Women's Volleyball vs. Lehman", "24", 19, ["Athletics"]),
              event("Movie Night", "24", 20, ["Social"]),
              event("Old Game", "21", 9, ["Athletics"])]
    start, end, _ = window("upcoming", WEDNESDAY)
    # The question's topic word picks the volleyball game; an event long over is not offered.
    ranked, matched = rank_events(values, "any volleyball games coming up", start, end)
    assert [v["title"] for v in ranked] == ["Women's Volleyball vs. Lehman"] and matched is True
    # No topic word: every event in the window, in time order.
    ranked, matched = rank_events(values, "what's happening", start, end)
    assert [v["title"] for v in ranked] == ["Women's Volleyball vs. Lehman", "Movie Night"]
    assert matched is None
    # A topic nothing in the window matches: the whole window, flagged as not matched.
    ranked, matched = rank_events(values, "any lacrosse", start, end)
    assert len(ranked) == 2 and matched is False
    # A description's words count, and an event whose card's meaning stands out from every
    # event card's matches a question that shares no word with it ("home game").
    fed = [{**event("Pizza Time", "24", 18, ["Social"]), "description": "Free pizza for all"},
           {**event("Women's Volleyball vs. Lehman", "24", 19, ["Athletics"]), "_id": "v"},
           {**event("Book Club", "24", 20, ["Social"]), "_id": "b"}]
    ranked, matched = rank_events(fed, "is there pizza this weekend", start, end)
    assert [v["title"] for v in ranked] == ["Pizza Time"] and matched is True
    calendar = {f"e{n}": 0.28 + (n % 9) * 0.01 for n in range(40)}
    ranked, matched = rank_events(fed, "when is the next home game", start, end,
                                  {**calendar, "v": 0.47, "b": 0.31})
    assert [v["title"] for v in ranked] == ["Women's Volleyball vs. Lehman"]
    # The same score among a crowd of cards near it stands out from nothing (11-03 check set: a
    # question about club fairs got a dozen unrelated events at 0.47-0.52).
    crowd = {f"e{n}": 0.38 + (n % 12) * 0.01 for n in range(40)}
    ranked, matched = rank_events(fed, "when is the next home game", start, end,
                                  {**crowd, "v": 0.47, "b": 0.31})
    assert matched is False
    # Too few cards to tell a crowd from a match: none is found by meaning.
    ranked, matched = rank_events(fed, "when is the next home game", start, end,
                                  {"v": 0.47, "b": 0.31})
    assert matched is False


def test_a_club_is_named_by_every_word_of_its_directory_name() -> None:
    clubs = [{"id": "1", "name": "Chess Club"}, {"id": "2", "name": "Asian Student Union"},
             {"id": "3", "name": "Club"}]
    assert [c["name"] for c in find_clubs(clubs, "is there a chess club here")] == ["Chess Club"]
    assert find_clubs(clubs, "is there a knitting club") == []


def calendar(organizer: str = "CPB",
             club_status: dict[str, Any] | None = None) -> MemoryEntityFacts:
    row = {"id": "row-v", "source_key": "archway-events",
           "source_record_key": "Fri, Oct 23, 2026:Trivia Night", "title": "Trivia Night",
           "date_label": "Fri, Oct 23, 2026", "starts_at": "2026-10-23T20:00:00-04:00",
           "start_time": "8 PM", "end_time": "10 PM", "organizer": organizer,
           "event_url": "https://archway.ramapo.edu/rsvp_boot?id=1", "collected_at": WEDNESDAY,
           "freshness_sla_hours": 36,
           "listing": {"location": "Student Center (SC)", "tags": ["Social"],
                       "ticketStatus": "FREE"}}
    club = {"id": "row-c", "source_key": "archway-clubs", "source_record_key": "Chess Club",
            "name": "Chess Club", "category": "Recreation",
            "website_url": "https://archway.ramapo.edu/chess/", "collected_at": WEDNESDAY,
            "freshness_sla_hours": 720, "listing": {"membershipInfo": "Lifetime membership"}}
    return MemoryEntityFacts(
        dataset_version="release-1", identity_hash="identities-1",
        entities=[{"id": "ev", "name": "Trivia Night (2026-10-23)", "kind": "event",
                   "aliases": [], "links": [{"collection": "events", "source_key":
                                             "archway-events", "source_record_keys": [
                                                 "Fri, Oct 23, 2026:Trivia Night"]}]},
                  {"id": "cl", "name": "Chess Club", "kind": "club", "aliases": [],
                   "links": [{"collection": "clubs", "source_key": "archway-clubs",
                              "source_record_keys": ["Chess Club"]}],
                   **({"status": club_status} if club_status else {})}],
        contacts=[], records={"events": [row], "clubs": [club]}, now=lambda: WEDNESDAY)


def test_events_and_clubs_are_read_as_published_with_their_listing() -> None:
    (facts,) = calendar().get_event_facts(["ev"], "release-1", identity_hash="identities-1")
    value = facts["properties"][0]["values"][0]["value"]
    assert (value["location"], value["tags"], value["ticket_status"]) == (
        "Student Center (SC)", ["Social"], "FREE")
    (club,) = calendar().get_club_facts(["cl"], "release-1", identity_hash="identities-1")
    assert club["properties"][0]["values"][0]["value"]["membership"] == "Lifetime membership"


def test_a_question_about_this_weekend_is_answered_from_the_calendar() -> None:
    reply = {"parts": [{"asked": "this weekend", "not_on_pages": False, "sections": [1]}],
             "text": "Trivia Night is Fri, Oct 23, 2026 at 8 PM in the Student Center. [2]"}
    gateway = ScriptedGateway(Completion("", (ToolCall("w", "write_answer", reply),), [
        {"type": "function_call", "call_id": "w", "name": "write_answer",
         "arguments": json.dumps(reply)}], Usage(10, 10, 0)))
    request = ChatRequest.model_validate({"messages": [
        {"role": "user", "content": "anything fun happening this weekend? also is there a "
                                    "chess club"}]})
    engine = ChatEngine(gateway, calendar(), output="written")
    result = asyncio.run(engine.answer(intake(request, now=WEDNESDAY), request))
    given = json.loads(gateway.inputs[0][1]["content"])
    assert given["calendar"]["window"] == "this weekend"
    (line,) = given["calendar"]["events"]
    # The calendar page is 1; the window's count is computed by code and cites it.
    assert line["event"] == "Trivia Night" and line["n"] == 2
    assert given["clubs"][0]["name"] == "Chess Club" and given["clubs"][0]["n"] == 3
    facts = [f["fact"] for f in given["computed"]]
    assert ("The calendar lists no event about chess from Friday, October 23 to Sunday, "
            "October 25; it lists 1 other event then.") in facts
    assert "The club directory lists Chess Club (Recreation): " \
           "https://archway.ramapo.edu/chess/." in facts
    assert result.trace is not None and result.trace["decidedBy"] == "one_call"


def test_clubs_found_by_meaning_stand_out_from_every_club_and_are_near_the_best() -> None:
    names = {f"c{n}": f"Club {n}" for n in range(60)}
    rest = [(f"c{n}", 0.30 + (n % 10) * 0.01) for n in range(5, 60)]
    scores = [("c0", 0.62), ("c1", 0.58), ("c2", 0.55), ("c3", 0.52), ("c4", 0.44), *rest]
    # Above the question's own bar (0.48 here) and within 0.08 of the best: three clubs.
    assert [c["name"] for c in clubs_by_meaning(scores, names)] == [
        "Club 0", "Club 1", "Club 2"]
    # A photography club the directory lacks: its nearest card (0.48) is only the top of a crowd
    # of cards near it, and no club is given (11-03 check set: eight unrelated clubs).
    crowd = sorted([(f"c{n}", 0.33 + (n % 16) * 0.01) for n in range(60)],
                   key=lambda pair: -pair[1])
    assert crowd[0][1] >= 0.48 and clubs_by_meaning(crowd, names) == []
    assert clubs_by_meaning([("c0", 0.31)], names) == []


def test_a_kind_of_club_lists_its_whole_category_and_each_interest_is_searched() -> None:
    greek = [{"id": f"g{n}", "name": f"Greek {n}", "category": "Greek Life"} for n in range(5)]
    other = [{"id": "s", "name": "Chess Club", "category": "Student Organization"}]
    directory = [*greek, *other,
                 {"id": "g9", "name": "Delta", "category": "Greek Life - Greek Letter "
                                                         "Fraternities and Sororities"}]
    # Most of the clubs found are Greek Life: all of Greek Life is given.
    assert len(club_category(greek[:3], directory)) == 6
    # A generic category, or no leading one, gives only the clubs found.
    assert club_category([*other, *other, *other], directory) == []
    assert interests("I like anime and community service, which club should I join?") == [
        "anime", "community service"]



def test_code_counts_the_topic_food_and_later_dates_for_the_reply() -> None:
    from rockygpt_brain.events import computed_answers, food_mention, rank_events
    at = datetime(2026, 10, 23, 9, 0, tzinfo=WEDNESDAY.tzinfo)

    def event(n: int, title: str, day: int, description: str = "") -> dict[str, Any]:
        return {"_id": f"e{n}", "title": title, "date": f"Oct {day}", "start_time": "7 PM",
                "starts_at": f"2026-10-{day}T19:00:00-04:00", "description": description,
                "tags": [], "organizer": "CPB", "location": "SC", "url": f"https://e/{n}"}
    corpus = [event(1, "Trivia Night", 23), event(2, "Laurel Game Night", 23, "Board games."),
              event(3, "Boo Bash", 23, "An evening of delicious food and games."),
              event(4, "Halloween Costume Contest", 31)]
    start, end = at, datetime(2026, 10, 23, 23, 59, tzinfo=at.tzinfo)
    # "Basketball" is on no listing, so a game night is not a basketball game.
    assert rank_events(corpus, "basketball game friday", start, end, None, corpus)[1] is False
    ranked, matched = rank_events(corpus, "trivia nights friday", start, end, None, corpus)
    assert matched and [v["title"] for v in ranked] == ["Trivia Night"]
    assert food_mention(corpus[2]) == "An evening of delicious food and games."
    ranked, matched = rank_events(corpus, "free food friday", start, end, None, corpus)
    lines = [{"n": k + 2, "event": v["title"], "when": v["date"], "_value": v}
             for k, v in enumerate(ranked)]
    facts = [f["fact"] for f in computed_answers("Friday, October 23", start, end, 3, lines,
                                                 matched, set(), True, (), 1)]
    assert facts == [
        "The window asked (Friday, October 23) is Friday, October 23.",
        "The calendar lists 1 event whose listing mentions food on Friday, October 23 (of 3 "
        "events then).",
        'Boo Bash (Oct 23) mentions food in its listing: "An evening of delicious food and '
        'games."']
    later = [{"n": 5, "event": "Halloween Costume Contest", "when": "Oct 31"}]
    facts = [f["fact"] for f in computed_answers("Friday, October 23", start, end, 3, [],
                                                 False, {"halloween"}, False, later, 1)]
    assert facts[1:] == [
        "The calendar lists no event about halloween on Friday, October 23; it lists 3 other "
        "events then.",
        "Outside Friday, October 23, the calendar's next events about halloween, by date: "
        "Halloween Costume Contest (Oct 31) [5]."]


def test_alternatives_match_alone_and_page_groups_are_checked_against_the_directory() -> None:
    from rockygpt_brain.events import alternatives, clubs_by_words, clubs_on_pages
    words = {"theater", "dance", "performance"}
    assert {"theater"} in alternatives("any theater or dance performances next week", words)
    directory = [{"name": "Black Student Union"}, {"name": "Commuter Affairs"},
                 {"name": "Naach"}, {"name": "Chess Club"}]
    assert [c["name"] for c in clubs_on_pages(
        directory, "Groups: Black Student Union (BSU), NAACH and the chess team.")] == [
        "Black Student Union", "Naach"]
    assert [c["name"] for c in clubs_by_words(directory, "is there a commuter club")] == [
        "Commuter Affairs"]


def test_a_named_weekday_wins_over_weekend_and_a_versus_listing_is_a_game() -> None:
    from rockygpt_brain.events import _about, _stem, window
    sunday = datetime(2026, 10, 25, 10, 0, tzinfo=WEDNESDAY.tzinfo)
    assert window("family weekend, what's open to parents this saturday", sunday)[2] == \
        "Saturday, October 31"
    assert _stem("games") in _about({"title": "Women's Soccer vs. New Paltz", "tags": []})


def test_a_clubs_own_listing_text_is_read_with_its_heading_dropped() -> None:
    from rockygpt_brain.retrieval.entity_facts import _club_text
    assert _club_text("Mission Naach is a dance club.┬áJoin us.", "Mission") == \
        "Naach is a dance club. Join us."
    assert _club_text("Membership BenefitsLearn dances.", "Membership Benefits") == \
        "Learn dances."
    assert _club_text(None, "Mission") == ""


def test_a_later_part_of_the_term_reads_the_rest_of_the_semester() -> None:
    from rockygpt_brain.events import window
    monday = datetime(2026, 10, 26, 13, 0, tzinfo=WEDNESDAY.tzinfo)
    assert window("any thanksgiving events before break", monday)[2] == "the rest of the semester"
    assert window("anything for finals week", monday)[1].date().isoformat() == "2026-12-31"
    assert window("anything fun", monday)[2] == "the next 14 days"


def test_function_words_and_term_words_are_not_what_an_event_is_about() -> None:
    from rockygpt_brain.events import computed_answers
    # "is any of it free" asks about the whole weekend; "it" was once read as its topic.
    assert asked_topic("what's happening this weekend and is any of it free") == set()
    # "finals week" and "before thanksgiving" say when (the later-term window), not what.
    assert asked_topic("finals week destress events like therapy dogs") == _stems(
        "destress therapy dog")
    assert "thanksgiving" not in asked_topic("anything the week before thanksgiving")
    # How good it is says nothing of what it is about: "cool" once matched "Clay it Cool".
    assert asked_topic("any cool events happening on campus this week?") == set()
    assert asked_topic("what are the best events this weekend") == set()
    assert asked_topic("any cool art events") == _stems("art")
    at = datetime(2026, 10, 30, 0, 0, tzinfo=WEDNESDAY.tzinfo)
    facts = [f["fact"] for f in computed_answers("this weekend", at, at, 4, [], False, {"xy"},
                                                 False, (), 1)]
    assert "mentions food" not in facts[1]
    from rockygpt_brain.events import alternatives
    words = asked_topic("finals week destress events like therapy dogs or pancakes")
    assert _stems("therapy dog") in alternatives("finals week destress events like therapy dogs or "
                                                 "pancakes", words)


THANKSGIVING = [("Thanksgiving Break (No Classes)", date(2026, 11, 25), date(2026, 11, 29)),
                ("Winter Break", date(2026, 12, 23), date(2027, 1, 19))]


def test_a_question_asking_only_what_is_on_in_one_window_is_plain() -> None:
    for plain in ("What events are happening this weekend?", "anything to do tonight?",
                  "What's going on Friday night?", "What can I do this weekend?",
                  "Any events on Nov 12?", "events on 11/12", "what events are coming up",
                  "events in December", "what's happening on campus in december"):
        assert plain_window(plain), plain
    for more in ("Are there any parties this weekend?", "free events this weekend",
                 "What's happening today and tomorrow?", "anything fun this weekend? also is "
                 "there a chess club", "Who is hosting events tonight?",
                 "What's for dinner at the Birch tonight?", "events in December or January",
                 "What time is the event tonight?"):
        assert not plain_window(more), more  # A topic, two windows, or not the calendar.
    # A break is a window when the academic calendar dates it.
    today = date(2026, 11, 4)
    assert plain_window("anything going on over thanksgiving break?", THANKSGIVING, today)
    assert not plain_window("anything going on over thanksgiving break?")
    assert not plain_window("any parties over thanksgiving break?", THANKSGIVING, today)


def test_a_month_or_a_break_named_is_its_own_window() -> None:
    now = datetime(2026, 11, 4, 15, 0, tzinfo=WEDNESDAY.tzinfo)
    start, end, label = window("what's happening on campus in december", now)
    assert (start.date(), end.date(), label) == (date(2026, 12, 1), date(2026, 12, 31),
                                                 "in December")
    start, end, label = window("anything in november?", now)
    assert (start, end.date(), label) == (now, date(2026, 11, 30), "in November")
    assert window("any events in march", now)[0].date() == date(2027, 3, 1)  # Next March.
    # "May" and "March" name a month only after a word saying when.
    assert window("what events may be fun", now)[2] == DEFAULT_LABEL
    start, end, label = window("anything going on over thanksgiving break?", now, THANKSGIVING)
    assert (start.date(), end.date(), label) == (date(2026, 11, 25), date(2026, 11, 29),
                                                 "over Thanksgiving Break")
    start, end, label = window("any events before thanksgiving", now, THANKSGIVING)
    assert (start, end.date(), label) == (now, date(2026, 11, 24), "before Thanksgiving Break")
    assert window("anything thanksgiving weekend", now, THANKSGIVING)[2] == (
        "over Thanksgiving Break")
    assert window("events over winter break", now, THANKSGIVING)[2] == "over Winter Break"
    # A Thanksgiving event is no break ("a thanksgiving dinner" is held before it).
    assert "Thanksgiving" not in window("is there a thanksgiving dinner", now, THANKSGIVING)[2]
    # The words that set the window are not what is asked about.
    assert asked_topic("anything going on over thanksgiving break?") == set()
    assert asked_topic("any events over spring break") == set()
    assert asked_topic("art events in december") == _stems("art")
    assert asked_topic("is there a march on campus") == _stems("march")


def test_social_and_shows_are_events_only_as_events() -> None:
    assert not asks_events("which dean runs social science and social work")
    assert asks_events("is there an ice cream social this week")
    assert asks_events("any socials for commuters")
    assert not asks_events("my portal shows an immunization hold so registration wont let me in")
    assert asks_events("any shows this weekend")
    assert asks_events("are there any comedy shows")


def test_starting_a_club_asks_how_not_for_a_club() -> None:
    for start in ("how do i start my own club", "can we form a new student organization",
                  "how to register a club", "how do I create a club"):
        assert not asks_clubs(start), start
    assert asks_clubs("is there a chess club? if not how do i start one")
    assert asks_clubs("how do i join a club")


def test_code_lists_a_windows_events_by_day_with_their_time_title_and_place() -> None:
    def on(title: str, at: str, start_time: str, end_time: str | None,
           location: str) -> dict[str, Any]:
        return {"title": title, "starts_at": at, "start_time": start_time, "end_time": end_time,
                "location": location}
    start, end, label = window("what's happening this weekend", WEDNESDAY)
    shown = [on("Trivia #2", "2026-10-24T00:00:00+00:00", "8 PM", "10 PM", "Student Center (SC)"),
             on("Cocoa and Crafts", "2026-10-24T11:00:00-04:00", "11 AM", None, "-"),
             on("Avoiding Plagiarism", "2026-10-25T13:00:00-04:00", "1 PM", "2 PM",
                "Online Event")]
    # Trivia starts at midnight UTC: Friday, 8 PM on campus.
    assert listing_text(label, start, end, shown, 5) == (
        "The calendar lists 5 events this weekend, Friday, October 23 to Sunday, October 25; "
        "here are the first 3 by time [1]:\n\n"
        "Friday, October 23:\n- 8 PM to 10 PM: Trivia \\#2, at Student Center (SC) [2]\n\n"
        "Saturday, October 24:\n- 11 AM: Cocoa and Crafts [3]\n\n"
        "Sunday, October 25:\n- 1 PM to 2 PM: Avoiding Plagiarism, Online Event [4]")
    start, end, label = window("anything going on tonight", WEDNESDAY)
    assert listing_text(label, start, end, [], 0) == (
        "The calendar lists no events tonight, Wednesday, October 21 [1].")
    start, end, label = window("what's on friday night", WEDNESDAY)
    one = [{**on("Moonlight Masquerade", "2026-10-23T19:00:00-04:00", "7 PM", "9 PM", "-"),
            "organizer": "The Overlook"}]
    assert listing_text(label, start, end, one, 1) == (
        "The calendar lists 1 event on Friday, October 23, in the evening [1]:\n\n"
        "- 7 PM to 9 PM: Moonlight Masquerade (The Overlook) [2]")
    assert listing_text(label, start, end, [{**one[0], "title": ""}], 1) is None


def test_an_event_still_on_when_the_window_opens_ends_on_the_campus_clock() -> None:
    start, end, _ = window("what's happening this weekend", WEDNESDAY)  # Friday 5 PM on.
    # Published in UTC: 12:15-3:45 PM and 4-5:30 PM on campus, Friday October 23.
    ended = {"title": "Service", "starts_at": "2026-10-23T16:15:00+00:00", "end_time": "3:45 PM"}
    still_on = {"title": "Reading", "starts_at": "2026-10-23T20:00:00+00:00",
                "end_time": "5:30 PM"}
    assert in_window([ended, still_on], start, end) == [still_on]


def test_a_window_question_is_answered_by_code_with_no_model_call() -> None:
    gateway = ScriptedGateway()  # Any model call would fail: none is scripted.
    request = ChatRequest.model_validate({"messages": [
        {"role": "user", "content": "What events are happening this weekend?"}]})
    engine = ChatEngine(gateway, calendar(), output="written")
    result = asyncio.run(engine.answer(intake(request, now=WEDNESDAY), request))
    assert result.body["answer"] == (
        "The calendar lists 1 event this weekend, Friday, October 23 to Sunday, October 25 "
        "[1]:\n\nFriday, October 23:\n- 8 PM to 10 PM: Trivia Night (CPB), at Student Center (SC) "
        "[2]"
        "\n\nSources: [1](https://archway.ramapo.edu/home/events/) "
        "[2](https://archway.ramapo.edu/rsvp_boot?id=1)")
    assert gateway.inputs == []
    assert result.trace is not None and result.trace["decidedBy"] == "code"


def test_a_question_only_about_what_a_named_club_hosts_next_is_the_clubs() -> None:
    directory = [{"name": "Chess Club"}, {"name": "Math Club"}]
    for asked in ("When does the Chess Club meet?",
                  "What events does the Chess Club have coming up?"):
        assert club_meets(asked, directory) == {"name": "Chess Club"}, asked
    for more in ("Where does the Chess Club meet?", "Is there a chess club?",
                 "When does the Chess Club meet and how do I join?",
                 "When does the chess team meet?"):
        assert club_meets(more, directory) is None, more  # A place, existence, more, no name.


def test_what_a_club_hosts_next_is_answered_by_code_from_the_calendar() -> None:
    gateway = ScriptedGateway()  # Any model call would fail: none is scripted.
    request = ChatRequest.model_validate({"messages": [
        {"role": "user", "content": "When does the Chess Club meet?"}]})
    engine = ChatEngine(gateway, calendar(organizer="Chess Club"), output="written")
    result = asyncio.run(engine.answer(intake(request, now=WEDNESDAY), request))
    assert result.body["answer"] == (
        "The calendar's next events hosted by Chess Club [1]:\n\n"
        "- Fri, Oct 23, 2026 8 PM to 10 PM: Trivia Night, at Student Center (SC) [2]"
        "\n\nSources: [1](https://archway.ramapo.edu/chess/) "
        "[2](https://archway.ramapo.edu/rsvp_boot?id=1)")
    assert gateway.inputs == [] and result.trace is not None
    assert result.trace["decidedBy"] == "code"


INACTIVE = {"state": "inactive", "as_of": "2025-06-12", "evidence": [
    {"collection": "office_sections", "source_key": "office-pages", "field": "status",
     "source_record_key": "www.ramapo.edu/clubs/organization/chess-club/",
     "source_url": "https://www.ramapo.edu/clubs/organization/chess-club/"}]}


def test_whether_a_club_exists_says_the_directory_and_an_inactive_page_beside_it() -> None:
    clubs = [{"id": "m", "name": "Math Club"}, {"id": "c", "name": "Ramapo College Chess Club"}]
    assert club_named("Is there a math club?", clubs) == {"id": "m", "name": "Math Club"}
    # The college's own name before a club's is not needed to name it.
    assert club_named("Is there a chess club?", clubs) == clubs[1]
    assert club_named("Is there a math club for girls?", clubs) is None  # Asks more.
    math = {"n": 1, "name": "Math Club", "category": "Student Organization",
            "mission": "We share an interest in the mathematical sciences. We meet.",
            "email": "mathclub@ramapo.edu"}
    assert club_text("Math Club", math, None) == (
        "Yes. The club directory lists Math Club (Student Organization) [1]. Its page says: "
        '"We share an interest in the mathematical sciences." [1] Email: mathclub@ramapo.edu [1].')
    status = {"state": "inactive", "as_of": "2025-06-12", "n": 2}
    assert club_text("Math Club", math, status) == (
        "The club directory lists Math Club (Student Organization) [1], but Ramapo's page for it "
        "says it is not currently an active club, as of June 12, 2025 [2].")
    assert club_text("Chess Club", None, {**status, "n": 1}) == (
        "Ramapo's page for Chess Club says it is not currently an active club, as of June 12, "
        "2025 [1]. The club directory does not list it.")


def test_whether_a_club_exists_is_codes_only_once_the_release_marks_inactive_clubs() -> None:
    gateway = ScriptedGateway()  # Any model call would fail: none is scripted.
    request = ChatRequest.model_validate({"messages": [
        {"role": "user", "content": "Is there a chess club?"}]})
    engine = ChatEngine(gateway, calendar(club_status=INACTIVE), output="written")
    result = asyncio.run(engine.answer(intake(request, now=WEDNESDAY), request))
    assert result.body["answer"] == (
        "The club directory lists Chess Club (Recreation) [1], but Ramapo's page for it says it is "
        "not currently an active club, as of June 12, 2025 [2].\n\nSources: "
        "[1](https://archway.ramapo.edu/chess/) "
        "[2](https://www.ramapo.edu/clubs/organization/chess-club/)")
    assert gateway.inputs == [] and result.trace is not None
    assert result.trace["decidedBy"] == "code"
    # A release with no inactive marks cannot tell an active club from one ramapo.edu says is not.
    plain = ChatEngine(ScriptedGateway(), calendar(), output="written")
    assert plain._club_listing("Is there a chess club?", WEDNESDAY) is None


def test_a_hosts_name_matches_only_when_the_question_names_the_host() -> None:
    # 11-03 practice: "a board game club" matched every College Programming Board event, whose
    # tags and description name the host ("Join the College Programming Board for ...").
    def hosted(title: str, organizer: str, description: str) -> dict[str, Any]:
        return {**event(title, "24", 20, ["Social/Entertainment", organizer],
                        organizer=organizer),
                "description": f"Join the {organizer} for {title}: {description}."}

    values = [hosted("Tuesday Night Live", "College Programming Board", "live music"),
              hosted("Snack and Solve", "Data Science Club", "puzzles"),
              hosted("Card Night", "Bischoff Hall", "cards and board games")]
    start, end, _ = window("upcoming", WEDNESDAY)
    # On the calendar, games are common and "board" rare, so a host's "board" weighs the most.
    games = [event(f"Game {n}", "30", 19, ["Athletics"]) for n in range(10)]

    def titles(message: str) -> list[str]:
        ranked, matched = rank_events(values, message, start, end, corpus=[*values, *games])
        return [v["title"] for v in ranked] if matched else []

    assert titles("any board game nights coming up") == ["Card Night"]
    assert titles("what is the college programming board doing") == ["Tuesday Night Live"]
    assert titles("any data science club events") == ["Snack and Solve"]


def test_whether_a_club_exists_is_not_a_question_about_events() -> None:
    # 11-03 practice: "is there a chess club or a board game club" led with three events.
    assert not asks_events("is there a chess club or a board game club")
    assert not asks_events("are there any clubs that play board games")
    # Asked when, or for its events, it is.
    assert asks_events("is there a club for board games tonight")
    assert asks_events("are there any game nights this week")


def test_a_word_matches_however_each_side_writes_it() -> None:
    from rockygpt_brain.events import _stem, said_words, topic_words
    # Plurals, -ing, a final e and a final y, on both sides (11-03 practice: "fraternities"
    # became "fraternitie", which no club name holds, and "religious" lost its s).
    for one, other in (("fraternities", "fraternity"), ("studying", "study"), ("games", "gaming"),
                       ("dance", "dancing"), ("parties", "party"), ("cookies", "cookie"),
                       ("swimming", "swim"), ("classes", "class"), ("meetings", "meet")):
        assert _stem(one) == _stem(other), (one, other)
    for kept in ("religious", "campus", "tennis", "class", "spring", "money"):
        assert _stem(kept) == kept
    assert _stem("cultural") != _stem("culture") and _stem("formal") != _stem("form")
    # A reply says the question's own words, never the plain forms.
    asked = "what fraternities and sororities are there"
    assert said_words(asked, topic_words(asked)) == "fraternities, sororities"


def test_a_club_named_with_the_word_asked_is_never_said_to_be_missing() -> None:
    from rockygpt_brain.events import club_answers
    clubs = [{"n": 2, "name": "Zeta Phi Beta Sorority, Inc.", "category": "Greek Life",
              "url": "https://archway.ramapo.edu/zpb/"},
             {"n": 3, "name": "Alpha Sigma Phi", "category": "Greek Life",
              "url": "https://archway.ramapo.edu/asp/"}]
    facts = [f["fact"] for f in club_answers(clubs, "what fraternities and sororities are there",
                                             False, 248)]
    assert not any("No club in the directory has" in fact for fact in facts)
    facts = [f["fact"] for f in club_answers(clubs[1:], "are there any religious clubs", False,
                                             248)]
    assert facts[0].startswith("No club in the directory has religious in its name")


def test_an_entry_named_for_its_kind_stands_for_every_club_of_it() -> None:
    from rockygpt_brain.events import kind_members, names_its_kind
    directory = [{"id": "u", "name": "Honor Societies", "category": "Honor Society - Academic"},
                 {"id": "a", "name": "Alpha Kappa Delta", "category": "Honor Society"},
                 {"id": "b", "name": "Tau Sigma", "category": "Honor Society - Academic"},
                 {"id": "s", "name": "Sports Club", "category": "Student Organization"}]
    assert [names_its_kind(e) for e in directory] == [True, False, False, False]
    found = kind_members([{"id": "u", "name": "Honor Societies"}], directory)
    assert [c["name"] for c in found] == ["Honor Societies", "Alpha Kappa Delta", "Tau Sigma"]
    assert kind_members([{"id": "s", "name": "Sports Club"}], directory) == [
        {"id": "s", "name": "Sports Club"}]


def test_a_listing_holding_part_of_the_topic_needs_its_meaning_to_agree() -> None:
    start, end, _ = window("upcoming", WEDNESDAY)
    fed = [{**event("Women's Basketball vs. Baruch", "24", 18, ["Sports/Recreation"]),
            "_id": "g"},
           {**event("Birdwatching", "24", 19, ["Sports/Recreation"]), "_id": "w"},
           {**event("Card Game Night", "24", 20, ["Social"]), "_id": "c"},
           {**event("You Can Narcan!", "24", 21, ["Health"]), "_id": "n",
            "description": "Learn to use naloxone, a life saving drug."},
           {**event("Budget Basics", "25", 18, ["Workshop"]), "_id": "b",
            "description": "Money tips."}]
    calendar = {f"e{n}": 0.28 + (n % 9) * 0.01 for n in range(40)}
    # The games hold both words asked: a listing holding one of them is not a sports game
    # (11-03 practice: a birdwatching outing and a card game meeting were listed).
    ranked, matched = rank_events(fed, "any sports games coming up", start, end,
                                  {**calendar, "g": 0.50, "w": 0.33, "c": 0.36, "n": 0.30,
                                   "b": 0.31})
    assert [v["title"] for v in ranked] == ["Women's Basketball vs. Baruch"] and matched is True
    # No listing holds both "saving" and "money": one word counts only where the meaning agrees
    # (11-03 practice: "a life saving drug" was listed for saving money).
    ranked, matched = rank_events(fed, "any events about saving money", start, end,
                                  {**calendar, "g": 0.30, "w": 0.30, "c": 0.31, "n": 0.33,
                                   "b": 0.52})
    assert [v["title"] for v in ranked] == ["Budget Basics"]
    assert ranked[0]["_matched_by"] == "words"


def test_events_near_in_meaning_are_about_as_near_as_the_one_the_words_found() -> None:
    start, end, _ = window("upcoming", WEDNESDAY)
    fed = [{**event("HIV Awareness", "24", 18, ["Health"]), "_id": "h",
            "description": "Sexual health trivia and free condoms."},
           {**event("Spa Night", "24", 19, ["Wellness"]), "_id": "s"},
           {**event("Community Circle", "24", 20, ["LGBTQ"]), "_id": "q"}]
    calendar = {f"e{n}": 0.28 + (n % 9) * 0.01 for n in range(40)}
    # 11-03 practice: the band was measured from the best event the words had not found, so
    # four events far below the one asked for came after it.
    ranked, _ = rank_events(fed, "any events about sexual health", start, end,
                            {**calendar, "h": 0.62, "s": 0.45, "q": 0.47})
    assert [(v["title"], v["_matched_by"]) for v in ranked] == [("HIV Awareness", "words")]
    ranked, _ = rank_events(fed, "any events about sexual health", start, end,
                            {**calendar, "h": 0.62, "s": 0.45, "q": 0.57})
    assert [(v["title"], v["_matched_by"]) for v in ranked] == [
        ("HIV Awareness", "words"), ("Community Circle", "meaning")]


def test_a_place_named_for_its_host_office_says_where_not_what() -> None:
    from rockygpt_brain.events import _about, _stem
    circle = {"title": "QT+ Community Circle", "tags": ["LGBTQ"],
              "organizer": "Women's Center & LGBTQ+ Services",
              "location": "Women’s Center & LGBTQ+ Services (C-220)"}
    assert _stem("service") not in _about(circle, {_stem("service")})
    cleanup = {"title": "Service Opportunity: Campus Cleanup", "tags": ["Volunteering"],
               "organizer": "Ramapo Green", "location": "The Arch"}
    assert _stem("service") in _about(cleanup, {_stem("service")})


def test_an_upcoming_event_whose_title_names_all_that_is_asked_by_an_uncommon_word() -> None:
    from rockygpt_brain.events import (
        heading_words,
        listing_words,
        related_events,
        related_text,
        related_topic,
    )
    # What is asked about, not how it is asked: "info", "training", "food" (a menu's word).
    assert related_topic("Any info on Narcan training?") == {"narcan"}
    assert related_topic("is there kosher food on campus") == {"kosher"}
    december = {**event("You Can Narcan!", "26", 12, []),
                "starts_at": "2026-12-01T12:00:00-05:00", "date": "Dec 1"}
    values = [event("You Can Narcan!", "26", 12, ["Wellness"]),
              event("You Can Narcan!", "28", 12, ["Wellness"]), december,
              event("Narcan Ninjas vs. Lehman", "27", 19, ["Athletics"]),
              event("Library Open House", "27", 15, []),
              event("Grab and Go Breakfast", "29", 9, []),
              *({**event(f"Social {n}", "30", 18, []), "description": "Grab a snack."}
                for n in range(4))]
    listings = listing_words(values)
    headings = heading_words([["health", "Naloxone (Narcan)"],
                              *(["library", f"Library {n}"] for n in range(4))])
    # The first date of the event the title names; not its later dates, one past the next 30
    # days, or a game whose team happens to share the word.
    (found,) = related_events(values, {"narcan"}, WEDNESDAY, listings, headings)
    assert (found["title"], found["date"]) == ("You Can Narcan!", "Oct 26")
    # Every word asked must be in the title, and a word many pages' sections ("library") or many
    # listings ("grab a snack") say ties no one event to the question.
    assert related_events(values, {"narcan", "dorm"}, WEDNESDAY, listings, headings) == []
    assert related_events(values, {"librari"}, WEDNESDAY, listings, headings) == []
    assert related_events(values, {"grab"}, WEDNESDAY, listings, headings) == []
    assert related_events(values, set(), WEDNESDAY, listings, headings) == []
    assert related_text([(3, found)], WEDNESDAY) == (
        "Coming up on the campus calendar:\n\nMonday, October 26:\n"
        "- 12 PM: You Can Narcan! (CSI), at SC [3]")


def narcan_calendar() -> MemoryEntityFacts:
    """A calendar with a Narcan training and a library open house, beside a meaning index whose
    page sections say "library" four times and "narcan" once."""
    import array

    from rockygpt_brain.retrieval.meaning import MeaningIndex

    def row(key: str, title: str, day: str, hour: int) -> dict[str, Any]:
        return {"id": f"row-{key}", "source_key": "archway-events",
                "source_record_key": f"Oct {day}:{title}", "title": title,
                "date_label": f"Oct {day}", "starts_at": f"2026-10-{day}T{hour:02d}:00:00-04:00",
                "start_time": f"{hour % 12 or 12} PM", "end_time": None,
                "organizer": "Health Services", "collected_at": WEDNESDAY,
                "event_url": f"https://archway.ramapo.edu/rsvp_boot?id={key}",
                "freshness_sla_hours": 36, "listing": {"location": "Student Center (SC)"}}

    class Indexed(MemoryEntityFacts):
        def meaning_index(self) -> MeaningIndex | None:
            labels = [["health", "Naloxone (Narcan)"],
                      *(["library", f"Library {n}"] for n in range(4))]
            return MeaningIndex({"dataset_version": "release-1", "identity_hash": "identities-1",
                                 "model": "m", "dimensions": 2, "owner": [], "labels": labels,
                                 "units": [[f"page-{n}", "0", []] for n in range(len(labels))]},
                                array.array("f"))

    rows = [row("n", "You Can Narcan!", "26", 12), row("l", "Library Open House", "27", 15)]
    return Indexed(
        dataset_version="release-1", identity_hash="identities-1", contacts=[],
        entities=[{"id": f"ev-{r['id']}", "name": f"{r['title']} ({r['starts_at'][:10]})",
                   "kind": "event", "aliases": [],
                   "links": [{"collection": "events", "source_key": "archway-events",
                              "source_record_keys": [r["source_record_key"]]}]} for r in rows],
        records={"events": rows}, now=lambda: WEDNESDAY)


def test_a_reply_the_model_wrote_is_followed_by_the_event_its_question_names() -> None:
    from rockygpt_brain.engine import ChatResult
    from test_engine import finish

    def ask(message: str) -> Any:
        request = ChatRequest.model_validate({"messages": [{"role": "user", "content": message}]})
        engine = ChatEngine(ScriptedGateway(finish("unsupported")), narcan_calendar(),
                            output="written")
        return asyncio.run(engine.answer(intake(request, now=WEDNESDAY), request))

    # 11-03 practice: "Anything about Narcan?" asks for no event, so the calendar was not read
    # and the Narcan training was missed.
    result = ask("Anything about Narcan?")
    assert result.body["answer"].endswith(
        "\n\nComing up on the campus calendar:\n\nMonday, October 26:\n"
        "- 12 PM: You Can Narcan! (Health Services), at Student Center (SC) [1]\n\n"
        "Sources: [1](https://archway.ramapo.edu/rsvp_boot?id=n)")
    assert result.body["citations"] == [{"id": "event-1", "title": "You Can Narcan!",
                                         "url": "https://archway.ramapo.edu/rsvp_boot?id=n"}]
    assert result.trace is not None and result.trace["relatedEvents"] == {
        "words": ["narcan"], "events": ["You Can Narcan!"]}
    # "Library" is said by four of the pages' sections: no library event follows a library reply.
    assert "calendar" not in ask("What are the library hours?").body["answer"]
    # A question about events has read the calendar itself; its events are not said twice.
    listed = ask("any narcan events?").body["answer"]
    assert listed.count("You Can Narcan!") == 1 and "Coming up" not in listed

    # A reply that already names the event, or that code wrote, stays as it is; another reply's
    # sources are kept and the event is numbered after them.
    request = ChatRequest.model_validate(
        {"messages": [{"role": "user", "content": "Anything about Narcan?"}]})
    engine = ChatEngine(ScriptedGateway(), narcan_calendar(), output="written")
    turn = intake(request, now=WEDNESDAY)

    def reply(text: str) -> ChatResult:
        return ChatResult(200, {"answer": text, "status": "answered", "citations": [],
                                "requestId": "r"})
    named = reply("You Can Narcan! is on October 26. [1]\n\nSources: [1](https://a.edu/n)")
    assert asyncio.run(engine._with_related_events(turn, request, named,
                                                   {"decidedBy": "model"})) is named
    pages = reply("Public Safety officers carry naloxone. [1]\n\nSources: [1](https://a.edu/ps)")
    assert asyncio.run(engine._with_related_events(turn, request, pages,
                                                   {"decidedBy": "code"})) is pages
    added = asyncio.run(engine._with_related_events(turn, request, pages,
                                                    {"decidedBy": "one_call"}))
    assert added.body["answer"].endswith(
        "at Student Center (SC) [2]\n\nSources: [1](https://a.edu/ps) "
        "[2](https://archway.ramapo.edu/rsvp_boot?id=n)")
