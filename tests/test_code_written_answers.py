"""The answers code writes for the 09-28 stress-test misses, and what routes to them."""

from __future__ import annotations

import copy
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import Mock, patch
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from rockygpt_brain.campus.formats import (
    combine_exact,
    events_answer,
    exact_search,
    plain_event_list,
)
from rockygpt_brain.campus.profile_answers import convener_answer, no_menu_answer
from rockygpt_brain.contracts import ChatMessage
from rockygpt_brain.core.routing import (
    day_asked,
    interpret,
    routing_payload,
    validate_answers,
)
from rockygpt_brain.retrieval import profiles
from rockygpt_brain.retrieval.data import CampusData
from rockygpt_brain.retrieval.models import SearchQuery
from rockygpt_brain.retrieval.processing import CONVENER_FIELD_LIMITATION
from rockygpt_brain.retrieval.profiles import Identity, ProfileQuery
from test_profiles import IDENTITY, repository
from test_routing import NOW, answers_for

CAMPUS = ZoneInfo("America/New_York")
PERSON = "00000000-0000-4000-8000-00000000f5ee"
OTHER = "00000000-0000-4000-8000-00000000f5ef"


def program(index: int, name: str) -> dict[str, Any]:
    return {
        "id": f"00000000-0000-4000-8000-0000000c5b5{index}", "kind": "program", "name": name,
        "aliases": ["Computer Science", "CS"],
        "links": [{"collection": "programs", "source_key": "catalog",
                   "source_record_keys": [f"program-{index}"]}],
    }


NAMES = ["Computer Science BS", "Computer Science MS", "Computer Science 4+1",
         "Computer Science Minor"]


def lookup_family(query: ProfileQuery, *conveners: str) -> dict[str, Any]:
    """A lookup by a name four programs share, each program's profile read as given."""
    data = repository()
    data._artifacts["campus-identities"]["entities"] = [
        program(index, name) for index, name in enumerate(NAMES)]
    original = profiles.lookup_profile

    def each(data: CampusData, query: ProfileQuery) -> dict[str, Any]:
        if query.entity_id is None:
            return original(data, query)
        index = int(str(query.entity_id)[-1])
        record = {"id": f"programs:{index}:convener", "collection": "programs"}
        return {"records": [record], "truncated": False, "resolution": {
            "entity": {"id": str(query.entity_id), "name": NAMES[index], "kind": "program"}},
            "components": {"conveners": {"status": "available", "relationships": [
                {"type": "convener", "evidence_ids": [record["id"]],
                 "target": {"id": conveners[index % len(conveners)], "kind": "person",
                            "name": "Scott Frees"}}]}}}

    with patch.object(profiles, "lookup_profile", side_effect=each):
        return original(data, query)


def test_a_family_name_answers_once_when_every_program_shares_its_convener() -> None:
    query = ProfileQuery(entity="CS", include=["conveners"])
    shared = lookup_family(query, PERSON)
    assert shared["resolution"]["status"] == "shared"
    assert [variant["entity"]["name"] for variant in shared["variants"]] == NAMES
    assert len(shared["records"]) == 4
    # Asked anything else too, the programs' own answers differ: GPT asks which one.
    both = lookup_family(ProfileQuery(entity="CS", include=["conveners", "faculty"]), PERSON)
    assert both["resolution"]["status"] == "ambiguous"


def test_different_conveners_stay_ambiguous() -> None:
    query = ProfileQuery(entity="Computer Science", include=["conveners"])
    assert lookup_family(query, PERSON, OTHER)["resolution"]["status"] == "ambiguous"


def shared_output(*targets: dict[str, Any]) -> dict[str, Any]:
    record = {"id": "programs:1:convener", "collection": "programs", "trust_tier":
              "official_primary", "freshness": "fresh", "limitations": [],
              "url": "https://catalog.ramapo.edu/", "title": "CS BS — published convener"}
    return {"status": "ok", "truncated": False, "records": [record],
            "resolution": {"status": "matched",
                           "entity": {"id": "p", "name": "Computer Science BS",
                                      "kind": "program"}},
            "components": {"conveners": {
                "status": "available", "failed_links": 0, "relationships_missing": 0,
                "relationships": [{"type": "convener", "target": target,
                                   "evidence_ids": [record["id"]]} for target in targets]}}}


def test_the_convener_answer_states_only_current_linked_people() -> None:
    query = ProfileQuery(entity="Computer Science BS", include=["conveners"])
    person = {"id": PERSON, "name": "Scott Frees", "kind": "person"}
    answer = convener_answer(shared_output(person), query)
    assert answer is not None
    assert answer.parts[0].text == "Scott Frees is the listed convener of the Computer Science BS."
    # Every catalog convener record carries the standard caveat about its raw field.
    caveated = shared_output(person)
    caveated["records"][0]["limitations"] = [CONVENER_FIELD_LIMITATION]
    assert convener_answer(caveated, query) == answer
    retired = {**person, "status": "retired"}
    assert convener_answer(shared_output(retired), query) is None
    stale = shared_output(person)
    stale["records"][0]["freshness"] = "stale"
    assert convener_answer(stale, query) is None
    # Asked more than who convenes it, GPT writes.
    assert convener_answer(shared_output(person), ProfileQuery(
        entity="Computer Science BS", include=["conveners", "faculty"])) is None


def cs_candidates() -> list[Identity]:
    return [Identity.model_validate(program(index, name)) for index, name in enumerate(
        ["Computer Science BS", "Computer Science MS", "Computer Science 4+1",
         "Computer Science Minor"])]


@pytest.mark.parametrize("text,entity", [
    ("who is the cs convener", "CS"),
    ("Who is the computer science convener?", "Computer Science"),
])
def test_a_convener_question_is_one_lookup_by_the_shared_name(text: str, entity: str) -> None:
    candidates = cs_candidates()
    request = [ChatMessage(role="user", content=text)]
    payload, day = routing_payload(request, candidates, NOW)
    # Measured 09-28: Jev read "cs convener" as asking who belongs to it too (0.9).
    answers = answers_for(payload, route="profile", entity="several", detail_contact=0.0,
                          detail_leaders=0.98, detail_teachers=0.9)
    validate_answers(answers, payload["questions"])
    decision = interpret(answers, candidates, day, request)
    assert decision.tool == "lookup_profile" and decision.template == "convener"
    assert decision.arguments is not None
    assert decision.arguments["entity"] == entity
    assert decision.arguments["include"] == ["conveners"]
    # Anything more is GPT's: the lookup isn't narrowed and code doesn't write.
    request = [ChatMessage(role="user", content=text + " and what is their email")]
    payload, day = routing_payload(request, candidates, NOW)
    answers = answers_for(payload, route="profile", entity="several", detail_contact=0.9,
                          detail_leaders=0.98)
    validate_answers(answers, payload["questions"])
    assert interpret(answers, candidates, day, request).template is None


def dated(text: str, **probabilities: float) -> tuple[dict[str, Any], list[ChatMessage]]:
    request = [ChatMessage(role="user", content=text)]
    payload, _ = routing_payload(request, [], NOW)
    answers = answers_for(payload, date="named")
    answers["date"].update(probabilities=probabilities, confidence=0.87,
                           choice=max(probabilities, key=probabilities.__getitem__))
    return answers, request


def test_a_day_code_read_stands_when_jev_rules_out_working_one_out() -> None:
    # "atrium hours tmrw": 0.91 named, 0.08 other, confidence 0.87 (09-28).
    answers, request = dated("atrium hours tmrw", named=0.91, other=0.08, none=0.01)
    assert day_asked(answers, request) == "named"
    answers, request = dated("atrium hours tmrw", named=0.8, other=0.19, none=0.01)
    assert day_asked(answers, request) is None
    # A word that moves the day always leaves it to GPT.
    answers, request = dated("atrium hours the day after tmrw", named=0.91, other=0.08,
                             none=0.01)
    assert day_asked(answers, request) is None


def venue_output(menu: dict[str, Any]) -> dict[str, Any]:
    return {"status": "ok", "records": [], "truncated": False,
            "resolution": {"status": "matched", "entity": {
                "id": IDENTITY["id"], "name": "The Atrium", "kind": "venue"}},
            "components": {"menu": menu}}


def test_a_place_with_no_menu_that_day_says_so_and_nothing_else() -> None:
    day = NOW.date()
    query = ProfileQuery(entity_id=UUID(str(IDENTITY["id"])), include=["menu"], date=day)
    missing = {"status": "missing", "complete": True, "total_matches": 0, "failed_links": 0,
               "linked_records_missing": 0, "service_date": day.isoformat()}
    answer = no_menu_answer(venue_output(missing), query)
    assert answer is not None and answer.status == "unavailable"
    assert answer.parts[0].text == (
        f"I couldn't find a published menu for The Atrium on {day:%A, %B} {day.day}.")
    for broken in ({"failed_links": 1}, {"complete": False}, {"status": "partial"}):
        assert no_menu_answer(venue_output({**missing, **broken}), query) is None
    lunch = query.model_copy(update={"meal": "lunch"})
    assert no_menu_answer(venue_output(missing), lunch) is None
    # A building has no menu of its own: "the dining place in the Learning Commons" is
    # about a place inside it, which GPT finds.
    building = venue_output(missing)
    building["resolution"]["entity"]["kind"] = "building"
    assert no_menu_answer(building, query) is None


def event(index: int, title: str, start: str, end: str | None, location: str | None,
          day: datetime) -> dict[str, Any]:
    clock = datetime.strptime(start, "%I:%M %p").time()
    fields: dict[str, Any] = {
        "title": title, "start_time": start, "end_time": end,
        "starts_at": datetime.combine(day.date(), clock, tzinfo=CAMPUS).isoformat(),
        "occurrence_date": day.date().isoformat(), "location": location}
    return {"id": f"events:{index}", "entity_id": f"event:{index}", "source_key": "archway",
            "collection": "events", "trust_tier": "official_primary", "freshness": "fresh",
            "fields": fields, "coverage": {"fields": {
                key: "published" if value is not None else "not_published"
                for key, value in fields.items()}}}


def test_a_day_of_events_is_listed_by_code_soonest_first() -> None:
    now = NOW.replace(hour=17)
    records = [event(1, "Slime Time!", "9:00 PM", "10 PM", None, now),
               event(2, "Spanish Club Jeopardy", "4:30 PM", "5:30 PM", None, now),
               event(3, "Adler Lock Out", "8:00 AM", "11:59 PM", "Berrie Center", now),
               event(4, "Yoga", "7:00 AM", "8 AM", "Arch Courtyard", now)]
    output = {"status": "ok", "records": records, "total_matches": 4, "truncated": False}
    query = SearchQuery(collection="events", date_from=now.date(), date_to=now.date(),
                        limit=100)
    answer = events_answer(output, query, now)
    assert answer is not None
    text = "\n".join(part.text for part in answer.parts)
    assert text.index("Adler Lock Out") < text.index("Spanish Club") < text.index("Slime")
    assert "8:00 AM to 11:59 PM: Adler Lock Out, Berrie Center" in text
    # No location is published for the Jeopardy night: nothing is said about one.
    assert "- 4:30 PM to 5:30 PM: Spanish Club Jeopardy\n" in text + "\n"
    assert "Yoga" not in text and "1 earlier event has already ended today." in text
    assert events_answer({**output, "truncated": True}, query, now) is None
    unreadable = copy.deepcopy(records)
    unreadable[1]["fields"]["end_time"] = "late"
    assert events_answer({**output, "records": unreadable}, query, now) is None


@pytest.mark.parametrize("text,plain", [
    ("what events are tomorrow", True),
    ("whats happening on campus today", True),
    ("is there free food tonight", False),
    ("any club events tomorrow", False),
    ("what events are on tonight", False),
])
def test_only_a_plain_day_of_events_is_listed_by_code(text: str, plain: bool) -> None:
    assert plain_event_list(text, NOW) is plain


def trip(sequence: int, route: str, departure: str, stops: list[tuple[str, str]],
         returned: str) -> dict[str, Any]:
    fields = {"sequence": sequence, "route": route, "service_day": "weekday",
              "service_date": str(NOW.date()), "campus_departure": departure,
              "campus_return": returned,
              "stops": [{"location": name, "time": time} for name, time in stops]}
    return {"id": f"shuttle:{sequence}", "entity_id": f"trip:{sequence}", "source_key": "s",
            "collection": "shuttle", "fields": fields, "freshness": "fresh",
            "trust_tier": "official_primary",
            "coverage": {"fields": {key: "published" for key in fields}}}


def timetable() -> dict[str, Any]:
    records = [
        trip(1, "Roadrunner", "7:00 AM", [("Interstate Plaza", "7:10 AM")], "7:30 AM"),
        trip(2, "Roadrunner", "9:00 AM", [("Garden State Plaza", "9:25 AM")], "9:50 AM"),
        trip(3, "Roadrunner", "2:00 PM", [("Garden State Plaza", "2:25 PM")], "2:50 PM"),
        trip(4, "Route 17", "8:00 AM", [("Train", "8:10 AM")], "8:20 AM"),
    ]
    return {"status": "ok", "records": records, "total_matches": 4, "truncated": False}


@pytest.mark.parametrize("question,expected", [
    # "What's the first shuttle today?" was answered with the next one (09-28).
    ("whats the first shuttle today", "The first published departures from campus on "
     f"{NOW.date()} (America/New_York) are: Roadrunner at 7:00 AM; Route 17 at 8:00 AM."),
    ("whats the first shuttle today that goes to garden state plaza",
     "The first published departure from campus that reaches Garden State Plaza on "
     f"{NOW.date()} (America/New_York) is Roadrunner at 9:00 AM, arriving at 9:25 AM."),
    ("when is the next shuttle to garden state plaza",
     "The next published departure from campus that reaches Garden State Plaza on "
     f"{NOW.date()} (America/New_York) is Roadrunner at 2:00 PM, arriving at 2:25 PM."),
])
def test_first_next_and_last_come_from_one_calculation(question: str, expected: str) -> None:
    query = SearchQuery(collection="shuttle", date_from=NOW.date(), limit=100)
    piece = exact_search(question, [ChatMessage(role="user", content=question)], query,
                         timetable(), NOW)
    assert piece is not None and piece.answer.parts[0].text == expected


def two_days() -> dict[str, Any]:
    """Today's timetable and tomorrow's, as the departures lookup fetches them."""
    tomorrow = []
    for row in timetable()["records"]:
        later = copy.deepcopy(row)
        # The same trip, like the published timetable's, under the same identity.
        later["id"] = row["id"] + ":next"
        day = NOW.date() + timedelta(days=1)
        later["fields"]["service_date"] = str(day)
        later["fields"]["service_day"] = ("weekday" if day.weekday() < 5
                                          else day.strftime("%A").lower())
        tomorrow.append(later)
    records = [*timetable()["records"], *tomorrow]
    return {"status": "ok", "records": records, "total_matches": len(records),
            "truncated": False}


def test_the_last_shuttle_after_it_left_says_when_and_gives_the_next() -> None:
    # "When is the last shuttle?" at 11:10 PM (09-28) said only that no later departure
    # was found, route by route.
    query = SearchQuery(collection="shuttle", date_from=NOW.date(),
                        date_to=NOW.date() + timedelta(days=1), limit=100)
    tomorrow = NOW.date() + timedelta(days=1)

    def answer(question: str, now: datetime) -> list[str]:
        piece = exact_search(question, [ChatMessage(role="user", content=question)], query,
                             two_days(), now)
        assert piece is not None and piece.answer.status == "answered"
        return [part.text for part in piece.answer.parts]

    late = NOW.replace(hour=23, minute=10)
    assert answer("when is the last shuttle", late)[:3] == [
        f"The last published departures from campus on {NOW.date()} (America/New_York) are: "
        "Route 17 at 8:00 AM (time passed); Roadrunner at 2:00 PM (time passed).",
        f"The next published departure from campus on Route 17 is 8:00 AM on {tomorrow} "
        "(America/New_York).",
        f"The next published departure from campus on Roadrunner is 7:00 AM on {tomorrow} "
        "(America/New_York).",
    ]
    # Before it leaves, the last is still today's, never tomorrow's.
    assert answer("when is the last shuttle", NOW.replace(hour=10))[0] == (
        f"The last published departures from campus on {NOW.date()} (America/New_York) are: "
        "Route 17 at 8:00 AM (time passed); Roadrunner at 2:00 PM.")
    # One stop.
    assert answer("when is the last shuttle to garden state plaza", late)[:2] == [
        "The last published departure from campus that reaches Garden State Plaza on "
        f"{NOW.date()} (America/New_York) was Roadrunner at 2:00 PM, arriving at 2:25 PM; "
        "that time has passed.",
        "The next published departure from campus that reaches Garden State Plaza is 9:00 AM "
        f"on {tomorrow} (America/New_York), arriving at 9:25 AM.",
    ]
    # The next shuttle after the day's last is tomorrow's, with its own date.
    assert answer("when is the next shuttle", late)[0] == (
        "The next published departures from campus on "
        f"{tomorrow} (America/New_York) are: Roadrunner at 7:00 AM; Route 17 at 8:00 AM.")


def test_no_later_trip_to_a_stop_is_said_once() -> None:
    query = SearchQuery(collection="shuttle", date_from=NOW.date(), limit=100)
    question = "when is the next shuttle to interstate plaza"
    piece = exact_search(question, [ChatMessage(role="user", content=question)], query,
                         timetable(), NOW)
    assert piece is not None
    assert [part.text for part in piece.answer.parts] == [
        "I couldn't find a later scheduled departure from campus that reaches Interstate "
        f"Plaza on {NOW.date()}. This does not establish that service has ended after that."]


def test_a_comma_listed_shuttle_part_is_written_by_code() -> None:
    # Q30 quoted "Tell me the next shuttle", but the comma after it kept the part from
    # code and sent it to GPT and review (09-29).
    question = ("Tell me the next shuttle, where the Registrar is, and what I should do if "
                "someone is unconscious.")
    messages = [ChatMessage(role="user", content=question)]
    query = SearchQuery(collection="shuttle", date_from=NOW.date(),
                        date_to=NOW.date() + timedelta(days=1), limit=100)
    late = NOW.replace(hour=23, minute=10)
    piece = exact_search("Tell me the next shuttle", messages, query, two_days(), late)
    assert piece is not None
    first = (f"The next published departures from campus on {NOW.date() + timedelta(days=1)} "
             "(America/New_York) are: Roadrunner at 7:00 AM; Route 17 at 8:00 AM.")
    assert piece.answer.parts[0].text == first
    # The rest of the request is still left for the written answer.
    assert combine_exact(messages, [piece], fallback=False) is None
    prefix = combine_exact(messages, [piece], fallback=False, allow_remaining=True)
    assert prefix is not None and prefix.parts[0].text == first


def test_a_search_for_a_job_title_ranks_the_person_who_holds_it_first() -> None:
    data = repository()
    rows: list[dict[str, Any]] = [
        {"name": "Alexander Biagioli", "title": "Lecturer, Finance", "terms": ["presid"]},
        {"name": "Cindy R. Jebb, Ph.D.", "title": "President", "terms": ["presid"]},
        {"name": "Dean of Students", "title": None, "terms": ["dean", "student"]},
        {"name": "Ed Petkus", "title": "Dean", "terms": ["dean", "student"]},
    ]
    records = [{"id": f"contacts:{index}", "collection": "contacts", "title": row["name"],
                "fields": {"name": row["name"], "title": row["title"]},
                "_search_terms": row["terms"], "_title_terms": row["name"].lower().split(),
                "_role_terms": ["presid"] if row["title"] == "President"
                else ["dean"] if row["title"] == "Dean" else None}
               for index, row in enumerate(rows)]

    def search(query: str, terms: list[str]) -> list[str]:
        for record in records:
            record["_query_terms"] = terms
        with (patch.object(CampusData, "_ensure_loaded"),
              patch.object(CampusData, "_load", return_value=records),
              patch.object(CampusData, "_dates", side_effect=lambda loaded, _: loaded),
              patch("rockygpt_brain.retrieval.entity_evidence.attach_entity_navigation",
                    return_value=None),
              patch.object(CampusData, "_public", side_effect=lambda record: record)):
            output = data.search(SearchQuery(collection="contacts", query=query, limit=2))
        return [record["title"] for record in output["records"]]

    # "Ramapo College president" ranked the President 11th (09-28).
    assert search("Ramapo College president", ["ramapo", "colleg", "presid"])[0] == (
        "Cindy R. Jebb, Ph.D.")
    # A title that is only part of the search wins nothing: the office named whole leads.
    assert search("dean of students", ["dean", "student"])[0] == "Dean of Students"



def test_food_right_now_fetches_the_hours_and_the_meal_being_served_or_next() -> None:
    # "What can I eat on campus right now?" fetched the whole day's menu (66 of 141 items
    # arrived) and no hours (09-28).
    from rockygpt_brain.core.routing import RouteDecision, eating_now, serving_now

    now = NOW.replace(hour=17, minute=5)
    data = Mock()
    data.search.return_value = {"records": [{"fields": {"name": "Birch Tree Inn", "periods": [
        {"label": "Lunch", "start": "11:00 AM", "end": "02:00 PM"},
        {"label": "Dinner", "start": "05:00 PM", "end": "08:00 PM"},
        {"label": "Late Night", "start": "09:00 PM", "end": "12:00 AM"}]}}]}
    lookups = serving_now(data, now)
    assert [lookup["arguments"]["collection"] for lookup in lookups] == ["dining_hours", "menu"]
    assert lookups[1]["arguments"]["filters"]["meal"] == "Dinner"
    # Between meals, the next meal today is the one fetched: at 8:15 PM, Late Night at
    # 9 (09-28), never the Dinner that ended at 8.
    between = serving_now(data, now.replace(hour=20, minute=15))
    assert [lookup["arguments"]["collection"] for lookup in between] == ["dining_hours", "menu"]
    assert between[1]["arguments"]["filters"]["meal"] == "Late Night"
    assert serving_now(data, now.replace(hour=15))[1]["arguments"]["filters"]["meal"] == "Dinner"
    request = [ChatMessage(role="user", content="What can I actually eat on campus right now?")]
    payload, day = routing_payload(request, [], now)
    answers = answers_for(payload, route="search", kind="menu", entity="none")
    decision = RouteDecision(route="search")
    assert eating_now(decision, answers, [], day, request, now)
    unsure = answers_for(payload, route="search", kind="other", entity="none", list_menu=0.94)
    assert eating_now(decision, unsure, [], day, request, now)
    # Jev led with the menu at 0.87-0.91 and put the rest on dining hours (09-28): the
    # two together reach the bar, and both are what this lookup fetches.
    def kind(**probabilities: float) -> dict[str, Any]:
        leading = max(probabilities, key=lambda option: probabilities[option])
        picked = answers_for(payload, route="search", kind=leading, entity="none")
        picked["kind"] = {**picked["kind"], "confidence": probabilities[leading],
                          "probabilities": dict.fromkeys(picked["kind"]["probabilities"], 0.0)
                          | probabilities}
        return picked

    assert eating_now(decision, kind(menu=0.6, dining_hours=0.35, other=0.05), [], day,
                      request, now)
    # Leading with the menu is not enough on its own, and dining hours leading is a
    # question about places, which GPT plans.
    assert not eating_now(decision, kind(menu=0.6, other=0.4), [], day, request, now)
    assert not eating_now(decision, kind(menu=0.35, dining_hours=0.6, other=0.05), [], day,
                          request, now)
    # "rn" is right now.
    typed = [ChatMessage(role="user", content="whats for food rn")]
    assert eating_now(decision, answers, [], day, typed, now)
    # Code's own browse of the whole day's menu is the meal on now instead; a named meal
    # is still that meal's menu.
    whole_day = {"collection": "menu", "query": "", "filters": None}
    assert eating_now(RouteDecision(route="search", arguments=whole_day), answers, [], day,
                      request, now)
    dinner = {**whole_day, "filters": {"meal": "Dinner"}}
    assert not eating_now(RouteDecision(route="search", arguments=dinner), answers, [], day,
                          request, now)
    later = [ChatMessage(role="user", content="What can I eat on campus tonight?")]
    assert not eating_now(decision, answers, [], day, later, now)
    # After the day's last meal, only the hours are fetched.
    data.search.return_value = {"records": [{"fields": {"name": "Birch Tree Inn", "periods": [
        {"label": "Late Night", "start": "09:00 PM", "end": "11:00 PM"}]}}]}
    assert len(serving_now(data, now.replace(hour=23, minute=30))) == 1


@pytest.mark.parametrize("title,ended", [
    ("Residence Life › Spring 2026 - Check Out Information › Overnight Guest Policy Ends",
     "Spring 2026"),
    ("Tuition › Fall 2025 - Spring 2026 Rates", "Spring 2026"),
    ("Registrar › Fall 2026 Academic Calendar", None),
    ("Residence Life › Guest Policy", None),
])
def test_a_page_named_for_an_ended_term_says_so(title: str, ended: str | None) -> None:
    # GPT read a Spring 2026 check-out notice ("no overnight guests after May 11, 2026")
    # as a rule for this fall (09-28).
    from rockygpt_brain.retrieval.data import ended_term

    assert ended_term(title, NOW.date()) == ended


def test_document_search_skips_copies_and_puts_ended_terms_last() -> None:
    # "Overnight guest policy" (09-28): the Spring 2026 check-out page led, a word-for-word
    # copy of Guest Parking Procedures took a place, and the three-night rule never came.
    def row(index: int, title: str, content: str) -> dict[str, Any]:
        return {"id": f"chunk-{index}", "document_id": f"doc-{index}", "chunk_index": 0,
                "content": content, "metadata": {"headingPath": title}, "source_id": "s",
                "title": title, "collected_at": NOW.isoformat(), "total": 40}

    rows = [
        row(0, "Residence Life › Spring 2026 - Check Out › Overnight Guest Policy Ends",
            "The last night residents may host overnight guests is May 11, 2026."),
        row(1, "Policies › Guest Parking Procedures", "Guests parking overnight need a pass."),
        row(2, "Guide to Community Living › Guest Parking Procedures",
            "Guests  parking overnight need a pass.\n"),
        row(3, "Policies › Guest Procedures", "Each guest may stay three nights a week."),
        row(4, "Policies › Adult Guests (18+)", "Adult guests register after 10 PM."),
    ]
    data = CampusData("", NOW)
    data.sources = {"s": {"title": "Residence Life", "trust_tier": "official_primary",
                          "source_key": "reslife",
                          "freshness_sla_hours": 24, "canonical_url": "https://ramapo.edu"}}
    data.dataset = {"id": "release"}
    data._has_heading_path_index = True
    with (patch.object(CampusData, "_artifact", return_value={}),
          patch.object(CampusData, "_fetch", return_value=rows) as fetch):
        records, total = data._documents(
            SearchQuery(collection="documents", query="overnight guest policy", limit=3))
        assert [record["title"].split(" › ")[-1] for record in records] == [
            "Guest Parking Procedures", "Guest Procedures", "Adult Guests (18+)"]
        assert total == 40
        # Twice the places asked, so the copies and ended pages set aside can be refilled.
        assert fetch.call_args.args[1][-2:] == (6, 6)
        # A request that names the ended term still gets that page first.
        named, _ = data._documents(SearchQuery(
            collection="documents", query="spring 2026 overnight guests", limit=2))
        assert named[0]["title"].endswith("Overnight Guest Policy Ends")


def test_document_search_leaves_out_the_words_for_asking() -> None:
    # "financial aid ask office location" (09-29): "ask" matched every "Frequently Asked
    # Questions" page title, and scholarship FAQs took the office's place.
    data = CampusData("", NOW)
    data.dataset = {"id": "release"}
    data._has_heading_path_index = True
    with (patch.object(CampusData, "_artifact", return_value={}),
          patch.object(CampusData, "_fetch", return_value=[]) as fetch):
        data._documents(SearchQuery(collection="documents",
                                    query="financial aid ask office location", limit=4))
        assert fetch.call_args.args[1][0] == "aid OR financial OR location OR office"
        # A request made only of them keeps them.
        data._documents(SearchQuery(collection="documents", query="questions", limit=4))
        assert fetch.call_args.args[1][0] == "questions"


def test_document_search_knows_a_copy_under_another_heading_path() -> None:
    # Since 09-29 a passage opens with its page's heading path, and the Guide to Community
    # Living's copy of Guest Parking Procedures sits under other headings than the policies
    # page's: compared whole, the copy took a place from Guest Procedures.
    def row(index: int, heading: str, body: str) -> dict[str, Any]:
        return {"id": f"chunk-{index}", "document_id": f"doc-{index}", "chunk_index": 0,
                "content": f"### {heading}\n\n{body}",
                "metadata": {"headingPath": f"Residence Life › {heading}",
                             "canonicalUrl": f"https://www.ramapo.edu/reslife/{index}/"},
                "source_id": "s", "title": heading, "collected_at": NOW.isoformat(),
                "total": 9}

    rows = [
        row(0, "Guest Kiosk Locations › Guest Parking Procedures", "Guests parking overnight "
            "need a pass."),
        row(1, "RESIDENCE HALL SERVICES › Guest Kiosk Locations › Guest Parking Procedures",
            "Guests parking  overnight need a pass.\n"),
        row(2, "Guest/Visitation Policy › Guest Procedures", "Guests may stay three nights."),
        row(3, "Guest Kiosk Locations", ""),
        row(4, "Guest Kiosk Locations", ""),
    ]
    data = CampusData("", NOW)
    data.sources = {"s": {"title": "Residence Life", "trust_tier": "official_primary",
                          "source_key": "reslife", "freshness_sla_hours": 24,
                          "canonical_url": "https://ramapo.edu"}}
    data.dataset = {"id": "release"}
    data._has_heading_path_index = True
    with (patch.object(CampusData, "_artifact", return_value={}),
          patch.object(CampusData, "_fetch", return_value=rows)):
        records, _ = data._documents(
            SearchQuery(collection="documents", query="overnight guest policy", limit=3))
    # A passage that is only a heading is compared whole.
    assert [record["title"].split(" › ")[-1] for record in records] == [
        "Guest Parking Procedures", "Guest Procedures", "Guest Kiosk Locations"]


def test_document_search_lets_one_page_hold_half_the_places_and_reads_on_past_set_asides() -> None:
    # "withdrawal after deadline transcript grade W class" (09-29): the academic calendar's
    # "Last Day to Withdraw" dates held the first seven places and the Registrar's rule came
    # eighth. "class withdrawal deadline": 2021-2025 "Previous Deadlines" pages took all eight
    # places read, so no current page reached GPT.
    def row(index: int, title: str, url: str | None = None) -> dict[str, Any]:
        return {"id": f"chunk-{index}",
                "document_id": "calendar" if url is None else f"doc-{index}",
                "chunk_index": index, "content": f"{title} passage {index}.",
                "metadata": {"headingPath": title, **({"canonicalUrl": url} if url else {})},
                "source_id": "s", "title": title, "collected_at": NOW.isoformat(), "total": 900}

    calendar = [row(i, f"Academic Calendar › Fall 2026 › Last Day to Withdraw {i}")
                for i in range(7)]
    registrar = row(7, "Registrar › Withdraw from a Course", "https://www.ramapo.edu/registrar/w/")
    late = row(8, "Registrar › Late Administrative Withdrawal", "https://www.ramapo.edu/registrar/w/")
    form = row(9, "Registrar › Withdrawal Form", "https://www.ramapo.edu/registrar/w/")
    archive = [row(10 + i, f"Student Accounts › Previous Deadlines › Fall {2021 + i} Withdrawal "
                   "Deadlines", f"https://www.ramapo.edu/student-accounts/{2021 + i}/")
               for i in range(5)]
    data = CampusData("", NOW)
    data.sources = {"s": {"title": "Registrar", "trust_tier": "official_primary",
                          "source_key": "registrar", "freshness_sla_hours": 24,
                          "canonical_url": "https://ramapo.edu"}}
    data.dataset = {"id": "release"}
    data._has_heading_path_index = True
    with (patch.object(CampusData, "_artifact", return_value={}),
          patch.object(CampusData, "_fetch",
                       return_value=[*calendar, registrar, late, form]) as fetch):
        records, _ = data._documents(
            SearchQuery(collection="documents", query="withdrawal after deadline", limit=4))
        # Two calendar dates, then the Registrar's rule and the next Registrar section.
        assert [record["title"].split(" › ")[-1] for record in records] == [
            "Last Day to Withdraw 0", "Last Day to Withdraw 1", "Withdraw from a Course",
            "Late Administrative Withdrawal"]
        assert fetch.call_count == 1
        # Ten places leave a page five of them.
        wide, _ = data._documents(
            SearchQuery(collection="documents", query="withdrawal after deadline", limit=10))
        assert [record["title"].split(" › ")[-1] for record in wide][:6] == [
            *(f"Last Day to Withdraw {i}" for i in range(5)), "Withdraw from a Course"]
        assert [record["title"].split(" › ")[-1] for record in wide][-2:] == [
            "Last Day to Withdraw 5", "Last Day to Withdraw 6"]
    # The rest of a section already listed follows it: Guest Procedures' second passage
    # holds the three-night rule.
    rule = {**row(10, "Registrar › Withdraw from a Course", "https://www.ramapo.edu/registrar/w/"),
            "content": "A student may NOT withdraw after the published deadline."}
    with (patch.object(CampusData, "_artifact", return_value={}),
          patch.object(CampusData, "_fetch",
                       return_value=[registrar, late, form, rule, *calendar])):
        records, _ = data._documents(
            SearchQuery(collection="documents", query="withdrawal after deadline", limit=4))
        assert [record["content"] for record in records][:3] == [
            registrar["content"], late["content"], rule["content"]]
        assert records[3]["title"].endswith("Last Day to Withdraw 0")
    ended_first = [*archive, *calendar[:3]]
    with (patch.object(CampusData, "_artifact", return_value={}),
          patch.object(CampusData, "_fetch", side_effect=[ended_first[:4],
                                                          [*ended_first, registrar]]) as fetch):
        records, _ = data._documents(
            SearchQuery(collection="documents", query="class withdrawal deadline", limit=2))
        # The first window held only ended terms, so a wider one was read.
        assert [call.args[1][-1] for call in fetch.call_args_list] == [4, 100]
        assert [record["title"].split(" › ")[-1] for record in records] == [
            "Last Day to Withdraw 0", "Last Day to Withdraw 1"]


def test_a_place_asked_about_with_no_day_is_looked_up_for_today() -> None:
    # "What's on the menu at the Atrium" names no day; the lookup's day was left unset, so
    # code couldn't state that no menu is published today and GPT wrote (09-28).
    venue = Identity.model_validate({**IDENTITY, "kind": "venue", "name": "The Atrium",
                                     "aliases": []})
    request = [ChatMessage(role="user", content="What's on the menu at the Atrium?")]
    payload, day = routing_payload(request, [venue], NOW)
    answers = answers_for(payload, route="profile", entity=str(venue.id), detail_contact=0.0,
                          detail_menu=0.98, date="none", meal="none", diet="none")
    validate_answers(answers, payload["questions"])
    decision = interpret(answers, [venue], day, request, NOW.date().isoformat())
    assert decision.template == "menu" and decision.arguments is not None
    assert decision.arguments["date"] == NOW.date().isoformat()


def test_a_plain_day_of_events_is_listed_even_when_jev_doubts_the_kind() -> None:
    # "Whats happening on campus today": Jev was sure of a search (1.0), unsure of the
    # kind, so GPT planned it (18 s, 09-28).
    request = [ChatMessage(role="user", content="whats happening on campus today")]
    payload, day = routing_payload(request, [], NOW)
    answers = answers_for(payload, route="search", kind="other", entity="none", date="named",
                          detail_contact=0.0)
    validate_answers(answers, payload["questions"])
    decision = interpret(answers, [], day, request, NOW.date().isoformat())
    assert decision.template == "events" and decision.arguments is not None
    assert decision.arguments["collection"] == "events"
    assert decision.arguments["date_from"] == NOW.date().isoformat()


def test_a_building_lookup_names_the_places_located_in_it() -> None:
    # "The dining place in the Learning Commons" found the building, whose profile has no
    # menu, and GPT offered Birch instead (09-28).
    data = repository()
    building = {**IDENTITY, "id": "00000000-0000-4000-8000-00000000b001", "kind": "building",
                "name": "Example Learning Commons", "aliases": ["Learning Commons"],
                "links": [{"collection": "buildings", "source_key": "map",
                           "source_record_keys": ["1133431"]}]}
    venue = {**IDENTITY, "kind": "venue", "name": "Example Cafe", "aliases": [],
             "relationships": [{"type": "located_at", "target_entity_id": building["id"],
                                "evidence": [{"collection": "buildings", "source_key": "map",
                                              "source_record_key": "1133431",
                                              "field": "reviewed_locations"}]}]}
    office = {**venue, "id": "00000000-0000-4000-8000-00000000b002", "kind": "office",
              "name": "Example Library", "links": [
                  {"collection": "contacts", "source_key": "directory",
                   "source_record_keys": ["office:library"]}]}
    data._artifacts["campus-identities"]["entities"] = [building, venue, office]
    data._load = Mock(return_value=[])  # type: ignore[method-assign]
    # A menu question about the building is about its one dining place.
    menu = data.lookup_profile(ProfileQuery(entity="Learning Commons", include=["menu"]))
    assert menu["resolution"]["entity"]["name"] == "Example Cafe"
    assert menu["resolution"]["located_in"]["name"] == "Example Learning Commons"
    # Anything else lists what's inside, and says to look the right one up.
    other = data.lookup_profile(ProfileQuery(entity="Learning Commons", include=["contact"]))
    assert [item["name"] for item in other["resolution"]["located_here"]] == [
        "Example Cafe", "Example Library"]
    assert "located_here" in other["next_step"]
