"""Academic calendar dates said by code (academic_calendar.py), read through the shared reader."""

import asyncio
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from rockygpt_brain.academic_calendar import (
    entries_asked,
    kind_asked,
    term_asked,
    term_spans,
)
from rockygpt_brain.contract import ChatRequest
from rockygpt_brain.engine import ChatEngine
from rockygpt_brain.retrieval.entity_facts import MemoryEntityFacts
from rockygpt_brain.turn import intake
from test_engine import ScriptedGateway

EASTERN = ZoneInfo("America/New_York")
MONDAY = datetime(2026, 11, 2, 16, 0, tzinfo=EASTERN)
URL = "https://www.ramapo.edu/academic-calendars/"
TERMS = [{"source_key": "academic-calendar", "source_record_key": key, "name": name,
          "season": name.split()[0].lower(), "year": int(name.split()[1]), "source_url": URL}
         for key, name in (("fall-2026", "Fall 2026"), ("spring-2027", "Spring 2027"))]


def entry(day: str, title: str, kind: str, term: str, time: str | None = None) -> dict[str, Any]:
    return {"source_key": "academic-calendar", "source_record_key": f"{day}:{title}",
            "date": day, "title": title, "kind": kind, "time": time, "terms": [term],
            "source_url": URL}


DATES = [entry("2026-08-26", "Full Semester Classes Begin", "classes_begin", "Fall 2026"),
         entry("2026-11-25", "Thanksgiving Recess - No Classes", "holiday", "Fall 2026"),
         entry("2026-12-09", "Full and Session II Courses - Final Exams", "finals", "Fall 2026",
               "December 09, 12:00 am -- December 15, 11:59 pm"),
         entry("2026-12-15", "Full and Session II Courses - Last Day of the Semester",
               "classes_end", "Fall 2026"),
         entry("2027-01-20", "Full Semester Classes Begin", "classes_begin", "Spring 2027"),
         entry("2027-03-15", "Spring Break (No Classes)", "break", "Spring 2027"),
         entry("2027-05-10", "Final Exams", "finals", "Spring 2027")]


def calendar() -> MemoryEntityFacts:
    entities = [{"id": f"t-{t['source_record_key']}", "name": t["name"], "kind": "term",
                 "aliases": [], "links": [{"collection": "terms", "source_key": "academic-calendar",
                                           "source_record_keys": [t["source_record_key"]]}]}
                for t in TERMS]
    entities += [{"id": f"d-{n}", "name": e["title"], "kind": "calendar_date", "aliases": [],
                  "links": [{"collection": "calendar_dates", "source_key": "academic-calendar",
                             "source_record_keys": [e["source_record_key"]]}]}
                 for n, e in enumerate(DATES)]
    return MemoryEntityFacts(dataset_version="release-1", identity_hash="identities-1",
                             entities=entities, contacts=[],
                             records={"terms": TERMS, "calendar_dates": DATES},
                             now=lambda: MONDAY)


def test_one_kind_of_calendar_date_asked_plainly_is_codes() -> None:
    assert kind_asked("When are finals?") == "finals"
    assert kind_asked("What is the last day to withdraw from a class this semester?") == (
        "withdrawal_deadline")
    assert kind_asked("When does the spring semester start?") == "classes_begin"
    assert kind_asked("When is Thanksgiving break?") == "break"
    assert kind_asked("When is the last day to drop a class?") == "add_drop_deadline"
    for more in ("When are finals and when does spring start?",  # Two kinds.
                 "When are finals for transfer students?",  # A word it does not know.
                 "How do I study for finals?"):  # Not when.
        assert kind_asked(more) is None, more


def test_the_term_asked_is_named_next_or_in_session() -> None:
    spans = term_spans(DATES)
    today = MONDAY.date()

    def name(message: str) -> str | None:
        found = term_asked(message, TERMS, spans, today)
        return found["name"] if found else None
    assert name("When are finals?") == "Fall 2026"
    assert name("When does the spring semester start?") == "Spring 2027"
    assert name("When are finals next semester?") == "Spring 2027"
    assert name("When are fall 2026 finals?") == "Fall 2026"
    in_spring = term_asked("When are finals?", TERMS, spans, date(2027, 2, 1))
    assert in_spring is not None and in_spring["name"] == "Spring 2027"
    fall = TERMS[0]
    assert [e["title"] for e in entries_asked("When is Thanksgiving break?", "break", fall,
                                              DATES)] == ["Thanksgiving Recess - No Classes"]


def test_when_finals_are_is_answered_by_code_from_the_terms_calendar() -> None:
    gateway = ScriptedGateway()  # Any model call would fail: none is scripted.
    request = ChatRequest.model_validate({"messages": [
        {"role": "user", "content": "When are finals?"}]})
    engine = ChatEngine(gateway, calendar(), output="written")
    result = asyncio.run(engine.answer(intake(request, now=MONDAY), request))
    assert result.body["answer"] == (
        "The Fall 2026 academic calendar lists [1]:\n"
        "- Wednesday, December 9, 2026: Full and Session II Courses - Final Exams (through "
        f"December 15)\n\nSources: [1]({URL})")
    assert gateway.inputs == [] and result.trace is not None
    assert result.trace["decidedBy"] == "code"
