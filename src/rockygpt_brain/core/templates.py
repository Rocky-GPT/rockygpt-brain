"""Every answer code writes itself, as the Dev control room's Templates page shows them.

Each template says what picks it, what it reads, what it checks, and shows an example the
real template code writes from sample records. The Jev questions are read from the routing
payload, so a reworded question shows here as soon as it ships, and an example that stops
rendering shows as an error here and fails the tests.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from functools import partial
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from rockygpt_brain.campus.formats import ExactPiece, exact_search, safety_part
from rockygpt_brain.campus.profile_answers import (
    DIET_LIMITATION,
    hours_answer,
    menu_answer,
)
from rockygpt_brain.config import RELEASE
from rockygpt_brain.contracts import Answer, AnswerPart, ChatMessage
from rockygpt_brain.core.engine import CONSULTED, DROPPED_NOTE, UNVERIFIED, SafetyNet
from rockygpt_brain.core.render import consulted_sources, render_answer
from rockygpt_brain.core.routing import (
    DETAILS,
    DIET_FILTERS,
    DISH,
    MEAL_FILTERS,
    ROUTE_BAR,
    RULED_OUT,
    routing_payload,
)
from rockygpt_brain.retrieval.exact import ContactQuery, contact_answer, fact_contact_answer
from rockygpt_brain.retrieval.models import SearchFilters, SearchQuery
from rockygpt_brain.retrieval.profiles import SCHEDULE_LIMITATION, ProfileQuery

Group = Literal["jev", "gpt", "fixed"]
Evidence = dict[str, dict[str, Any]]

GROUPS = [
    {"id": "jev", "name": "Jev picks, code writes", "sharedChecks": True,
     "summary": "Jev decides the question is plain. Code looks it up, checks the records "
     "and writes the answer. GPT is never called."},
    {"id": "gpt", "name": "GPT finds, code writes", "sharedChecks": True,
     "summary": "GPT picks the lookup and quotes the question word for word. Code checks the "
     "wording and the records, then writes the answer, so no checker is needed."},
    {"id": "fixed", "name": "Set wording", "sharedChecks": False,
     "summary": "Fixed text code adds for safety, or when an answer can't be verified."},
]
# What every answer in a group with sharedChecks also passes on its way out, like GPT's.
COMMON_CHECKS = [
    "Every fact cites a fresh source with a safe https link.",
    "The answer fits in 12 paragraphs and 12,000 characters.",
    "If any check fails, GPT writes the answer and the checker reviews it, as usual.",
]

# The samples: a Monday at noon on campus.
DAY = date(2026, 9, 21)
NOW = datetime(2026, 9, 21, 12, tzinfo=ZoneInfo("America/New_York"))
BIRCH = "00000000-0000-4000-8000-00000000b1c4"
REGISTRAR = "00000000-0000-4000-8000-0000000004e9"
DINING = "https://www.ramapo.edu/dining/"


@dataclass(frozen=True)
class Condition:
    by: Literal["Jev", "GPT", "Code"]
    text: str
    needs: str = ""
    # Jev's own wording, read from the payload it is sent.
    wording: tuple[str, ...] = ()


@dataclass(frozen=True)
class AnswerTemplate:
    id: str
    name: str
    group: Group
    mode: str
    summary: str
    when: list[Condition]
    lookup: str
    reads: list[str]
    checks: list[str]
    question: str
    example: Callable[[], tuple[Answer | None, Evidence]]
    code: str
    note: str = ""
    sample_note: str = "Sample records, not today's data."
    # More examples: another question the same template answers.
    more: list[tuple[str, Callable[[], tuple[Answer | None, Evidence]]]] = field(
        default_factory=list)


def _record(record_id: str, collection: str, title: str, fields: dict[str, Any],
            **changes: Any) -> dict[str, Any]:
    return {
        "id": record_id, "entity_id": record_id, "collection": collection, "title": title,
        "url": DINING, "source_title": "Ramapo Dining", "source_key": "dining",
        "trust_tier": "official_primary", "fields": fields,
        "collected_at": "2026-09-21T10:00:00+00:00", "freshness": "fresh",
        "valid_from": DAY.isoformat(), "valid_until": DAY.isoformat(), "limitations": [],
        "coverage": {"scope": "record_fields_only",
                     "fields": {key: "published" for key in fields}},
        "content_truncated": False, **changes,
    }


def _component(ids: list[str], meal: str | None, **changes: Any) -> dict[str, Any]:
    return {
        "status": "available", "evidence_ids": ids, "fields": {}, "conflicts": {},
        "linked_records_missing": 0, "relationships_missing": 0, "failed_links": 0,
        "truncated": False, "total_matches": len(ids), "returned_count": len(ids),
        "omitted_count": 0, "service_date": DAY.isoformat(), "meal": meal, **changes,
    }


LUNCH = {
    "Grill": ["Cheeseburger", "Grilled Chicken Sandwich", "French Fries", "Sliced Tomato"],
    "Pizza": ["Pepperoni Pizza", "Cheese Pizza"],
    "Soup": ["Chicken Noodle Soup", "Oyster Crackers"],
}
# Stands in for Jev's dish pick: what it rules out as added to a dish.
NOT_DISHES = {"Sliced Tomato", "Oyster Crackers"}
PERIODS = [{"label": "Breakfast", "start": "07:30 AM", "end": "10:30 AM"},
           {"label": "Lunch", "start": "11:30 AM", "end": "02:00 PM"},
           {"label": "Dinner", "start": "05:00 PM", "end": "08:00 PM"}]


def _birch(meal: str | None, menu: bool) -> dict[str, Any]:
    """Birch Tree Inn's profile lookup for Monday: its hours, and lunch when asked."""
    hours_fields: dict[str, Any] = {
        "name": "Birch Tree Inn", "day": "Monday",
        "schedule": "07:30 AM - 10:30 AM; 11:30 AM - 02:00 PM; 05:00 PM - 08:00 PM",
        "periods": PERIODS, "availability_scope": "dining_service",
        "service_date": DAY.isoformat(),
    }
    if meal is not None:
        hours_fields.update(requested_meal=meal, meal_coverage="published",
                            requested_meal_periods=[period for period in PERIODS
                                                    if period["label"].casefold() == meal])
    hours = _record("dining_hours:birch:monday", "dining_hours", "Birch Tree Inn",
                    hours_fields, limitations=[SCHEDULE_LIMITATION], canonical_entity_id=BIRCH)
    hours["coverage"]["fields"] = {key: "published"
                                   for key in ("name", "day", "schedule", "periods")}
    records = [hours]
    components = {"hours": _component([hours["id"]], meal,
                                      availability_scope="dining_service")}
    if menu:
        dishes = [
            _record(f"menu:birch-{index}", "menu", name,
                    {"venue": "Birch Tree Inn", "meal": "Lunch", "station": station,
                     "name": name},
                    limitations=[DIET_LIMITATION], related_to_entity_id=BIRCH)
            for index, (station, name) in enumerate(
                (station, name) for station, names in LUNCH.items() for name in names)
        ]
        records = [hours, *dishes]
        components["menu"] = _component([dish["id"] for dish in dishes], meal,
                                        complete=True, diet=None)
    return {
        "status": "ok", "records": records, "total_matches": len(records), "truncated": False,
        "resolution": {"status": "matched", "entity": {"id": BIRCH, "name": "Birch Tree Inn",
                                                       "kind": "venue"}},
        "components": components,
    }


def _profile(output: dict[str, Any], answer: Answer | None) -> tuple[Answer | None, Evidence]:
    return answer, {record["id"]: record for record in output["records"]}


def _menu(full: bool) -> tuple[Answer | None, Evidence]:
    output = _birch("lunch", menu=True)
    query = ProfileQuery(entity_id=UUID(BIRCH), include=["menu", "hours"], date=DAY,
                         meal="lunch", menu_limit=100)
    dishes = {record["id"] for record in output["records"]
              if record["collection"] == "menu" and record["title"] not in NOT_DISHES}
    return _profile(output, menu_answer(output, query, full=full, dishes=dishes))


def _hours(meal: str | None) -> tuple[Answer | None, Evidence]:
    output = _birch(meal, menu=False)
    query = ProfileQuery(entity_id=UUID(BIRCH), include=["hours"], date=DAY, meal=meal)
    return _profile(output, hours_answer(output, query))


# The Registrar's campus directory entry as published on 2026-09-16.
REGISTRAR_RECORD: dict[str, Any] = {
    "id": "contacts:2f6d42fc-34bd-4739-86e3-84b66d258dc6",
    "entity_id": "campus-directory:office:registrar", "collection": "contacts",
    "title": "Registrar", "url": "https://www.ramapo.edu/campus-directory/",
    "source_title": "Campus Directory", "source_key": "campus-directory",
    "trust_tier": "official_primary",
    "fields": {"name": "Registrar", "department": "Office of the Registrar",
               "phone": "201-684-7695", "email": "registrar@ramapo.edu", "office": "D-224"},
    "collected_at": "2026-09-16T15:24:58.927000+00:00", "freshness": "fresh",
    "valid_from": None, "valid_until": None, "limitations": [],
    "coverage": {"scope": "record_fields_only", "fields": {key: "published" for key in (
        "name", "department", "phone", "email", "office")}},
    "content_truncated": False,
}


def _contact_facts() -> tuple[Answer | None, Evidence]:
    record_id = REGISTRAR_RECORD["id"]

    def fact(key: str, value: Any) -> dict[str, Any]:
        assertion = f"{record_id}#{key}"
        return {"key": key, "status": "known", "assertions": [{"id": assertion,
                                                               "limitations": []}],
                "values": [{"value": value, "assertion_ids": [assertion],
                            "supporting_evidence_ids": [record_id]}]}

    output = {
        "status": "ok", "match": "canonical_entity", "truncated": False,
        "records": [REGISTRAR_RECORD],
        "entity_facts": {
            "entity": {"id": REGISTRAR, "name": "Registrar", "kind": "office"},
            "properties_complete": True,
            "properties": [fact("phones", [{"number": "201-684-7695"}]),
                           fact("email", "registrar@ramapo.edu"), fact("offices", ["D-224"])],
            "sources": [{"id": record_id, "freshness": "fresh", "limitations": []}],
        },
    }
    return _profile(output, fact_contact_answer(output, ["phone", "email"]))


def _directory_contact() -> tuple[Answer | None, Evidence]:
    output = {"status": "ok", "match": "exact", "truncated": False,
              "records": [REGISTRAR_RECORD]}
    question = "How can I contact the Registrar?"
    query = ContactQuery(entity="Registrar",
                         fields=["phone", "email", "office", "department", "fax", "hours",
                                 "website"])
    return _profile(output, contact_answer([ChatMessage(role="user", content=question)], query,
                                           output, DAY))


def _searched(quote: str, query: SearchQuery,
              records: list[dict[str, Any]]) -> tuple[Answer | None, Evidence]:
    output = {"status": "ok", "records": records, "total_matches": len(records),
              "truncated": False}
    piece: ExactPiece | None = exact_search(
        quote, [ChatMessage(role="user", content=quote)], query, output, NOW)
    return _profile(output, piece.answer if piece else None)


def _menu_list() -> tuple[Answer | None, Evidence]:
    names = ["Pasta Primavera", "Roasted Chicken", "Garden Salad"]
    records = [
        _record(f"menu:birch-dinner-{index}", "menu", name,
                {"name": name, "meal": "Dinner", "venue": "Birch Tree Inn"})
        for index, name in enumerate(names)
    ]
    query = SearchQuery(collection="menu", date_from=DAY, date_to=DAY, limit=100,
                        filters=SearchFilters(meal="Dinner"))
    return _searched("list the dinner menu at Birch Tree Inn today", query, records)


def _hours_list() -> tuple[Answer | None, Evidence]:
    library = _record("campus_hours:library:monday", "campus_hours", "George T. Potter Library",
                      {"name": "George T. Potter Library", "day": "Monday",
                       "schedule": "08:00 AM - 11:00 PM", "service_date": DAY.isoformat()},
                      url="https://www.ramapo.edu/library/", source_title="Potter Library",
                      source_key="library", aliases=["Potter Library"])
    library["coverage"]["fields"] = {key: "published" for key in ("name", "day", "schedule")}
    query = SearchQuery(collection="campus_hours", date_from=DAY, date_to=DAY, limit=10)
    return _searched("when does Potter Library close today", query, [library])


def _shuttle() -> tuple[Answer | None, Evidence]:
    def trip(sequence: int, route: str, departure: str, stop: str,
             returned: str) -> dict[str, Any]:
        return _record(f"shuttle:{sequence}", "shuttle", route, {
            "sequence": sequence, "route": route, "service_day": "weekday",
            "service_date": DAY.isoformat(), "campus_departure": departure,
            "campus_return": returned,
            "stops": [{"location": "Ramsey train station", "time": stop,
                       "restriction": "Pickup only"}],
        }, url="https://www.ramapo.edu/shuttle/", source_title="Shuttle Schedule",
            source_key="shuttle", valid_from=None, valid_until=None)

    records = [trip(1, "Ramsey Route 17", "12:30 PM", "12:45 PM", "01:05 PM"),
               trip(2, "Ramsey Route 17", "02:30 PM", "02:45 PM", "03:05 PM"),
               trip(3, "Roadrunner Express", "01:10 PM", "01:25 PM", "01:50 PM")]
    query = SearchQuery(collection="shuttle", date_from=DAY, date_to=DAY, limit=100)
    return _searched("when is the next shuttle from campus", query, records)


SAFETY_RECORDS: list[dict[str, Any]] = [
    {"id": f"critical_facts:{key}", "entity_id": f"critical_facts:{key}",
     "title": "Campus Facts", "url": "https://www.ramapo.edu/publicsafety/",
     "collection": "critical_facts", "freshness": "static",
     "fields": {"fact_key": key, "fact_value": value}}
    for key, value in [("safety.emergency_phone", "201-684-6666"),
                       ("safety.non_emergency_phone", "201-684-7432")]
]


def _safety_block(kind: str) -> tuple[Answer | None, Evidence]:
    net = SafetyNet(kind=kind, records=SAFETY_RECORDS)
    return Answer(status="answered", parts=net.parts()), net.evidence()


def _safety_numbers() -> tuple[Answer | None, Evidence]:
    part = safety_part(SAFETY_RECORDS)
    evidence = {record["id"]: record for record in SAFETY_RECORDS}
    return (Answer(status="answered", parts=[part]) if part else None), evidence


def _unverified() -> tuple[Answer | None, Evidence]:
    _, evidence = _shuttle()  # The pages it looked at are linked, never what it concluded.
    return Answer(status="unavailable", parts=[AnswerPart(
        kind="limitation", text=UNVERIFIED + CONSULTED, evidence_ids=[])]), evidence


def _dropped() -> tuple[Answer | None, Evidence]:
    return Answer(status="partial", parts=[DROPPED_NOTE]), {}


def _choices(values: set[str]) -> str:
    ordered = sorted(values)
    return ", ".join(ordered[:-1]) + " or " + ordered[-1] if len(ordered) > 1 else ordered[0]


def templates() -> list[AnswerTemplate]:
    payload, _ = routing_payload([ChatMessage(role="user", content="What's for lunch today?")],
                                 [], NOW)

    def jev(*keys: str) -> tuple[str, ...]:
        return tuple(str(payload["questions"][key]["instructions"]) for key in keys)

    def others(*asked: str) -> tuple[str, ...]:
        return jev(*("detail_" + detail for detail in DETAILS if detail not in asked))

    def nothing_else(*asked: str) -> str:
        return "Asks nothing else: " + ", ".join(
            detail for detail in DETAILS if detail not in asked)

    place = [
        Condition("Jev", "Picks the profile route: one named place", "profile", jev("route")),
        Condition("Jev", "Picks which place", "one listed place", jev("entity")),
        Condition("Jev", "Is sure which day, or none is named (today)", "a sure day",
                  jev("date")),
    ]
    no_danger = Condition("Jev", "Reads no danger", "none", jev("danger"))
    meals = _choices(set(MEAL_FILTERS))
    menu_when = [
        *place,
        Condition("Jev", "Asks what food is served", "yes", jev("detail_menu")),
        Condition("Jev", nothing_else("menu", "hours") + " (hours may come along)", "no",
                  others("menu", "hours")),
        Condition("Jev", "Adds no condition about the food", "no", jev("menu_condition")),
        Condition("Jev", "Names one meal (code reads \"tonight\" as dinner)", meals,
                  jev("meal")),
        Condition("Jev", "Names no diet, or vegan or vegetarian",
                  _choices({"none", *DIET_FILTERS}), jev("diet")),
    ]
    menu_checks = [
        "The lookup matched the very place Jev picked, for that day and meal.",
        "The menu came back whole: every item it matched, nothing cut short, no missing "
        "links, no two records disagreeing.",
        "Every item is a fresh, official record for that place, day and meal, and its "
        "name, meal and station are published and arrived whole.",
        "With a diet, every item carries the published vegan or vegetarian label.",
        "No caveat beyond the standard allergy and nutrient notes.",
        "The meal's time is added only when the hours publish exactly one period for it.",
        "At most 50 dishes a station and 12 paragraphs.",
    ]
    return [
        AnswerTemplate(
            id="menu", name="Meal menu", group="jev", mode="exact_menu",
            summary="Lists a meal's dishes station by station, then counts the extras Jev "
            "left out.",
            when=[*menu_when,
                  Condition("Jev", "Asks for the whole menu", "no", jev("complete_menu")),
                  no_danger,
                  Condition("Jev", "After the lookup, asks of each item: is it a dish?",
                            "more likely yes", (DISH[0],))],
            lookup="lookup_profile: one place, one day, one meal, fetched whole (up to 100 "
            "items)",
            reads=["menu", "dining_hours"],
            checks=[*menu_checks,
                    "If Jev's dish pick fails, or keeps every item or none, every item is "
                    "listed."],
            question="What's for lunch at Birch Tree Inn today?",
            example=lambda: _menu(full=False),
            code="campus/profile_answers.py: menu_answer; core/routing.py: written_by_code, "
            "pick_dishes",
            sample_note="Sample records, not today's data. The dish pick stands in for Jev's.",
        ),
        AnswerTemplate(
            id="full_menu", name="Full meal menu", group="jev", mode="exact_menu",
            summary="Lists every item a meal serves, station by station.",
            when=[*menu_when,
                  Condition("Jev", "Asks for the whole menu", "yes", jev("complete_menu")),
                  no_danger],
            lookup="lookup_profile: one place, one day, one meal, fetched whole (up to 100 "
            "items)",
            reads=["menu", "dining_hours"],
            checks=menu_checks,
            question="What's the full lunch menu at Birch Tree Inn today?",
            example=lambda: _menu(full=True),
            code="campus/profile_answers.py: menu_answer; core/routing.py: written_by_code",
        ),
        AnswerTemplate(
            id="hours", name="Hours on a day", group="jev", mode="exact_hours",
            summary="States a place's published hours for one day, or one meal's hours.",
            when=[
                *place,
                Condition("Jev", "Asks when a place is open", "yes", jev("detail_hours")),
                Condition("Jev", nothing_else("hours"), "no", others("hours")),
                Condition("Jev", "Asks about no single moment, like \"now\" or \"at 9 PM\"",
                          "no", jev("at_time")),
                Condition("Jev", "Names a meal for that meal's hours, or none for the day",
                          "any", jev("meal")),
                no_danger,
            ],
            lookup="lookup_profile: one place's hours on one day",
            reads=["campus_hours", "dining_hours"],
            checks=[
                "The lookup matched the very place Jev picked, for that day.",
                "The hours came back whole, as fresh, official records for that exact day, "
                "with the name and schedule published.",
                "One schedule only: two (like a building's and its help desk's) go to GPT.",
                "The \"Hours unavailable\" placeholder goes to GPT.",
                "A meal needs exactly one published period for it.",
                "A note on the hours page is quoted word for word.",
                "A regular weekly schedule adds that special dates aren't confirmed.",
            ],
            question="When is Birch Tree Inn open today?",
            example=lambda: _hours(None),
            code="campus/profile_answers.py: hours_answer; core/routing.py: written_by_code",
            more=[("When is lunch at Birch Tree Inn today?", partial(_hours, "lunch"))],
        ),
        AnswerTemplate(
            id="contact_facts", name="Contact details", group="jev", mode="exact_facts",
            summary="States an office's phone, email or office room from the shared facts.",
            when=[
                Condition("Jev", "Picks the contact route", "contact", jev("route")),
                Condition("Jev", "Picks which office", "one listed place", jev("entity")),
                Condition("Jev", "Asks for a phone number", "clearly yes or no",
                          jev("asks_phone")),
                Condition("Jev", "Asks for an email address", "clearly yes or no",
                          jev("asks_email")),
                Condition("Jev", "Asks where it is (adds the office room)",
                          "clearly yes or no", jev("detail_location")),
                Condition("Jev", "If none of those: asks how to reach it (phone, email and "
                          "office)", "yes", jev("detail_contact")),
                Condition("Jev", "Adds no purpose, like \"for transcripts\"", "no",
                          jev("adds_purpose")),
                Condition("Jev", nothing_else("contact", "location"), "no",
                          others("contact", "location")),
                no_danger,
            ],
            lookup="lookup_contact: one office's shared entity facts",
            reads=["contacts"],
            checks=[
                "The lookup came back whole and the office's facts are complete.",
                "Each detail asked is known, with exactly one value: none unknown, "
                "conflicting or doubled.",
                "Every source behind it is fresh, has no caveat, and came back in this "
                "lookup.",
                "The value has a plain shape (a phone is a number, maybe an extension and "
                "a type).",
            ],
            question="What's the Registrar's phone and email?",
            example=_contact_facts,
            code="retrieval/exact.py: fact_contact_answer; core/routing.py: answer_fields",
            sample_note="The Registrar's directory entry as published on September 16.",
        ),
        AnswerTemplate(
            id="directory_contact", name="Directory contact", group="gpt",
            mode="exact_contact",
            summary="States an office's details from one campus directory entry.",
            when=[
                Condition("GPT", "Looks up one office with lookup_contact as its first and only "
                          "call (or Jev's contact route does)"),
                Condition("Code", "The chat's first message is a fixed contact question: "
                          "\"How can I contact X?\", \"Where is X's office?\" or \"X's "
                          "phone, email, office, department, fax, hours or website\""),
                Condition("Code", "The office isn't in the shared facts yet (those use "
                          "Contact details)"),
            ],
            lookup="lookup_contact: the directory entry with that exact name",
            reads=["contacts"],
            checks=[
                "Exactly one directory entry matches the name, and the lookup is whole.",
                "It is a fresh, official record with an https source, no caveats, that "
                "applies today.",
                "Each detail asked is published, with one value; a missing one is named as "
                "not provided.",
                "When a check fails it says what it couldn't verify, like an unknown name, "
                "instead of guessing.",
            ],
            question="How can I contact the Registrar?",
            example=_directory_contact,
            code="retrieval/exact.py: contact_answer",
            sample_note="The Registrar's directory entry as published on September 16.",
        ),
        AnswerTemplate(
            id="menu_list", name="Menu list", group="gpt", mode="exact_records",
            summary="Lists every item of one meal that a menu search found.",
            when=[
                Condition("GPT", "Searches menus for one meal and day, and quotes the request "
                          "word for word"),
                Condition("Code", "The quote is a whole part of the message and asks for the "
                          "menu, list, or all, full or complete"),
                Condition("Code", "Every other word is the meal, the dining hall's name, "
                          "vegan or vegetarian (only if searched), allergens or calories"),
            ],
            lookup="search_campus: menu, one meal, one day",
            reads=["menu"],
            checks=[
                "The day in the quote (today, tomorrow, a weekday) is the day searched.",
                "Every item is a fresh, official, whole record for that meal and day, with "
                "its name, meal and dining hall published.",
                "A vegan or vegetarian ask needs the published label on every item.",
                "A cut-off search says only part of the list came back.",
                "Asking for allergens adds that labels don't prove allergy safety.",
            ],
            question="List the dinner menu at Birch Tree Inn today",
            example=_menu_list,
            code="campus/formats.py: exact_search, menu_parts",
        ),
        AnswerTemplate(
            id="hours_list", name="Hours from a search", group="gpt", mode="exact_records",
            summary="States a place's hours, or when it closes, from an hours search.",
            when=[
                Condition("GPT", "Searches campus or dining hours for one day, and quotes "
                          "the request word for word"),
                Condition("Code", "The quote names the place and asks its hours, or when it "
                          "opens or closes, and nothing else"),
            ],
            lookup="search_campus: campus_hours or dining_hours, one day",
            reads=["campus_hours", "dining_hours"],
            checks=[
                "The whole list came back, with one schedule for that exact day.",
                "The name, schedule and day are published.",
                "\"Open now\" without \"hours\", and the \"Hours unavailable\" placeholder, "
                "go to GPT.",
                "\"When does it close?\" gets the closing time code works out from the "
                "schedule.",
                "A regular weekly schedule adds that special dates aren't confirmed.",
            ],
            question="When does Potter Library close today?",
            example=_hours_list,
            code="campus/formats.py: exact_search, hours_parts",
        ),
        AnswerTemplate(
            id="shuttle", name="Next or last shuttle", group="gpt", mode="exact_records",
            summary="States the next or last departure from campus, route by route.",
            when=[
                Condition("GPT", "Searches the whole shuttle timetable for a day, and quotes "
                          "the request word for word"),
                Condition("Code", "The quote asks for the next or the last shuttle or bus "
                          "from campus, maybe naming a route, and nothing else"),
            ],
            lookup="search_campus: shuttle, one day, every trip",
            reads=["shuttle"],
            checks=[
                "Code works out the departures from the full timetable; a cut-off one proves "
                "nothing.",
                "Every trip is a fresh, official record with clear clock times.",
                "Every route's departures from campus must be known.",
                "It says when no later departure is published, and that it's a timetable, "
                "not live bus tracking.",
            ],
            question="When is the next shuttle from campus?",
            example=_shuttle,
            code="campus/formats.py: exact_search, departure_parts; campus/schedules.py",
        ),
        AnswerTemplate(
            id="safety_block", name="Safety block", group="fixed", mode="safety_net",
            summary="Shown first when Jev reads danger, above whatever else the answer says.",
            when=[
                Condition("Jev", "Reads danger", "self-harm or danger", jev("danger")),
            ],
            lookup="Public Safety's numbers from the campus facts, no model call",
            reads=["critical_facts"],
            checks=[
                "The words are fixed in code; no model writes them.",
                "Public Safety's numbers are shown exactly as their records publish them, "
                "and left out if they can't be read.",
                "It stays even when the rest of the answer fails.",
                "Jev's templates stand down: GPT writes the rest.",
            ],
            question="I don't want to be alive anymore",
            example=partial(_safety_block, "self_harm"),
            more=[("Someone is following me on campus right now",
                   partial(_safety_block, "danger"))],
            code="campus/formats.py: SAFETY_NET, safety_part; core/engine.py: SafetyNet",
            note="Logged as safetyNet on any answer; the mode is safety_net only when the "
            "rest fails.",
        ),
        AnswerTemplate(
            id="safety_numbers", name="Public Safety numbers", group="fixed",
            mode="urgent_safety",
            summary="Adds Public Safety's numbers under GPT's urgent safety guidance.",
            when=[
                Condition("GPT", "Writes a general answer marked urgent safety, with no "
                          "campus records"),
                Condition("Code", "The safety block isn't already shown"),
            ],
            lookup="Public Safety's numbers from the campus facts, no model call",
            reads=["critical_facts"],
            checks=[
                "The numbers are shown exactly as their records publish them.",
                "GPT's guidance itself is its own; code adds only this line.",
            ],
            question="Someone is following me on campus. What do I do?",
            example=_safety_numbers,
            code="campus/formats.py: safety_part; core/engine.py",
            note="GPT's guidance comes first; code adds this line under it.",
        ),
        AnswerTemplate(
            id="unverified", name="Couldn't verify", group="fixed", mode="safe_fallback",
            summary="What a student sees when nothing drafted could be verified.",
            when=[
                Condition("Code", "GPT's draft breaks the answer format, or the checker "
                          "rejects every paragraph"),
            ],
            lookup="None: it links up to 3 pages the turn looked at",
            reads=[],
            checks=[
                "Nothing from the rejected draft is shown.",
                "Code-written parts of the request that passed their own checks are kept.",
            ],
            question="(any question whose answer fails)",
            example=_unverified,
            code="core/engine.py: fallback",
            sample_note="The second sentence appears only when a page was looked at.",
        ),
        AnswerTemplate(
            id="left_out", name="Left-out note", group="fixed", mode="reviewed_prose",
            summary="Added under GPT's answer when the checker drops some paragraphs.",
            when=[
                Condition("Code", "The checker rejects some paragraphs and passes the rest"),
            ],
            lookup="None",
            reads=[],
            checks=[
                "Paragraphs that failed are dropped, never rewritten.",
                "A caveat that only covered a dropped paragraph goes too.",
            ],
            question="(any answer with a rejected paragraph)",
            example=_dropped,
            code="core/engine.py: DROPPED_NOTE, supported_parts",
            note="Logged as reviewDroppedParts on the answer.",
            sample_note="Shown under the paragraphs that passed.",
        ),
    ]


def _example(question: str, build: Callable[[], tuple[Answer | None, Evidence]],
             note: str) -> dict[str, Any]:
    try:
        answer, evidence = build()
        if answer is None:
            raise ValueError("The template declined its sample records")
        rendered = render_answer(answer, evidence)
        sources = rendered["citations"] or consulted_sources(evidence)
        return {
            "question": question, "answer": rendered["answer"], "status": rendered["status"],
            "sources": list({source["url"]: {"title": source["title"], "url": source["url"]}
                             for source in sources}.values()),
            "note": note, "error": None,
        }
    except Exception as error:  # A template that no longer renders is shown, not hidden.
        return {"question": question, "answer": None, "status": None, "sources": [],
                "note": note, "error": f"{type(error).__name__}: {error}"}


def template_catalog() -> dict[str, Any]:
    entries = []
    for template in templates():
        examples = [_example(question, build, template.sample_note) for question, build in
                    [(template.question, template.example), *template.more]]
        entries.append({
            "id": template.id, "name": template.name, "group": template.group,
            "mode": template.mode, "summary": template.summary, "note": template.note or None,
            "when": [{"by": item.by, "text": item.text, "needs": item.needs or None,
                      "wording": list(item.wording)} for item in template.when],
            "lookup": template.lookup, "reads": template.reads, "checks": template.checks,
            "examples": examples, "code": template.code,
        })
    return {
        "jevModel": RELEASE.routing.model,
        "thresholds": {"yes": RELEASE.routing.threshold, "no": RULED_OUT, "route": ROUTE_BAR},
        "groups": GROUPS,
        "commonChecks": COMMON_CHECKS,
        "templates": entries,
    }
