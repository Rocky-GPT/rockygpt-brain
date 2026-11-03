"""Academic calendar dates said by code (academic_calendar.py), read through the shared reader."""

import asyncio
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from rockygpt_brain.academic_calendar import (
    dates_said,
    entries_asked,
    kind_asked,
    last_day,
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


def calendar(terms: list[dict[str, Any]] | None = None,
             dates: list[dict[str, Any]] | None = None) -> MemoryEntityFacts:
    terms, dates = terms or TERMS, dates or DATES
    entities = [{"id": f"t-{t['source_record_key']}", "name": t["name"], "kind": "term",
                 "aliases": [], "links": [{"collection": "terms", "source_key": "academic-calendar",
                                           "source_record_keys": [t["source_record_key"]]}]}
                for t in terms]
    entities += [{"id": f"d-{n}", "name": e["title"], "kind": "calendar_date", "aliases": [],
                  "links": [{"collection": "calendar_dates", "source_key": "academic-calendar",
                             "source_record_keys": [e["source_record_key"]]}]}
                 for n, e in enumerate(dates)]
    return MemoryEntityFacts(dataset_version="release-1", identity_hash="identities-1",
                             entities=entities, contacts=[],
                             records={"terms": terms, "calendar_dates": dates},
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


# 11-03: dates start from today. The terms and entries as the live calendar publishes them
# (2026-11-03), with the winter session between the semesters.
YEAR_TERMS = [{"source_key": "academic-calendar",
               "source_record_key": name.lower().replace(" ", "-"), "name": name,
               "season": name.split()[0].lower(), "year": int(name.split()[1]), "source_url": URL}
              for name in ("Fall 2026", "Winter 2027", "Spring 2027", "Fall 2027")]


def dated(day: str, title: str, kind: str, *terms: str, time: str | None = None,
          ) -> dict[str, Any]:
    return {**entry(day, title, kind, terms[0], time), "terms": list(terms)}


YEAR = [
    dated("2026-08-26", "Full and Session I Courses - Classes Begin", "classes_begin", "Fall 2026"),
    dated("2026-09-01", "Full Semester Courses - Last Day of Add/Drop for 100% Tuition Refund",
          "add_drop_deadline", "Fall 2026"),
    dated("2026-09-07", "Labor Day (College Closed - No Classes)", "holiday", "Fall 2026"),
    dated("2026-10-02", 'Session I Courses - Last Day to Withdraw ("W" Grade)',
          "withdrawal_deadline", "Fall 2026"),
    dated("2026-10-16", "Session I Courses - Last Day of the Session", "classes_end", "Fall 2026"),
    dated("2026-11-06", "Full Semester Courses - Last Day to Withdraw from Courses",
          "withdrawal_deadline", "Fall 2026"),
    dated("2026-11-25", "Thanksgiving Break (No Classes)", "break", "Fall 2026",
          time="November 25, 12:00 am -- November 29, 11:59 pm"),
    dated("2026-12-15", "Full and Session II Courses - Last Day of the Semester", "classes_end",
          "Fall 2026"),
    dated("2026-12-17", "First Day of Winter Classes", "classes_begin", "Fall 2026",
          "Winter 2027"),
    dated("2027-01-13", "Finals and the Last Day of Winter Semester", "finals", "Winter 2027"),
    dated("2027-01-19", "Full and Session I Courses - Classes Begin", "classes_begin",
          "Winter 2027", "Spring 2027"),
    dated("2027-01-25", "Full Semester Courses - Last Day of Add/Drop for 100% Tuition Refund",
          "add_drop_deadline", "Spring 2027"),
    dated("2027-03-14", "Spring Break (No Classes)", "break", "Spring 2027",
          time="March 14, 12:00 am -- March 21, 11:59 pm"),
    dated("2027-05-11", "Full and Session II Courses - Last Day of the Semester", "classes_end",
          "Spring 2027"),
    dated("2027-08-25", "Full and Session I Courses - Classes Begin", "classes_begin",
          "Fall 2027"),
]
TUESDAY = date(2026, 11, 3)


def said(message: str, today: date = TUESDAY) -> str | None:
    kind = kind_asked(message)
    assert kind is not None, message
    found = dates_said(message, kind, {"terms": YEAR_TERMS, "dates": YEAR}, today)
    return found[0] if found else None


def test_a_date_spanning_days_lasts_until_its_last_day() -> None:
    assert last_day(YEAR[6]) == date(2026, 11, 29) and last_day(YEAR[0]) == date(2026, 8, 26)
    new_year = dated("2026-12-28", "Winter Recess", "break", "Winter 2027",
                     time="December 28, 12:00 am -- January 03, 11:59 pm")
    assert last_day(new_year) == date(2027, 1, 3)


def test_winter_break_is_said_by_the_dates_around_it() -> None:
    # The calendar has no "Winter Break" entry, and "winter" is no part of the winter session.
    assert said("When does winter break start?") == (
        "The academic calendar doesn't list a winter break by name. Between the Fall 2026 and "
        "Spring 2027 semesters it lists [1]:\n"
        "- Tuesday, December 15, 2026: Full and Session II Courses - Last Day of the Semester\n"
        "- Thursday, December 17, 2026: First Day of Winter Classes\n"
        "- Tuesday, January 19, 2027: Full and Session I Courses - Classes Begin")
    # During the break, the days already gone are marked.
    during = said("When does winter break end?", date(2027, 1, 5))
    assert during is not None and "2026: First Day of Winter Classes (already passed)" in during
    assert said("When does summer break start?") == (
        "The academic calendar doesn't list a summer break by name. Between the Spring 2027 and "
        "Fall 2027 semesters it lists [1]:\n"
        "- Tuesday, May 11, 2027: Full and Session II Courses - Last Day of the Semester\n"
        "- Wednesday, August 25, 2027: Full and Session I Courses - Classes Begin")


def test_a_break_asked_before_its_term_is_the_next_one_listed() -> None:
    assert said("When is spring break?") == (
        "The Spring 2027 academic calendar lists [1]:\n"
        "- Sunday, March 14, 2027: Spring Break (No Classes) (through March 21)")
    # A break the calendar does not list is said not to be, beside the days with no classes.
    assert said("When is fall break?") == (
        "The Fall 2026 academic calendar doesn't list a fall break [1]. The days with no classes "
        "it lists are:\n"
        "- Wednesday, November 25, 2026: Thanksgiving Break (No Classes) (through November 29)")


def test_dates_already_gone_are_marked_and_the_next_one_leaves_them_out() -> None:
    assert said("When is the last day to withdraw?") == (
        "The Fall 2026 academic calendar lists [1]:\n"
        '- Friday, October 2, 2026: Session I Courses - Last Day to Withdraw ("W" Grade) '
        "(already passed)\n"
        "- Friday, November 6, 2026: Full Semester Courses - Last Day to Withdraw from Courses")
    assert said("When is the next break?") == (
        "The Fall 2026 academic calendar lists [1]:\n"
        "- Wednesday, November 25, 2026: Thanksgiving Break (No Classes) (through November 29)")


def test_a_kind_of_date_all_gone_this_term_is_the_next_terms_unless_the_past_is_asked() -> None:
    assert said("When is the add/drop deadline?") == (
        "The Spring 2027 academic calendar lists [1]:\n"
        "- Monday, January 25, 2027: Full Semester Courses - Last Day of Add/Drop for 100% "
        "Tuition Refund")
    assert said("When was the add/drop deadline?") == (
        "The Fall 2026 academic calendar lists [1]:\n"
        "- Tuesday, September 1, 2026: Full Semester Courses - Last Day of Add/Drop for 100% "
        "Tuition Refund (already passed)")
    named = said("When was the fall 2026 add/drop deadline?")
    assert named is not None and named.startswith("The Fall 2026 academic calendar")


def test_winter_break_from_slang_is_codes_answer_with_no_model_call() -> None:
    from test_triage import ask, read
    gateway = ScriptedGateway(read(question="When does winter break start?"))
    result = ask(gateway, "wen does winter brk start", service=calendar(YEAR_TERMS, YEAR),
                 now=datetime(2026, 11, 3, 9, 0, tzinfo=EASTERN))
    assert result.body["answer"].startswith(
        "The academic calendar doesn't list a winter break by name.")
    assert result.body["answer"].endswith(f"Sources: [1]({URL})")
    assert len(gateway.inputs) == 1 and result.trace is not None
    assert result.trace["decidedBy"] == "code"
