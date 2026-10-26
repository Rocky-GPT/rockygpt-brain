"""Campus events and clubs: the window a question asks about, the events in it, and the clubs it
names (events.py), read through the shared reader."""

import asyncio
import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from rockygpt_brain.contract import ChatRequest
from rockygpt_brain.engine import ChatEngine
from rockygpt_brain.events import (
    asks_clubs,
    asks_events,
    club_category,
    clubs_by_meaning,
    events_in,
    find_clubs,
    interests,
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
    # A description's words count, and an event whose card's meaning is near the window's best
    # above the floor matches a question that shares no word with it ("home game").
    fed = [{**event("Pizza Time", "24", 18, ["Social"]), "description": "Free pizza for all"},
           {**event("Women's Volleyball vs. Lehman", "24", 19, ["Athletics"]), "_id": "v"},
           {**event("Book Club", "24", 20, ["Social"]), "_id": "b"}]
    ranked, matched = rank_events(fed, "is there pizza this weekend", start, end)
    assert [v["title"] for v in ranked] == ["Pizza Time"] and matched is True
    ranked, matched = rank_events(fed, "when is the next home game", start, end,
                                  {"v": 0.47, "b": 0.31})
    assert [v["title"] for v in ranked] == ["Women's Volleyball vs. Lehman"]
    ranked, matched = rank_events(fed, "when is the next home game", start, end,
                                  {"v": 0.40, "b": 0.31})
    assert matched is False


def test_a_club_is_named_by_every_word_of_its_directory_name() -> None:
    clubs = [{"id": "1", "name": "Chess Club"}, {"id": "2", "name": "Asian Student Union"},
             {"id": "3", "name": "Club"}]
    assert [c["name"] for c in find_clubs(clubs, "is there a chess club here")] == ["Chess Club"]
    assert find_clubs(clubs, "is there a knitting club") == []


def calendar() -> MemoryEntityFacts:
    row = {"id": "row-v", "source_key": "archway-events",
           "source_record_key": "Fri, Oct 23, 2026:Trivia Night", "title": "Trivia Night",
           "date_label": "Fri, Oct 23, 2026", "starts_at": "2026-10-23T20:00:00-04:00",
           "start_time": "8 PM", "end_time": "10 PM", "organizer": "CPB",
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
                              "source_record_keys": ["Chess Club"]}]}],
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


def test_clubs_found_by_meaning_are_the_near_best_above_the_floor() -> None:
    names = {f"c{n}": f"Club {n}" for n in range(12)}
    scores = [(f"c{n}", 0.67 - n * 0.02) for n in range(12)]
    # Within 0.08 of the best: five clubs.
    assert [c["name"] for c in clubs_by_meaning(scores, names)] == [
        "Club 0", "Club 1", "Club 2", "Club 3", "Club 4"]
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
    from rockygpt_brain.events import _about, window
    sunday = datetime(2026, 10, 25, 10, 0, tzinfo=WEDNESDAY.tzinfo)
    assert window("family weekend, what's open to parents this saturday", sunday)[2] == \
        "Saturday, October 31"
    assert "game" in _about({"title": "Women's Soccer vs. New Paltz", "tags": []})


def test_a_clubs_own_listing_text_is_read_with_its_heading_dropped() -> None:
    from rockygpt_brain.retrieval.entity_facts import _club_text
    assert _club_text("Mission Naach is a dance club.┬áJoin us.", "Mission") == \
        "Naach is a dance club. Join us."
    assert _club_text("Membership BenefitsLearn dances.", "Membership Benefits") == \
        "Learn dances."
    assert _club_text(None, "Mission") == ""
