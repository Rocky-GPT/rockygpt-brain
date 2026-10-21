"""Majors, minors and certificates: which program a question names, and its requirements written
out by code from the catalog's own rules (academics.py)."""

import asyncio
import json
from typing import Any

import pytest

from rockygpt_brain.academics import (
    also_published,
    find_programs,
    program_level,
    render_requirements,
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
