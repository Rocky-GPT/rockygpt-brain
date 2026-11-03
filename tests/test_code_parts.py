"""Code writes as much of each answer as it can (Dan 11-03): a program's faculty and convener
by the catalog's graph links, the academic calendar's dates of the kind a question names, and the
calendar events code chose; the model words only what else is asked."""

import asyncio
import json
import re
from datetime import datetime, timedelta
from typing import Any

from rockygpt_brain import events
from rockygpt_brain.academic_calendar import kind_named
from rockygpt_brain.contract import ChatRequest
from rockygpt_brain.engine import ChatEngine
from rockygpt_brain.people import asks_faculty_list, asks_program_lead, faculty_text
from rockygpt_brain.retrieval.entity_facts import MemoryEntityFacts
from rockygpt_brain.turn import CAMPUS_TZ, intake
from test_academic_calendar import TERMS, URL, entry
from test_engine import NOW, ScriptedGateway
from test_meaning import meaning_service
from test_one_call import PartsGateway, one_reply

CATALOG = "https://catalog.ramapo.edu/programs/SN-BS-BIOL"


def person(key: str, name: str, title: str) -> tuple[dict[str, Any], dict[str, Any],
                                                     dict[str, Any]]:
    contact = {"id": f"c-{key}", "source_key": "faculty", "source_record_key": f"faculty:{key}",
               "name": name, "title": title, "department": "School of Science",
               "email": f"{key}@ramapo.edu", "collected_at": NOW, "freshness_sla_hours": 4320,
               "canonical_url": "https://www.ramapo.edu/snh/"}
    profile = {"id": f"f-{key}", "source_key": "faculty", "source_record_key": f"page-{key}",
               "collected_at": NOW, "freshness_sla_hours": 4320,
               "profile": {"profileUrl": f"https://www.ramapo.edu/snh/faculty/{key}/"}}
    entity = {"id": key, "name": name, "kind": "person", "aliases": [],
              "links": [{"collection": "contacts", "source_key": "faculty",
                         "source_record_keys": [f"faculty:{key}"]},
                        {"collection": "faculty", "source_key": "faculty",
                         "source_record_keys": [f"page-{key}"]}]}
    return entity, contact, profile


def link(kind: str, target: str) -> dict[str, Any]:
    return {"type": kind, "target_entity_id": target,
            "evidence": [{"field": "customFields", "collection": "programs",
                          "source_key": "academic-programs", "source_url": CATALOG,
                          "source_record_key": "catalog:SN-BS-BIOL"}]}


def biology() -> MemoryEntityFacts:
    people = [person("rain", "Emma Rainforth", "Professor of Environmental Science"),
              person("bacon", "Eta Bacon", "Professor of Biology"),
              person("xu", "Yan Xu", "Professor of Biology"),
              person("old", "Ellen Old", "Professor of Biology")]
    entities = [p[0] for p in people]
    entities[3]["status"] = {"state": "retired", "evidence": []}
    entities += [
        {"id": "bio-bs", "name": "Biology BS", "kind": "program", "aliases": [], "links": [],
         "relationships": [link("convener", "rain"), link("listed_faculty", "bacon"),
                           link("listed_faculty", "rain"), link("listed_faculty", "xu"),
                           link("listed_faculty", "old")]},
        {"id": "bio-mn", "name": "Biology Minor", "kind": "program", "aliases": [], "links": [],
         "relationships": [link("convener", "rain"), link("listed_faculty", "xu")]},
        {"id": "chem-bs", "name": "Chemistry BS", "kind": "program", "aliases": [], "links": [],
         "relationships": [link("listed_faculty", "bacon")]}]
    return MemoryEntityFacts(dataset_version="release-1", identity_hash="identities-1",
                             entities=entities, contacts=[p[1] for p in people],
                             records={"faculty": [p[2] for p in people]}, now=lambda: NOW)


def ask(service: Any, gateway: Any, question: str, now: datetime = NOW) -> Any:
    request = ChatRequest.model_validate({"messages": [{"role": "user", "content": question}]})
    engine = ChatEngine(gateway, service, output="written", howto_one_call=True)
    return asyncio.run(engine.answer(intake(request, now=now), request))


def test_a_program_lists_its_people_by_its_catalog_links() -> None:
    (bs, minor) = biology().program_people(["bio-bs", "bio-mn"], "release-1")
    assert (bs["conveners"], bs["faculty"], bs["retired"]) == (
        ["rain"], ["bacon", "rain", "xu", "old"], ["old"])
    assert bs["url"] == CATALOG and minor["faculty"] == ["xu"]


def test_a_faculty_list_or_a_program_lead_is_asked_and_one_best_professor_is_not() -> None:
    assert asks_faculty_list("can you give me a list of all the professors in computer science")
    assert asks_faculty_list("who teaches biology? is there a list of the bio faculty")
    assert not asks_faculty_list("who is the best professor for intro to biology")
    assert not asks_faculty_list("what time does the library open")
    assert asks_program_lead("who is the accounting director or chair?")
    assert not asks_program_lead("who is the dean of the school of business")


def test_the_faculty_are_said_once_each_conveners_first() -> None:
    programs = [{"name": "Biology BS", "n": 1, "conveners": ["rain"],
                 "faculty": ["bacon", "rain", "old"], "retired": ["old"]},
                {"name": "Biology Minor", "n": 1, "conveners": ["rain"], "faculty": ["bacon"],
                 "retired": []}]
    people = {"rain": {"name": "Emma Rainforth", "title": "Professor", "email": "r@x.edu",
                       "n": 2},
              "bacon": {"name": "Eta Bacon", "n": 3},
              "old": {"name": "Ellen Old", "email": "o@x.edu", "n": 4}}
    assert faculty_text("Biology", programs, people) == (
        "The college catalog lists 3 people as faculty of Biology (Biology BS, Biology Minor) "
        "[1]:\n"
        "- Emma Rainforth, program convener, Professor, r@x.edu [2]\n"
        "- Eta Bacon [3]\n"
        "- Ellen Old, listed as retired in the directory, o@x.edu [4]")
    assert faculty_text("Biology", programs, people, conveners_only=True) == (
        "The college catalog names the program convener of Biology (Biology BS, Biology Minor) "
        "[1]:\n- Emma Rainforth, program convener, Professor, r@x.edu [2]")


def test_a_list_of_a_programs_faculty_is_written_by_code_with_no_model_call() -> None:
    gateway = ScriptedGateway()  # Any model call would fail: none is scripted.
    result = ask(biology(), gateway, "can you list all the biology professors")
    answer = result.body["answer"]
    assert result.trace is not None and result.trace["decidedBy"] == "code"
    assert gateway.inputs == []
    assert answer.startswith("The college catalog lists 4 people as faculty of Biology "
                             "(Biology BS, Biology Minor) [1]:\n- Emma Rainforth, program "
                             "convener, Professor of Environmental Science, rain@ramapo.edu [2]")
    for name in ("Eta Bacon", "Yan Xu", "Ellen Old, Professor of Biology, listed as retired"):
        assert name in answer
    assert "Chemistry" not in answer and f"[1]({CATALOG})" in answer
    # Who leads it: its convener, by the same links.
    led = ask(biology(), ScriptedGateway(), "who is the chair of the biology program")
    assert led.body["answer"].startswith(
        "The college catalog names the program convener of Biology")
    assert "Eta Bacon" not in led.body["answer"]


def with_withdrawal_dates() -> Any:
    service = meaning_service()
    dates = [entry("2026-10-02", 'Session I Courses - Last Day to Withdraw ("W" Grade)',
                   "withdrawal_deadline", "Fall 2026"),
             entry("2026-11-06", "Full Semester Courses – Last Day to Withdraw (“W” Grade)",
                   "withdrawal_deadline", "Fall 2026"),
             entry("2026-08-26", "Full Semester Classes Begin", "classes_begin", "Fall 2026"),
             entry("2026-12-15", "Last Day of the Semester", "classes_end", "Fall 2026")]
    service.entities += [{"id": f"t-{t['source_record_key']}", "name": t["name"],
                          "kind": "term", "aliases": [],
                          "links": [{"collection": "terms", "source_key": "academic-calendar",
                                     "source_record_keys": [t["source_record_key"]]}]}
                         for t in TERMS]
    service.entities += [{"id": f"d-{n}", "name": e["title"], "kind": "calendar_date",
                          "aliases": [],
                          "links": [{"collection": "calendar_dates",
                                     "source_key": "academic-calendar",
                                     "source_record_keys": [e["source_record_key"]]}]}
                         for n, e in enumerate(dates)]
    service.records = {**service.records, "terms": TERMS, "calendar_dates": dates}
    return service


def test_a_deadline_named_beside_another_ask_is_said_by_code_first() -> None:
    assert kind_named("what's the deadline to withdraw from a course and what shows up on my "
                      "transcript if i do") == "withdrawal_deadline"
    assert kind_named("how do I withdraw from a course") is None  # Not when.
    # Two kinds.
    assert kind_named("when are finals and when does the spring semester start") is None
    gateway = PartsGateway(one_reply(
        "Center for Reading and Writing: Apply with the online form by March 1. [1]", [1]))
    result = ask(with_withdrawal_dates(), gateway,
                 "what's the deadline to withdraw from a course and how do i become a peer tutor")
    answer = result.body["answer"]
    assert result.trace is not None and result.trace["decidedBy"] == "one_call"
    # The calendar's dates before the model's words, and the cited office's contacts after.
    assert result.trace["codeParts"] == 2
    assert "\n\n**Center for Reading and Writing**: Email: tutoring@example.edu. [" in answer
    (n,) = {int(m) for m in re.findall(r"academic calendar lists \[(\d+)\]", answer)}
    assert answer.startswith(
        f"The Fall 2026 academic calendar lists [{n}]:\n"
        '- Friday, October 2, 2026: Session I Courses - Last Day to Withdraw ("W" Grade)\n'
        "- Friday, November 6, 2026: Full Semester Courses – Last Day to Withdraw "
        "(“W” Grade)\n\nCenter for Reading and Writing: Apply with the online form by "
        "March 1. [1]")
    assert f"[{n}]({URL})" in answer
    # The model is told what code wrote, and writes only the rest.
    given = json.loads(gateway.inputs[0][1]["content"])
    assert given["already_written"].startswith("The Fall 2026 academic calendar lists")


def test_code_says_every_event_it_chose_for_a_topic_and_none_when_none_matches() -> None:
    start = datetime(2026, 11, 3, 9, tzinfo=CAMPUS_TZ)
    end = start + timedelta(days=5)

    def line(n: int, title: str, day: int, by: str | None) -> dict[str, Any]:
        value = {"title": title, "start_time": "1 PM", "organizer": "Mackin Hall",
                 "starts_at": datetime(2026, 11, day, 13, tzinfo=CAMPUS_TZ).isoformat(),
                 **({"_matched_by": by} if by else {})}
        return {"n": n, "_value": value}
    chosen = [line(2, "Therapy Dogs", 3, "words"), line(3, "Donut Stress", 5, "meaning")]
    said = events.topic_text("this week", start, end, 40, chosen, True)
    assert said == (
        "The calendar lists 1 event this week, Tuesday, November 3 to Sunday, November 8 "
        "that matches what you asked (of 40 events then) [1]:\n\n"
        "Tuesday, November 3:\n- 1 PM: Therapy Dogs (Mackin Hall) [2]\n\n"
        "These are only near it in meaning, not named by their listings:\n\n"
        "Thursday, November 5:\n- 1 PM: Donut Stress (Mackin Hall) [3]")
    none = events.topic_text(events.DEFAULT_LABEL, start, end, 40,
                             [line(2, "Card Night", 3, None)], False)
    assert none is not None and none.startswith("The calendar lists no event in the next 14 "
                                                 "days") and "Card Night" not in none
    # "What shows up on my transcript" asks about no show.
    assert not events.asks_events("what shows up on my transcript if i withdraw")
    assert events.asks_events("any shows this weekend")


def test_an_offices_page_sections_are_said_by_code_once_in_their_own_words() -> None:
    from rockygpt_brain.plain_writer import plain_text
    section = {"heading": "How to Report an Incident", "page": "https://x.edu/report/",
               "text": "Call (201) 684-6666. " + "Give details. " * 60}
    view = {"facts": [
        {"subject": "Public Safety", "predicate": "phones",
         "value": [{"number": "+12016846666"}], "sources": [1]},
        {"subject": "Public Safety", "predicate": "procedures", "value": section,
         "sources": [2]},
        {"subject": "Public Safety (Emergency)", "predicate": "procedures", "value": section,
         "sources": [2]}]}
    said = plain_text(view)
    assert said is not None
    assert said.startswith("Public Safety phone: (201) 684-6666. [1]\n\n"
                           'Public Safety\'s page "How to Report an Incident" says: "Call (201) '
                           '684-6666. Give details.')
    assert said.endswith('Give details." (more on its page) [2]')
    assert said.count("How to Report an Incident") == 1  # Shared by both lines: said once.


def test_evidence_text_shown_without_a_writer_is_tidied_for_a_student() -> None:
    from rockygpt_brain.answers import tidy_evidence
    raw = ("**Health Services**\n\n"
           "Phones: +12016847536 [campus-directory](https://a.edu/1) "
           "[campus-directory](https://a.edu/2)\n\n"
           "Fax: not published on the reviewed official pages (checked 2026-10-25) "
           "[office-pages](https://a.edu/3). Scope: the office's own pages\n\n"
           "How-to and policies: Hours: Open daily. (see https://a.edu/4) "
           "[office-pages](https://a.edu/4)\n\n"
           "**Counseling**\n\n"
           "How-to and policies: Hours: Open daily. (see https://a.edu/5) "
           "[office-pages](https://a.edu/5)")
    assert tidy_evidence(raw) == (
        "**Health Services**\n\n"
        "Phones: (201) 684-7536 [Source](https://a.edu/1)\n\n"
        "Fax: not published on the reviewed official pages [Source](https://a.edu/3)\n\n"
        "Hours: Open daily. [Source](https://a.edu/4)")
