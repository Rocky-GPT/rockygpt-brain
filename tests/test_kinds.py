"""Kinds of events and clubs (kinds.py): which kind a question asks for, the events and clubs of
that kind, and how replies say them (events.py, engine.py)."""

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from rockygpt_brain import kinds
from rockygpt_brain.events import (
    asked_kinds,
    asked_topic,
    asks_events,
    club_list_text,
    computed_answers,
    names_events,
    rank_events,
    topic_text,
)

EASTERN = ZoneInfo("America/New_York")
START = datetime(2026, 11, 3, 9, 0, tzinfo=EASTERN)
END = datetime(2026, 11, 17, 23, 59, tzinfo=EASTERN)


def asked(message: str) -> tuple[set[str], list[set[str]]]:
    found, rest = asked_kinds(message, asked_topic(message))
    return set(found), rest


@pytest.mark.parametrize(("message", "expected"), [
    ("any art events this week", {"arts"}),
    ("anything artsy going on", {"arts"}),
    ("any arts and crafts events", {"arts"}),
    ("any cultural events coming up", {"culture"}),
    ("any volunteer opportunities", {"service"}),
    ("events about mental health", {"wellness"}),
    ("any self-care events", {"wellness"}),
    ("study abroad events", {"abroad"}),
    ("events for international students", {"international"}),
    ("any study sessions", {"study"}),
    ("is there tutoring this week", {"study"}),
    ("any academic events", {"academic"}),
    ("lgbtq+ events", {"lgbtq"}),
    ("any concerts", {"music"}),
    ("any shows this weekend", {"music", "theater"}),
    ("any theater shows", {"theater"}),
    ("any performing arts", {"music", "theater", "dance"}),
    # "Games" alone is sports or games; the words said, or a phrase, tell which.
    ("any games this weekend", {"athletics", "games"}),
    ("any sports games coming up", {"athletics"}),
    ("any home games", {"athletics"}),
    ("any game nights", {"games"}),
    ("are there any gaming clubs", {"games"}),
    ("what fraternities and sororities are there", {"greek"}),
    ("events about saving money", {"money"}),
    ("art and music events", {"arts", "music"}),
    ("what cultural clubs are there", {"culture"}),
    ("is there a club for musicians", {"music"}),
])
def test_a_question_naming_a_kind_asks_for_the_kind(message: str, expected: set[str]) -> None:
    assert asked(message) == (expected, [])


@pytest.mark.parametrize("message", [
    # A member of a kind is not the kind: painting is not all of art and crafts.
    "any painting events",
    "any knitting events",
    # A kind beside another word is a narrower thing than the kind.
    "any art shows",
    "events about black culture",
    "whats happening for womens history month",
    "any events about sexual health",
    "is there a cooking club",
    "what business clubs are there",
    "are there any honor societies",
    "any karaoke nights",
])
def test_a_question_naming_no_kind_is_left_to_the_listings_words(message: str) -> None:
    found, rest = asked(message)
    assert found == set() and rest and rest[0] == asked_topic(message)


def test_a_part_naming_a_kind_and_a_part_naming_none_are_each_matched_their_own_way() -> None:
    found, rest = asked("any art or black history events")
    assert found == {"arts"}
    # The words keep the whole question and the part naming no kind, never the kind's own part.
    assert {"black", "histori"} in rest and {"art"} not in rest


def test_a_words_saying_an_events_form_count_only_when_nothing_else_is_asked() -> None:
    # 11-03 practice: "any book clubs meeting" needed "meeting" in a listing.
    assert asked_topic("any book clubs meeting") == {"book"}
    assert asked_topic("any yoga sessions") == {"yoga"}
    assert asked_topic("any club meetings this week") == {"meet"}
    # A proper name keeps its words: the College Programming Board is not a form.
    assert "program" in asked_topic("what is the college programming board doing")


def test_the_weather_names_a_time_but_no_event() -> None:
    # 11-03 practice: "whats the weather gonna be like this weekend" listed the weekend's events.
    assert not asks_events("whats the weather gonna be like this weekend")
    assert not names_events("anything this weekend?") and asks_events("anything this weekend?")
    assert names_events("any sports games coming up")


def test_every_kind_has_a_key_a_name_and_names_of_its_own() -> None:
    keys = [kind.key for kind in kinds.KINDS]
    assert len(keys) == len(set(keys)) and len(kinds.KINDS_VERSION) == 16
    assert all(kind.name and kind.holds and kind.named for kind in kinds.KINDS)
    assert kinds.names(frozenset({"arts"})) == "art and crafts"
    assert kinds.names(frozenset({"theater", "music"})) == "music or theater and stage"
    assert kinds.names(frozenset()) == ""


def event(n: int, title: str, day: int, description: str = "",
          tags: list[str] | None = None) -> dict[str, Any]:
    return {"_id": f"e{n}", "title": title, "date": f"Nov {day}", "start_time": "7 PM",
            "starts_at": f"2026-11-{day:02d}T19:00:00-05:00", "description": description,
            "tags": tags or ["Social/Entertainment"], "organizer": "CPB", "location": "SC",
            "url": f"https://e/{n}"}


CALENDAR = [event(1, "Ceramics and Tea", 5, "Make a mug."),
            event(2, "Mask Decorating", 4, "Decorate a mask for the masquerade."),
            event(3, "Martial Arts Demo", 6, "See the club's martial arts."),
            event(4, "Trivia Night", 6, "Teams of four."),
            event(5, "Dreamcatcher Decorating", 7),
            event(6, "Paint Night", 20, "Paint a canvas.")]
KINDS = {"e1": ("arts",), "e2": ("arts",), "e3": ("fitness",), "e4": ("games",),
         "e5": ("arts", "culture"), "e6": ("arts",)}


def test_a_kind_lists_every_event_of_the_kind_and_not_a_listing_sharing_its_word() -> None:
    # 11-03 practice: "any art events" found only Cookie Art by its words, while the ceramics,
    # mask and dreamcatcher nights were the art events; a martial arts demo shares the word.
    ranked, matched = rank_events(CALENDAR, "any art events", START, END, None, CALENDAR,
                                  kinds=KINDS)
    assert matched is True
    assert [v["title"] for v in ranked] == ["Mask Decorating", "Ceramics and Tea",
                                            "Dreamcatcher Decorating"]
    assert {v["_matched_by"] for v in ranked} == {"kind"}
    # Without kinds (an index built before them), the listings' words are matched as before.
    words, _ = rank_events(CALENDAR, "any art events", START, END, None, CALENDAR)
    assert [v["title"] for v in words] == ["Martial Arts Demo"]


def test_a_kind_with_no_event_in_the_window_gives_its_next_dates() -> None:
    ranked, matched = rank_events(CALENDAR, "any art events", START,
                                  datetime(2026, 11, 3, 23, 59, tzinfo=EASTERN), None, CALENDAR,
                                  kinds=KINDS)
    assert matched is False
    later, again = rank_events(CALENDAR, "any art events", START,
                               datetime(2027, 1, 1, tzinfo=EASTERN), None, CALENDAR,
                               by_share=True, kinds=KINDS)
    assert again and [v["title"] for v in later][-1] == "Paint Night"


def test_a_kind_and_a_part_naming_none_are_both_listed() -> None:
    ranked, matched = rank_events(CALENDAR, "any art or trivia events", START, END, None,
                                  CALENDAR, kinds=KINDS)
    assert matched is True
    assert {v["title"]: v["_matched_by"] for v in ranked} == {
        "Mask Decorating": "kind", "Ceramics and Tea": "kind", "Trivia Night": "words",
        "Dreamcatcher Decorating": "kind"}


def lines_of(ranked: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"n": k + 2, "event": v["title"], "when": v["date"], "_value": v}
            for k, v in enumerate(ranked)]


def test_the_reply_says_the_kind_and_how_many_events_of_it() -> None:
    ranked, matched = rank_events(CALENDAR, "any art events", START, END, None, CALENDAR,
                                  kinds=KINDS)
    lines = lines_of(ranked)
    facts = [f["fact"] for f in computed_answers(
        "the next 14 days", START, END, 5, lines, matched, {"art"}, False, (), 1,
        asked="any art events", kinds_said="art and crafts", only_kinds=True)]
    assert ("By what their listings describe, the calendar lists 3 art and crafts events from "
            "Tuesday, November 3 to Tuesday, November 17 (of 5 events then).") in facts
    assert not any("whose listing names" in fact for fact in facts)
    said = topic_text("the next 14 days", START, END, 5, lines, matched, (), 1,
                      kinds_said="art and crafts", only_kinds=True)
    assert said is not None and said.startswith(
        "The calendar lists 3 art and crafts events in the next 14 days")
    assert "Ceramics and Tea" in said and "Martial Arts" not in said
    none = [f["fact"] for f in computed_answers(
        "the next 14 days", START, END, 5, [], False, {"art"}, False, (), 1,
        asked="any art events", kinds_said="art and crafts", only_kinds=True)]
    assert ("The calendar lists no art and crafts event from Tuesday, November 3 to Tuesday, "
            "November 17; it lists 5 other events then.") in none
    assert "No upcoming art and crafts event is on the whole calendar." in none


def test_clubs_of_a_kind_are_listed_by_code_each_by_name() -> None:
    clubs = [{"n": 2, "name": "Balkan Club", "category": "Student Organization"},
             {"n": 3, "name": "Black Student Union", "category": "Student Organization"}]
    lead = "By what their entries describe, the club directory lists 2 cultural and heritage clubs"
    assert club_list_text(lead, clubs) == (
        f"{lead}:\n\n- Balkan Club [2]\n- Black Student Union [3]")
    # A club a ramapo.edu page marks inactive says so beside its name.
    assert club_list_text(lead, clubs, {3: {"as_of": "2025-06-12", "n": 4}}) == (
        f"{lead}:\n\n- Balkan Club [2]\n- Black Student Union [3], but Ramapo's page for it says "
        "it is not currently an active club, as of June 12, 2025 [4]")


def test_the_index_gives_each_labelled_card_its_kinds() -> None:
    import array

    from rockygpt_brain.retrieval.meaning import MeaningIndex
    meta = {"dataset_version": "release-1", "identity_hash": "identities-1", "model": "m",
            "dimensions": 2, "units": [], "owner": [], "kinds": {"e1": ["arts"], "e2": []}}
    index = MeaningIndex(meta, array.array("f"))
    assert index.kinds == {"e1": ("arts",)}
    assert MeaningIndex({**meta, "kinds": None}, array.array("f")).kinds == {}


def test_each_card_is_given_its_kinds_once_and_only_known_kinds() -> None:
    import json

    import build_meaning_index as build

    asked: list[list[str]] = []

    def post(path: str, body: dict[str, Any]) -> dict[str, Any]:
        listings = json.loads(body["input"][1]["content"])["listings"]
        asked.append([x["listing"] for x in listings])
        assert body["text"]["format"]["schema"] == build.KINDS_SCHEMA
        items = [{"n": x["n"], "kinds": ["arts", "arts", "culture", "games", "film"]
                  if "Ceramics" in x["listing"] else []} for x in listings]
        return {"output": [{"type": "message", "content": [
            {"type": "output_text", "text": json.dumps({"items": items})}]}]}

    cards = [("e1", "event", "Ramapo College campus event: Ceramics and Tea"),
             ("e2", "event", "Ramapo College campus event: Ceramics and Tea"),
             ("e3", "event", "Ramapo College campus event: Hall Meeting"),
             ("c1", "club", "Ramapo College student club: Chess Club"),
             ("p1", "program", "Ramapo College program: Nursing (BSN)")]
    original, build._post = build._post, post
    try:
        cache: dict[str, list[str]] = {}
        assert build.label_kinds(cards, cache) == (3, 0)
        # A weekly event's dates share one card; a program is not given kinds. Each card is read
        # LABEL_READS times.
        assert len(asked) == kinds.LABEL_READS
        assert all(sorted(batch) == ["Ramapo College campus event: Ceramics and Tea",
                                     "Ramapo College campus event: Hall Meeting",
                                     "Ramapo College student club: Chess Club"]
                   for batch in asked)
        assert build.kinds_of(cards, cache) == {"e1": ["arts", "culture", "games"],
                                                "e2": ["arts", "culture", "games"]}
        # The cache keeps every card read, so the next build asks for none of them again.
        assert build.label_kinds(cards, cache) == (0, 0) and len(asked) == kinds.LABEL_READS
    finally:
        build._post = original
    assert "Kinds:\n- arts: " in build.KINDS_PROMPT


def test_a_card_keeps_the_kinds_most_of_its_reads_give() -> None:
    # 11-03 run 12: one read alone moved borderline clubs in and out of a kind whenever the rules
    # were reworded (Geek and Nerd Culture became cultural; the Disc Club lost sports).
    import json

    import build_meaning_index as build

    seen: dict[str, int] = {}
    given = {"Ramapo College student club: Geek and Nerd Culture": [
                 ["games", "culture"], ["games"], ["games", "film"]],
             "Ramapo College student club: Disc Club (Athletics)": [
                 ["athletics"], [], ["athletics", "fitness"]],
             "Ramapo College student club: Trillium (Office Sponsored Organization)": [
                 [], ["lgbtq"], []]}

    def post(path: str, body: dict[str, Any]) -> dict[str, Any]:
        listings = json.loads(body["input"][1]["content"])["listings"]
        items = []
        for x in listings:
            read = seen[x["listing"]] = seen.get(x["listing"], -1) + 1
            items.append({"n": x["n"], "kinds": given[x["listing"]][read]})
        return {"output": [{"type": "message", "content": [
            {"type": "output_text", "text": json.dumps({"items": items})}]}]}

    cards = [("g", "club", "Ramapo College student club: Geek and Nerd Culture"),
             ("d", "club", "Ramapo College student club: Disc Club (Athletics)"),
             ("t", "club", "Ramapo College student club: Trillium (Office Sponsored Organization)")]
    original, build._post = build._post, post
    try:
        cache: dict[str, list[str]] = {}
        assert build.label_kinds(cards, cache) == (3, 0)
        assert build.kinds_of(cards, cache) == {"g": ["games"], "d": ["athletics"]}
    finally:
        build._post = original

    def failing(path: str, body: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("down")
    original, build._post = build._post, failing
    try:
        # A card no read answered for is failed and read again by the next build.
        assert build.label_kinds(cards[:1], {}) == (1, 1)
    finally:
        build._post = original


def club_calendar(names: list[tuple[str, str]],
                  statuses: dict[int, dict[str, Any]] | None = None) -> Any:
    from rockygpt_brain.retrieval.entity_facts import MemoryEntityFacts
    now = datetime(2026, 10, 21, 18, 0, tzinfo=EASTERN)
    entities, records = [], []
    for n, (name, category) in enumerate(names):
        status = (statuses or {}).get(n)
        entities.append({"id": f"c{n}", "name": name, "kind": "club", "aliases": [],
                         "links": [{"collection": "clubs", "source_key": "archway-clubs",
                                    "source_record_keys": [name]}],
                         **({"status": status} if status else {})})
        records.append({"id": f"row-{n}", "source_key": "archway-clubs",
                        "source_record_key": name, "name": name, "category": category,
                        "website_url": f"https://archway.ramapo.edu/c{n}/", "collected_at": now,
                        "freshness_sla_hours": 720, "listing": {}})
    row = {"id": "row-v", "source_key": "archway-events",
           "source_record_key": "Fri, Oct 23, 2026:Trivia Night", "title": "Trivia Night",
           "date_label": "Fri, Oct 23, 2026", "starts_at": "2026-10-23T20:00:00-04:00",
           "start_time": "8 PM", "end_time": "10 PM", "organizer": "CPB",
           "event_url": "https://archway.ramapo.edu/rsvp_boot?id=1", "collected_at": now,
           "freshness_sla_hours": 36, "listing": {"location": "SC", "tags": ["Social"]}}
    entities.append({"id": "ev", "name": "Trivia Night (2026-10-23)", "kind": "event",
                     "aliases": [], "links": [{"collection": "events",
                                               "source_key": "archway-events",
                                               "source_record_keys": [
                                                   "Fri, Oct 23, 2026:Trivia Night"]}]})
    return MemoryEntityFacts(dataset_version="release-1", identity_hash="identities-1",
                             entities=entities, contacts=[],
                             records={"clubs": records, "events": [row]}, now=lambda: now)


def test_an_umbrella_entrys_clubs_are_read_however_many_there_are() -> None:
    # 11-03 practice: "are there any honor societies" stood for 25 clubs, read at once, which the
    # reader refuses past 12; the question fell to the slower model loop.
    from rockygpt_brain.engine import ChatEngine
    from test_engine import ScriptedGateway
    names = [("Honor Societies", "Honor Society - Academic"),
             *((f"Honor Society {n}", "Honor Society") for n in range(15))]
    engine = ChatEngine(ScriptedGateway(), club_calendar(names), output="written")
    now = datetime(2026, 10, 21, 18, 0, tzinfo=EASTERN)
    _, clubs, sources, computed, _ = engine._read_calendar("are there any honor societies", now, 0)
    assert len(clubs) == 16 and [s["n"] for s in sources] == list(range(1, 17))
    assert any("16 clubs are given" in f["fact"] for f in computed)


def test_with_nothing_matching_and_no_time_named_no_unrelated_event_is_given() -> None:
    # 11-03 practice: "any karaoke nights" was answered with a long list of unrelated events.
    from rockygpt_brain.engine import ChatEngine
    from test_engine import ScriptedGateway
    engine = ChatEngine(ScriptedGateway(), club_calendar([]), output="written")
    now = datetime(2026, 10, 21, 18, 0, tzinfo=EASTERN)
    calendar, *_ = engine._read_calendar("any karaoke nights", now, 0)
    assert calendar is not None and calendar["topic_matched"] is False
    assert calendar["events"] == []
    # A time named asks what is on then: its events are given.
    calendar, *_ = engine._read_calendar("any karaoke this weekend", now, 0)
    assert calendar is not None and [e["event"] for e in calendar["events"]] == ["Trivia Night"]


def test_a_kind_of_club_is_every_club_given_that_kind() -> None:
    from rockygpt_brain.engine import ChatEngine
    from test_engine import ScriptedGateway

    class Index:
        kinds = {"c0": ("culture",), "c1": ("culture", "dance"), "c2": ("games",)}

    names = [("Black Student Union", "Student Organization"),
             ("Bachata Club", "Student Organization"), ("Chess Club", "Student Organization")]
    engine = ChatEngine(ScriptedGateway(), club_calendar(names), output="written")
    found = engine._clubs_by_kind(Index(), "what cultural clubs are there")  # type: ignore[arg-type]
    assert found is not None
    clubs, said = found
    assert [c["name"] for c in clubs] == ["Black Student Union", "Bachata Club"]
    assert said == "cultural and heritage"
    # A question asking more than kinds, or a kind no club has, is left to meaning.
    assert engine._clubs_by_kind(Index(), "is there a cooking club") is None  # type: ignore[arg-type]
    assert engine._clubs_by_kind(Index(), "any lgbtq clubs") is None  # type: ignore[arg-type]
    # Code lists them by name, numbered after what comes before.
    listed = engine._kind_list(Index(), "what cultural clubs are there", 3)  # type: ignore[arg-type]
    assert listed is not None
    said_list, sources, listed_clubs, facts = listed
    assert said_list == ("By what their entries describe, the club directory lists 2 cultural "
                         "and heritage clubs:\n\n- Bachata Club [4]\n- Black Student Union [5]")
    assert [s["n"] for s in sources] == [4, 5] and [c["n"] for c in listed_clubs] == [4, 5]
    assert facts == [{"fact": "By what their entries describe, the club directory lists 2 "
                              "cultural and heritage clubs."}]
