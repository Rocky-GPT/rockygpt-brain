"""Academic calendar dates said by code (academic_calendar.py), read through the shared reader."""

from dataclasses import asdict
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from rockygpt_brain.academic_calendar import (
    closed_on,
    dates_said,
    entries_asked,
    kind_asked,
    last_day,
    term_asked,
    term_spans,
)
from rockygpt_brain.asked_time import AskedTime
from rockygpt_brain.engine import ChatEngine
from rockygpt_brain.provider import Completion
from rockygpt_brain.retrieval.entity_facts import MemoryEntityFacts
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


def read_dates(question: str, **time: Any) -> Completion:
    """The read of a calendar question, with its kind, term and break picks (asked_time.py)."""
    from test_triage import read
    return read(question=question, time=asdict(AskedTime(**time)))


def test_one_kind_of_calendar_date_asked_plainly_is_codes() -> None:
    # The read picks the kind; code checks the question asks only when it is.
    assert kind_asked("When are finals?", "finals") == "finals"
    assert kind_asked("What is the last day to withdraw from a class this semester?",
                      "withdrawal_deadline") == "withdrawal_deadline"
    assert kind_asked("When does the spring semester start?", "classes_begin") == (
        "classes_begin")
    assert kind_asked("When is Thanksgiving break?", "break") == "break"
    for more in ("When are finals and when does spring start?",  # Two asks.
                 "When are finals for transfer students?",  # A word it does not know.
                 "How do I study for finals?"):  # Not when.
        assert kind_asked(more, "finals") is None, more
    # No kind picked, or no read: no calendar date is code's.
    assert kind_asked("When are finals?", "none") is None
    assert kind_asked("When are finals?") is None


def test_the_term_asked_is_named_next_or_in_session() -> None:
    spans = term_spans(DATES)
    today = MONDAY.date()

    def name(message: str, picked: str | None = None) -> str | None:
        found = term_asked(message, TERMS, spans, today, picked)
        return found["name"] if found else None
    assert name("When are finals?") == "Fall 2026"
    assert name("When does the spring semester start?", "spring") == "Spring 2027"
    assert name("When are finals next semester?", "next") == "Spring 2027"
    assert name("When are fall 2026 finals?", "fall") == "Fall 2026"
    in_spring = term_asked("When are finals?", TERMS, spans, date(2027, 2, 1))
    assert in_spring is not None and in_spring["name"] == "Spring 2027"
    fall = TERMS[0]
    assert [e["title"] for e in entries_asked("When is Thanksgiving break?", "break", fall,
                                              DATES, "thanksgiving")] == [
        "Thanksgiving Recess - No Classes"]


def test_when_finals_are_is_answered_by_code_from_the_terms_calendar() -> None:
    from test_triage import ask
    # Only the read is scripted: any other model call would fail.
    gateway = ScriptedGateway(read_dates("When are finals?", date_kind="finals"))
    result = ask(gateway, "When are finals?", service=calendar(), now=MONDAY)
    assert result.body["answer"] == (
        "The Fall 2026 academic calendar lists [1]:\n"
        "- Wednesday, December 9, 2026: Full and Session II Courses - Final Exams (through "
        f"December 15)\n\nSources: [1]({URL})")
    assert len(gateway.inputs) == 1 and result.trace is not None
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


def said(message: str, *, today: date = TUESDAY, **time: Any) -> str | None:
    """Code's answer, with the read's picks (`time`: the kind of date, term, break)."""
    asked = AskedTime(**time)
    kind = kind_asked(message, asked.date_kind)
    assert kind is not None, message
    found = dates_said(message, kind, {"terms": YEAR_TERMS, "dates": YEAR}, today, asked=asked)
    return found[0] if found else None


def test_a_date_spanning_days_lasts_until_its_last_day() -> None:
    assert last_day(YEAR[6]) == date(2026, 11, 29) and last_day(YEAR[0]) == date(2026, 8, 26)
    new_year = dated("2026-12-28", "Winter Recess", "break", "Winter 2027",
                     time="December 28, 12:00 am -- January 03, 11:59 pm")
    assert last_day(new_year) == date(2027, 1, 3)


def test_winter_break_is_said_by_the_dates_around_it() -> None:
    # The calendar has no "Winter Break" entry, and "winter" is no part of the winter session.
    winter: dict[str, Any] = {"date_kind": "break", "break_name": "winter"}
    assert said("When does winter break start?", **winter) == (
        "The academic calendar doesn't list a winter break by name. Between the Fall 2026 and "
        "Spring 2027 semesters it lists [1]:\n"
        "- Tuesday, December 15, 2026: Full and Session II Courses - Last Day of the Semester\n"
        "- Thursday, December 17, 2026: First Day of Winter Classes\n"
        "- Tuesday, January 19, 2027: Full and Session I Courses - Classes Begin")
    # During the break, the days already gone are marked.
    during = said("When does winter break end?", today=date(2027, 1, 5), **winter)
    assert during is not None and "2026: First Day of Winter Classes (already passed)" in during
    assert said("When does summer break start?", date_kind="break", break_name="summer") == (
        "The academic calendar doesn't list a summer break by name. Between the Spring 2027 and "
        "Fall 2027 semesters it lists [1]:\n"
        "- Tuesday, May 11, 2027: Full and Session II Courses - Last Day of the Semester\n"
        "- Wednesday, August 25, 2027: Full and Session I Courses - Classes Begin")


def test_a_break_asked_before_its_term_is_the_next_one_listed() -> None:
    assert said("When is spring break?", date_kind="break", break_name="spring") == (
        "The Spring 2027 academic calendar lists [1]:\n"
        "- Sunday, March 14, 2027: Spring Break (No Classes) (through March 21)")
    # A break the calendar does not list is said not to be, beside the days with no classes.
    assert said("When is fall break?", date_kind="break", break_name="fall") == (
        "The Fall 2026 academic calendar doesn't list a fall break [1]. The days with no classes "
        "it lists are:\n"
        "- Wednesday, November 25, 2026: Thanksgiving Break (No Classes) (through November 29)")


def test_dates_already_gone_are_marked_and_the_next_one_leaves_them_out() -> None:
    assert said("When is the last day to withdraw?", date_kind="withdrawal_deadline") == (
        "The Fall 2026 academic calendar lists [1]:\n"
        '- Friday, October 2, 2026: Session I Courses - Last Day to Withdraw ("W" Grade) '
        "(already passed)\n"
        "- Friday, November 6, 2026: Full Semester Courses - Last Day to Withdraw from Courses")
    assert said("When is the next break?", date_kind="break") == (
        "The Fall 2026 academic calendar lists [1]:\n"
        "- Wednesday, November 25, 2026: Thanksgiving Break (No Classes) (through November 29)")


def test_a_kind_of_date_all_gone_this_term_is_the_next_terms_unless_the_past_is_asked() -> None:
    add_drop: dict[str, Any] = {"date_kind": "add_drop_deadline"}
    assert said("When is the add/drop deadline?", **add_drop) == (
        "The Spring 2027 academic calendar lists [1]:\n"
        "- Monday, January 25, 2027: Full Semester Courses - Last Day of Add/Drop for 100% "
        "Tuition Refund")
    assert said("When was the add/drop deadline?", **add_drop) == (
        "The Fall 2026 academic calendar lists [1]:\n"
        "- Tuesday, September 1, 2026: Full Semester Courses - Last Day of Add/Drop for 100% "
        "Tuition Refund (already passed)")
    named = said("When was the fall 2026 add/drop deadline?", **add_drop, term="fall")
    assert named is not None and named.startswith("The Fall 2026 academic calendar")


def test_winter_break_from_slang_is_codes_answer_with_no_model_call() -> None:
    from test_triage import ask
    gateway = ScriptedGateway(read_dates("When does winter break start?", date_kind="break",
                                         break_name="winter"))
    result = ask(gateway, "wen does winter brk start", service=calendar(YEAR_TERMS, YEAR),
                 now=datetime(2026, 11, 3, 9, 0, tzinfo=EASTERN))
    assert result.body["answer"].startswith(
        "The academic calendar doesn't list a winter break by name.")
    assert result.body["answer"].endswith(f"Sources: [1]({URL})")
    assert len(gateway.inputs) == 1 and result.trace is not None
    assert result.trace["decidedBy"] == "code"


GRADING = [*YEAR,
           dated("2026-10-30", "Full Semester Courses - Last Day to Request Pass/Fail or Audit",
                 "grading_option_deadline", "Fall 2026"),
           dated("2026-12-04", "Full Semester Courses - Last Day to Request an Incomplete",
                 "grading_option_deadline", "Fall 2026"),
           dated("2027-03-26", "Full Semester Courses - Last Day to Request Pass/Fail or Audit",
                 "grading_option_deadline", "Spring 2027")]


def said_of(message: str, dates: list[dict[str, Any]], *, today: date = TUESDAY,
            **time: Any) -> str | None:
    from rockygpt_brain.academic_calendar import kind_named
    asked = AskedTime(**time)
    kind = kind_named(message, asked.date_kind)
    assert kind is not None, message
    found = dates_said(message, kind, {"terms": YEAR_TERMS, "dates": dates}, today, asked=asked)
    return found[0] if found else None


def test_can_i_still_asks_this_terms_date_even_when_it_has_passed() -> None:
    # Fix check 11-04: "can i still switch one of my classes to audit" got the spring date, and
    # "can i still add a class this semester" was read as finals.
    grading: dict[str, Any] = {"date_kind": "grading_option_deadline"}
    assert said_of("can i still switch one of my classes to audit", GRADING, **grading) == (
        "The Fall 2026 academic calendar lists [1]:\n"
        "- Friday, October 30, 2026: Full Semester Courses - Last Day to Request Pass/Fail or "
        "Audit (already passed)")
    assert said_of("when is the last day to take an incomplete", GRADING, **grading) == (
        "The Fall 2026 academic calendar lists [1]:\n"
        "- Friday, December 4, 2026: Full Semester Courses - Last Day to Request an Incomplete")
    added = said_of("can i still add a class this semester", GRADING,
                    date_kind="add_drop_deadline", term="this")
    assert added is not None and added.startswith("The Fall 2026 academic calendar lists [1]:")
    assert "Add/Drop for 100% Tuition Refund (already passed)" in added
    assert kind_asked("when are finals", "finals") == "finals"


def test_an_entry_another_term_also_lists_says_so() -> None:
    # The winter session's first day is on the fall calendar too: said as the winter's.
    found = said_of("when do classes begin this semester", YEAR, date_kind="classes_begin",
                    term="this")
    assert found is not None
    assert ("Thursday, December 17, 2026: First Day of Winter Classes (also on the Winter 2027 "
            "calendar)") in found


def test_the_breaks_an_event_question_can_name() -> None:
    from rockygpt_brain.academic_calendar import break_spans
    spans = break_spans({"terms": YEAR_TERMS, "dates": YEAR}, TUESDAY)
    assert ("Thanksgiving Break (No Classes)", date(2026, 11, 25), date(2026, 11, 29)) in spans
    assert ("Spring Break (No Classes)", date(2027, 3, 14), date(2027, 3, 21)) in spans
    assert ("Winter Break", date(2026, 12, 16), date(2027, 1, 18)) in spans
    assert ("Summer Break", date(2027, 5, 12), date(2027, 8, 24)) in spans


def test_a_day_the_calendar_marks_college_closed_is_said_with_its_page() -> None:
    year = {"dates": YEAR}
    said = closed_on(year, date(2026, 9, 7))
    assert said == (f"Ramapo's [academic calendar]({URL}) lists Monday, September 7, 2026 as "
                    "Labor Day (College Closed - No Classes).")
    # "No Classes" alone is no closure: offices keep their hours over a break.
    assert closed_on(year, date(2026, 11, 26)) is None
    assert closed_on(year, date(2026, 9, 8)) is None
    # A span closes every day in it; an entry with no page is never said.
    span = entry("2026-12-24", "College Closed", "holiday", "Fall 2026",
                 "December 24, 12:00 am -- January 01, 11:59 pm")
    assert closed_on({"dates": [span]}, date(2027, 1, 1)) is not None
    assert closed_on({"dates": [{**span, "source_url": None}]}, date(2026, 12, 25)) is None


def test_the_engine_reads_the_closure_from_the_release_calendar() -> None:
    engine = ChatEngine(ScriptedGateway(), calendar(dates=YEAR), output="written")
    assert engine._closed_on(date(2026, 9, 7)) is not None
    assert engine._closed_on(date(2026, 9, 8)) is None
    assert ChatEngine(ScriptedGateway(), calendar(), output="written")._closed_on(
        date(2026, 9, 7)) is None
