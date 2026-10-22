"""Majors, minors and certificates: which program a question names, and its requirements written
out by code from the catalog's own rules (academics.py)."""

import asyncio
import json
from datetime import date
from typing import Any

import pytest

from rockygpt_brain.academics import (
    also_published,
    asks_academics,
    by_meaning,
    catalog_listing,
    choose_plan,
    course_codes,
    course_evidence,
    find_programs,
    program_level,
    render_plan,
    render_requirements,
    render_requisites,
    requisite_codes,
)
from rockygpt_brain.contract import ChatRequest
from rockygpt_brain.engine import ChatEngine
from rockygpt_brain.provider import Completion, ToolCall, Usage
from rockygpt_brain.retrieval.entity_facts import EvidenceUnavailable, MemoryEntityFacts
from rockygpt_brain.turn import intake
from test_engine import NOW, ScriptedGateway

PROGRAMS = [{"id": f"p{n}", "name": name, "aliases": []} for n, name in enumerate([
    "Computer Science BS", "Computer Science Minor", "Computer Science MS",
    "Computer Science 4+1", "Data Science BS", "Data Science MS", "Psychology BA",
    "Psychology Minor", "Nursing Generic BSN", "Nursing RN to BSN", "Nursing MSN",
    "Data Analyst - Graduate Certificate"])]


def names(found: list[dict[str, Any]]) -> list[str]:
    return [p["name"] for p in found]


def test_a_program_is_named_by_its_core_name_and_its_level_by_the_questions_words() -> None:
    assert names(find_programs(PROGRAMS, "what do I need for the computer science major")) == [
        "Computer Science BS"]
    assert names(find_programs(PROGRAMS, "computer science minor requirements")) == [
        "Computer Science Minor"]
    assert names(find_programs(PROGRAMS, "is there a masters in data science")) == [
        "Data Science MS"]
    assert names(find_programs(PROGRAMS, "computer science 4+1")) == ["Computer Science 4+1"]
    # With no level word, the undergraduate major; the other levels are named beside it.
    found = find_programs(PROGRAMS, "computer science requirements")
    assert names(found) == ["Computer Science BS"]
    assert also_published(PROGRAMS, found) == [
        "Computer Science 4+1", "Computer Science MS", "Computer Science Minor"]
    # The longest core wins: "data science" is not "science".
    assert names(find_programs(PROGRAMS, "data science classes")) == ["Data Science BS"]
    # Several undergraduate programs share a core name: all of them, at most three.
    assert names(find_programs(PROGRAMS, "nursing requirements")) == [
        "Nursing Generic BSN", "Nursing RN to BSN"]
    assert find_programs(PROGRAMS, "how do i get a parking permit") == []
    assert program_level("Data Analyst - Graduate Certificate") == "certificate"
    assert program_level("Nursing MSN") == "graduate"


def test_requirements_are_written_out_as_the_catalog_publishes_them() -> None:
    groups = [
        {"label": "Major Requirements", "note": None, "shape": "rule", "course_list": None,
         "rule": {"name": "Required Courses", "choose": {"all": True}, "note": None,
                  "credits": None, "sub_rules": [], "items": [
                      {"logic": "and", "courses": [{"code": "CMPS 147",
                                                    "name": "COMPUTER SCIENCE I"}]},
                      {"logic": "or", "courses": [{"code": "MATH 205", "name": "DISCRETE MATH"},
                                                  {"code": "MATH 237", "name": "PROOFS"}]},
                      {"logic": "and", "courses": [{"code": "CHEM 206", "name": "ORGANIC"},
                                                   {"code": "CHEM 206L", "name": "LAB"}]},
                      {"logic": "and", "courses": [{"code": "DztNY 9pVq0reiUAyjEak",
                                                    "name": ""}]}]}},
        {"label": "Electives - Select 2", "note": None, "shape": "rule", "course_list": None,
         "rule": {"name": "Select 2 Courses", "choose": {"at_least": 2},
                  "note": "Must total 8 credits.", "credits": None, "sub_rules": [],
                  "items": [{"logic": "or",
                             "courses": [{"code": "CMPS 240", "name": "TOPICS:"}]}]}},
        {"label": "Catalog Requirements", "note": "Students may take up to three graduate "
         "courses.", "shape": "text", "rule": None, "course_list": None},
    ]
    text = render_requirements(groups)
    assert "Required Courses all of:" in text
    assert "- CMPS 147 COMPUTER SCIENCE I" in text
    assert "- MATH 205 DISCRETE MATH or MATH 237 PROOFS" in text
    assert "- CHEM 206 ORGANIC and CHEM 206L LAB" in text
    # A catalog id that is not a course is not shown as one; a lone "or" joins nothing.
    assert "an unnamed catalog entry" in text and "DztNY" not in text
    assert "Select 2 Courses at least 2 of:" in text and "Note: Must total 8 credits." in text
    assert "- CMPS 240 TOPICS" in text
    assert "Catalog Requirements\n  Note: Students may take up to three graduate courses." in text


def catalog() -> MemoryEntityFacts:
    program = {"id": "row-cs", "source_key": "academic-programs",
               "source_record_key": "catalog:SN-BS-CMPS", "name": "Computer Science BS",
               "degree": "Bachelor of Science", "program_kind": "major",
               "school": "School of Science, Nursing, and Health", "description": "Study code.",
               "program_url": "https://catalog.ramapo.edu/programs/SN-BS-CMPS",
               "collected_at": NOW, "freshness_sla_hours": 4320, "content_hash": "h"}

    def group(key: str, label: str, code: str) -> dict[str, Any]:
        return {"id": key, "source_key": "program-requirement-groups", "source_record_key": key,
                "requirement": {"label": label, "shape": "rule", "note": None,
                                "course_list": None,
                                "rule": {"name": "Required Courses", "choose": {"all": True},
                                         "items": [{"logic": "and", "courses": [
                                             {"code": code, "name": "COURSE"}]}]}},
                "collected_at": NOW, "freshness_sla_hours": 4320, "content_hash": key}
    return MemoryEntityFacts(
        dataset_version="release-1", identity_hash="identities-1",
        entities=[{"id": "cs", "name": "Computer Science BS", "kind": "program", "aliases": [],
                   "links": [{"collection": "programs", "source_key": "academic-programs",
                              "source_record_keys": ["catalog:SN-BS-CMPS"]},
                             {"collection": "requirement_groups",
                              "source_key": "program-requirement-groups",
                              "source_record_keys": ["g2", "g1", "g-gone"]}]}],
        contacts=[], records={"programs": [program], "requirement_groups": [
            group("g1", "Career Pathways", "PATH 001"),
            group("g2", "Major Requirements", "CMPS 147")]},
        now=lambda: NOW)


def test_a_programs_facts_are_its_catalog_row_and_its_groups_in_the_order_it_links_them() -> None:
    facts = catalog().get_program_facts("cs", "release-1", identity_hash="identities-1")
    props = {p["key"]: p for p in facts["properties"]}
    assert props["program"]["values"][0]["value"]["catalog_url"] == (
        "https://catalog.ramapo.edu/programs/SN-BS-CMPS")
    labels = [v["value"]["label"] for v in props["requirements"]["values"]]
    assert labels == ["Major Requirements", "Career Pathways"]
    # A linked group the release does not hold is said, never silently dropped.
    assert facts["caveats"] == ["1 linked requirement groups are missing."]
    assert not facts["complete"]
    assert facts["sources"][0]["freshness"] == "fresh"


def test_a_program_without_one_exact_catalog_row_is_not_read() -> None:
    service = catalog()
    service.records["programs"] = []
    with pytest.raises(EvidenceUnavailable):
        service.get_program_facts("cs", "release-1", identity_hash="identities-1")


def test_a_question_naming_a_program_is_answered_from_its_requirements() -> None:
    reply = {"parts": [{"asked": "cs requirements", "not_on_pages": False, "sections": [1]}],
             "text": "The Computer Science BS requires CMPS 147 COURSE. [1]"}
    gateway = ScriptedGateway(Completion("", (ToolCall("w", "write_answer", reply),), [
        {"type": "function_call", "call_id": "w", "name": "write_answer",
         "arguments": json.dumps(reply)}], Usage(10, 10, 0)))
    request = ChatRequest.model_validate({"messages": [
        {"role": "user", "content": "what do I need for the computer science major"}]})
    # The how-to switch is off: a named program is still read through its identity.
    engine = ChatEngine(gateway, catalog(), output="written")  # type: ignore[arg-type]
    result = asyncio.run(engine.answer(intake(request, now=NOW), request))
    given = json.loads(gateway.inputs[0][1]["content"])
    (program,) = given["programs"]
    assert program["program"] == "Computer Science BS" and program["n"] == 1
    assert "- CMPS 147 COURSE" in program["requirements"]
    assert result.trace is not None and result.trace["decidedBy"] == "one_call"
    assert "Sources: [1](https://catalog.ramapo.edu/programs/SN-BS-CMPS)" in result.body["answer"]


def test_a_course_is_named_only_by_an_exact_catalog_code() -> None:
    known = {"CMPS 147", "MATH 100T", "PATHCA 1", "PSYC 101"}
    assert course_codes("is cmps147 hard? what about CMPS-147 and math 100t", known) == [
        "CMPS 147", "MATH 100T"]
    assert course_codes("PATHCA 1 and psyc 101", known) == ["PATHCA 1", "PSYC 101"]
    # A room or a number in passing is not a course.
    assert course_codes("meet me in ASB 312 at room 147 in 2026", known) == []


def test_prerequisites_are_written_out_as_published_with_their_grade() -> None:
    text = render_requisites([{"section": "Prerequisite", "rule": {
        "name": "Prerequisite Course", "condition": "completedAllOf", "minimumGrade": "C",
        "note": "May be taken concurrently with CMPS 231.",
        "items": [{"logic": "and", "codes": [{"code": "CMPS 148",
                                              "name": "COMPUTER SCIENCE II"}]}]}}])
    assert text.splitlines() == [
        "Prerequisite", "  Prerequisite Course complete all of:", "    (minimum grade: C)",
        "    - CMPS 148 COMPUTER SCIENCE II", "    May be taken concurrently with CMPS 231."]
    # A rule of sub-rules (a course or a placement test) is written out whole, and a minimum
    # grade published as a constraint is kept.
    text = render_requisites([{"section": "Prerequisite", "rule": {
        "name": "Prerequisite Course or Placement Test", "condition": "anyOf", "subRules": [
            {"name": "Select 1 Course", "condition": "completedAnyOf", "items": [
                {"logic": "and", "codes": [{"code": "MATH 110", "name": "PRECALCULUS"}]}]},
            {"name": "Take 1 Test", "condition": "freeformText",
             "text": "Complete one Placement Test with the required minimum score:",
             "note": "- SAT MATH (minimum score 580)"}]}},
        {"section": "Prerequisite", "rule": {
            "name": "Prerequisite Course", "condition": "minimumGrade",
            "constraints": {"grade": "C-"},
            "items": [{"logic": "and", "codes": [{"code": "ACCT 321", "name": "INTERMEDIATE"}]}]}}])
    assert "    Select 1 Course complete one of:" in text and "      - MATH 110 PRECALCULUS" in text
    assert "      - SAT MATH (minimum score 580)" in text
    assert "    (minimum grade: C-)" in text


def course_catalog() -> MemoryEntityFacts:
    def course(code: str, name: str, credits: Any) -> dict[str, Any]:
        return {"id": f"course:{code}", "source_key": "academic-programs",
                "source_record_key": code, "url": f"https://catalog.ramapo.edu/courses/"
                f"{code.replace(' ', '')}", "collected_at": NOW, "freshness_sla_hours": 4320,
                "course": {"code": code, "name": name, "credits": credits, "attributes": [],
                           "requisites": [], "description": "A course.", "school": "SNH"}}
    entities = [{"id": f"id-{code}", "name": code, "kind": "course", "aliases": [],
                 "links": [{"collection": "courses", "source_key": "academic-programs",
                            "source_record_keys": [code]}]} for code in ("CMPS 147", "PATH 001")]
    return MemoryEntityFacts(dataset_version="release-1", identity_hash="identities-1",
                             entities=entities, contacts=[],
                             records={"courses": [course("CMPS 147", "COMPUTER SCIENCE I", 4),
                                                  course("PATH 001", "CAREER PATHWAYS",
                                                         {"min": 0})]},
                             now=lambda: NOW)


def test_courses_are_read_in_one_batch_with_unpublished_credits_unknown() -> None:
    facts = course_catalog().get_course_facts(["id-CMPS 147", "id-PATH 001"], "release-1",
                                              identity_hash="identities-1")
    values = [f["properties"][0]["values"][0]["value"] for f in facts]
    assert [v["code"] for v in values] == ["CMPS 147", "PATH 001"]
    assert values[0]["credits"] == 4 and values[1]["credits"] is None
    assert values[0]["catalog_url"] == "https://catalog.ramapo.edu/courses/CMPS147"


def test_a_question_naming_a_course_code_is_answered_from_its_catalog_record() -> None:
    reply = {"parts": [{"asked": "cmps 147", "not_on_pages": False, "sections": [1]}],
             "text": "CMPS 147 COMPUTER SCIENCE I is 4 credits. [1]"}
    gateway = ScriptedGateway(Completion("", (ToolCall("w", "write_answer", reply),), [
        {"type": "function_call", "call_id": "w", "name": "write_answer",
         "arguments": json.dumps(reply)}], Usage(10, 10, 0)))
    request = ChatRequest.model_validate({"messages": [
        {"role": "user", "content": "how many credits is cmps147"}]})
    engine = ChatEngine(gateway, course_catalog(), output="written")  # type: ignore[arg-type]
    result = asyncio.run(engine.answer(intake(request, now=NOW), request))
    (course,) = json.loads(gateway.inputs[0][1]["content"])["programs"]
    assert course["course"] == "CMPS 147 COMPUTER SCIENCE I" and course["credits"] == 4
    assert "Sources: [1](https://catalog.ramapo.edu/courses/CMPS147)" in result.body["answer"]


def plan(name: str, cohort: str) -> dict[str, Any]:
    return {"name": name, "cohort": cohort, "totalCredits": 128,
            "applicability": f"Applies to students admitted {cohort}.",
            "totals": ["Total Credits Required: 128 credits"],
            "terms": [{"year": "First Year", "term": "Fall Semester", "totalHours": 16,
                       "items": [{"text": "CMPS 147 - COMPUTER SCIENCE I", "hours": 4}]}],
            "generalEducation": [{"category": "Scientific Reasoning", "waivedForTransfers": True}],
            "placementText": "MATH 022 to MATH 108", "url": "https://www.ramapo.edu/plan/"}


def test_the_plan_is_the_named_or_current_cohorts_and_the_programs_own() -> None:
    plans = [plan("Data Science", "Fall 2026"), plan("Computer Science", "Fall 2025"),
             plan("Computer Science", "Fall 2026")]
    chosen = choose_plan(plans, "Computer Science BS", "what should i take", date(2026, 10, 21))
    assert chosen is not None and (chosen["name"], chosen["cohort"]) == (
        "Computer Science", "Fall 2026")
    # In July the academic year is still the one that began the August before.
    chosen = choose_plan(plans, "Computer Science BS", "plan", date(2026, 7, 1))
    assert chosen is not None and chosen["cohort"] == "Fall 2025"
    chosen = choose_plan(plans, "Computer Science BS", "i started in 2025", date(2026, 10, 21))
    assert chosen is not None and chosen["cohort"] == "Fall 2025"
    text = render_plan(plans[2])
    assert "First Year, Fall Semester (total 16 credits)" in text
    assert "  - CMPS 147 - COMPUTER SCIENCE I (4)" in text
    assert "Total Credits Required: 128 credits" in text
    assert "  - Scientific Reasoning (waived for transfer students)" in text
    assert choose_plan([], "Computer Science BS", "x", date(2026, 10, 21)) is None


def test_a_program_or_course_named_in_part_is_found_by_meaning_above_its_floor() -> None:
    from rockygpt_brain.one_call import routes_here
    from test_meaning import index_of
    index = index_of([("page-a", "0", ["advising"], [[1, 0, 0]])],
                     cards=[("advising", [1, 0, 0])],
                     academic=[("eng-ba", "program", [0, 1, 0]), ("eng-mn", "program", [0, 1, 0.1]),
                               ("litr-304", "course", [0, 0, 1])])
    # Program cards never score as offices, and courses not as programs.
    assert [e for e, _ in index.office_scores([0, 1, 0])] == ["advising"]
    scores = index.academic_scores([0, 1, 0], "program")
    assert [e for e, _ in scores] == ["eng-ba", "eng-mn"]
    names = {"eng-ba": "English and Literary Studies BA",
             "eng-mn": "English and Literary Studies Minor"}
    # Near-tied programs: the question's level words choose, else the major.
    assert [p["name"] for p in by_meaning(scores, names, "english minor", 0.42, 2)] == [
        "English and Literary Studies Minor"]
    assert [p["name"] for p in by_meaning(scores, names, "the english major", 0.42, 2)] == [
        "English and Literary Studies BA"]
    assert by_meaning([("eng-ba", 0.30)], names, "how do i get a parking permit", 0.42, 2) == []
    assert asks_academics("what classes are in the business degree")
    assert not asks_academics("how do i get a parking permit")
    # "Food" names a dining question unless the message asks about a program or course.
    assert routes_here("what is the food studies minor")
    assert not routes_here("where can i get food right now")


def test_a_short_subject_names_the_one_course_with_its_number() -> None:
    known = {"SOCI 301", "PSYC 101", "SOCI 101", "SOWK 301"}
    assert course_codes("whats the prereq for soc 301", known) == ["SOCI 301"]
    assert course_codes("what does psych 101 cover", known) == ["PSYC 101"]
    # "SO 301" is too short to be a subject, and two subjects with 301 would be a guess.
    assert course_codes("so 301", known) == []
    assert course_codes("sow 301 or soc 301", known) == ["SOWK 301", "SOCI 301"]


def test_a_class_year_or_a_plans_own_name_chooses_the_plan() -> None:
    plans = [plan("Visual Arts", "Fall 2026"), plan("Contemporary Arts", "Fall 2026"),
             plan("Visual Arts", "Fall 2024")]
    chosen = choose_plan(plans, "Visual Arts BA", "im a junior in visual arts", date(2026, 10, 21))
    assert chosen is not None and chosen["cohort"] == "Fall 2024"
    chosen = choose_plan(plans, "Visual Arts BA", "freshman in contemporary arts",
                         date(2026, 10, 21))
    assert chosen is not None and chosen["name"] == "Contemporary Arts"


def test_the_catalog_lists_programs_by_the_levels_asked() -> None:
    listing = catalog_listing(PROGRAMS, "is there a minor in law and society")
    assert listing == {"minors": ["Computer Science Minor", "Psychology Minor"]}
    assert set(catalog_listing(PROGRAMS, "what can i study")) == {
        "majors", "minors", "graduate programs", "certificates", "4+1 programs"}


def test_a_course_is_said_with_the_courses_whose_prerequisites_name_it() -> None:
    assert requisite_codes([{"section": "Prerequisite", "rule": {"subRules": [
        {"items": [{"codes": [{"code": "PSYC 101"}]}]}], "items": [
        {"codes": [{"code": "CRWT 102"}]}]}}]) == {"PSYC 101", "CRWT 102"}
    facts = {"properties": [{"values": [{"value": {
        "code": "PSYC 101", "name": "INTRO", "credits": 4, "requisites": [],
        "catalog_url": "https://catalog.ramapo.edu/courses/PSYC101"}}]}]}
    item = course_evidence(facts, 1, [f"PSYC {n}" for n in range(200, 225)])
    assert item["prerequisites"] == "none published"
    assert len(item["is_a_prerequisite_for"]) == 20 and item["more_courses_list_it"] == 5


def test_a_course_is_named_by_an_everyday_form_of_its_title() -> None:
    from rockygpt_brain.academics import courses_by_title
    titles = {"c1": "CALCULUS I", "c2": "CALCULUS II", "o1": "ORGANIC CHEMISTRY I LECTURE",
              "g2": "GENERAL CHEMISTRY II LECTURE", "ge": "GENETICS LAB",
              "fm": "FINANCIAL MANAGEMENT I", "fp": "FORENSIC PSYCHOLOGY",
              "ip": "INTRODUCTION TO PSYCHOLOGY"}
    assert courses_by_title(titles, "how many credits is calc 2 and what do i need") == ["c2"]
    assert courses_by_title(titles, "what are the prereqs for orgo 1") == ["o1"]
    assert courses_by_title(titles, "is gen chem 2 enough") == ["g2"]
    assert courses_by_title(titles, "prereqs for intro to psych") == ["ip"]
    # "gen ed" is not Genetics, the pronoun "I" is not Roman one, "for" is not Forensic.
    assert courses_by_title(titles, "do the bio labs count as natural science gen ed") == []
    assert courses_by_title(titles, "how do i apply for financial aid") == []


def test_programs_are_named_by_short_forms_each_with_its_own_level() -> None:
    from rockygpt_brain.academics import find_programs
    programs = [{"id": n, "name": n} for n in (
        "Biology BS", "Biology Minor", "Chemistry BS", "Chemistry Minor", "Mathematics BS",
        "Sociology BA", "Social Work BSW", "Physics BS", "Engineering Physics BS",
        "Data Science Minor", "Computer Science BS")]
    def names(q: str) -> list[str]:
        return [p["name"] for p in find_programs(programs, q)]
    assert names("what should a freshman math major take first") == ["Mathematics BS"]
    assert names("I'm a biology major thinking about a chem minor") == [
        "Biology BS", "Chemistry Minor"]
    assert names("can I double major in sociology and social work") == [
        "Sociology BA", "Social Work BSW"]
    assert names("how is engineering physics different from just physics") == [
        "Physics BS", "Engineering Physics BS"]
    assert names("is there a data science minor") == ["Data Science Minor"]


def test_code_works_out_published_programs_credits_and_gen_ed() -> None:
    from rockygpt_brain.academics import computed_answers
    programs = [{"id": n, "name": n} for n in (
        "Digital Filmmaking Minor", "Filmmaking BA", "Chemistry Minor", "Chemistry BS",
        "Nursing Generic BSN", "Social Work MSW")]
    facts = [f["fact"] for f in computed_answers([], programs, "is there a film minor?", 9)]
    assert facts == ["The catalog lists this minor for 'film': Digital Filmmaking Minor."]
    facts = [f["fact"] for f in computed_answers([], programs, "is there a nursing minor", 9)]
    assert facts == ["The catalog lists no minor for 'nursing'. It does list: "
                     "Nursing Generic BSN."]
    evidence = [
        {"n": 1, "course": "BIOL 111 FUNDAMENTALS OF BIOLOGY I LECTURE", "credits": 4,
         "attributes": [], "prerequisites": "none published"},
        {"n": 2, "course": "BIOL 111L FUNDAMENTALS OF BIOLOGY I LAB", "credits": 1,
         "attributes": []},
        {"n": 3, "course": "MATH 108 ELEMENTARY PROBABILITY AND STATISTICS", "credits": 4,
         "attributes": ["Gen Ed 18-Quantitative Rsng (GEQR)"]},
        {"n": 4, "program": "Social Work MSW",
         "requirements": "MSW Advanced Standing Track - 30 credit program\n  - MSWK 690\n"
                         "MSW Traditional Track - 60 credit program"}]
    facts = [f["fact"] for f in computed_answers(evidence, programs,
                                                 "does bio 1 with lab count for gen ed", None)]
    assert "BIOL 111 and BIOL 111L together are 5 credits (4 + 1)." in facts
    assert "BIOL 111 carries no Gen Ed attribute in the catalog." in facts
    assert "MATH 108 carries the Gen Ed attribute Gen Ed 18-Quantitative Rsng (GEQR)." in facts
    assert "The catalog gives MSW Traditional Track as 60 credits." in facts
    assert "The catalog gives MSW Advanced Standing Track as 30 credits." in facts
    assert "BIOL 111 has no published prerequisites." in facts


def test_a_gen_ed_question_about_a_subject_gets_its_courses_carrying_gen_ed() -> None:
    from rockygpt_brain.academics import gen_ed_courses
    titles = {"m1": "MATH WITH APPLICATIONS", "m8": "ELEMENTARY PROBABILITY AND STATISTICS",
              "sf": "SURVEY OF SCIENCE FICTION", "b1": "INTRODUCTION TO BIOLOGY"}
    gen_ed = {"m1": "GEQR", "m8": "GEQR", "sf": "GEVE", "b1": "GESR"}
    codes = {"m1": "MATH 101", "m8": "MATH 108", "sf": "LITR 263", "b1": "BIOL 101"}
    lines = gen_ed_courses(titles, gen_ed, codes, "does stats count for my gen ed math?")
    assert lines[0] == "MATH 108 ELEMENTARY PROBABILITY AND STATISTICS: GEQR"
    assert gen_ed_courses(titles, gen_ed, codes,
                          "do the bio labs count as natural science gen ed") == [
        "BIOL 101 INTRODUCTION TO BIOLOGY: GESR"]
    assert gen_ed_courses(titles, gen_ed, codes, "does stats count for my major") == []



def test_a_reply_stating_a_computed_sum_passes_the_evidence_check() -> None:
    from rockygpt_brain.writer import unsupported_by
    view = {"programs": [{"n": 1, "course": "BIOL 111", "credits": 4},
                         {"n": 2, "course": "BIOL 111L", "credits": 1}],
            "computed": [{"fact": "BIOL 111 and BIOL 111L together are 5 credits (4 + 1).",
                          "n": 1}],
            "sources": [{"n": 1, "urls": ["https://catalog.ramapo.edu/courses/BIOL111"]}]}
    assert unsupported_by("Together they are 15 credits. [1]", view) == [
        "a number the facts do not hold: 15"]
    assert unsupported_by("Together they are 5 credits. [1]", view) == []
