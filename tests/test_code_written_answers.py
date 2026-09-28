"""The answers code writes for the 09-28 stress-test misses, and what routes to them."""

from __future__ import annotations

import copy
from datetime import datetime
from typing import Any
from unittest.mock import Mock, patch
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from rockygpt_brain.campus.formats import events_answer, exact_search, plain_event_list
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


def test_no_later_trip_to_a_stop_is_said_once() -> None:
    query = SearchQuery(collection="shuttle", date_from=NOW.date(), limit=100)
    question = "when is the next shuttle to interstate plaza"
    piece = exact_search(question, [ChatMessage(role="user", content=question)], query,
                         timetable(), NOW)
    assert piece is not None
    assert [part.text for part in piece.answer.parts] == [
        "I couldn't find a later scheduled departure from campus that reaches Interstate "
        f"Plaza on {NOW.date()}. This does not establish that service has ended after that."]


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



def test_food_right_now_fetches_the_hours_and_only_the_meal_being_served() -> None:
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
    # Between meals only the hours are fetched; no meal is assumed.
    assert len(serving_now(data, now.replace(hour=15))) == 1
    request = [ChatMessage(role="user", content="What can I actually eat on campus right now?")]
    payload, day = routing_payload(request, [], now)
    answers = answers_for(payload, route="search", kind="menu", entity="none")
    decision = RouteDecision(route="search")
    assert eating_now(decision, answers, [], day, request, now)
    unsure = answers_for(payload, route="search", kind="other", entity="none", list_menu=0.94)
    assert eating_now(decision, unsure, [], day, request, now)
    later = [ChatMessage(role="user", content="What can I eat on campus tonight?")]
    assert not eating_now(decision, answers, [], day, later, now)


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
