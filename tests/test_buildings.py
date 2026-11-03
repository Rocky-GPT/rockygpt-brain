"""A campus building asked where it is or what is open in it (11-03 practice: "where da student
center at" found nothing, as no office is called that). Code answers from the building's campus
map record and the graph's located_at links, and its places' own hours."""

import asyncio
from typing import Any

from rockygpt_brain.contract import ChatRequest
from rockygpt_brain.engine import ChatEngine
from rockygpt_brain.retrieval.entity_facts import MemoryEntityFacts
from rockygpt_brain.turn import intake
from test_engine import NOW, ScriptedGateway, completion, finish

MAP = "https://map.example.edu/?id=1#!m/1218428"
DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def in_the_student_center(key: str) -> list[dict[str, Any]]:
    # Placed by its own published room, which starts with the building's room prefix.
    return [{"type": "located_at", "target_entity_id": "b-sc", "evidence": [
        {"collection": "contacts", "source_key": "directory", "field": "office",
         "source_record_key": key}]}]


def student_center() -> MemoryEntityFacts:
    entities = [
        {"id": "b-sc", "kind": "building", "name": "Student Center (SC)",
         "aliases": ["SC", "Student Center"],
         "links": [{"collection": "buildings", "source_key": "campus-map",
                    "source_record_keys": ["1218428"]}]},
        {"id": "registrar", "kind": "office", "name": "Registrar", "aliases": [],
         "links": [{"collection": "contacts", "source_key": "directory",
                    "source_record_keys": ["registrar"]},
                   {"collection": "campus_hours", "source_key": "campus-hours",
                    "source_record_keys": [f"Registrar:{day}" for day in DAYS]}],
         "relationships": in_the_student_center("registrar")},
        {"id": "lounge", "kind": "office", "name": "Commuter Lounge", "aliases": [],
         "links": [{"collection": "contacts", "source_key": "directory",
                    "source_record_keys": ["lounge"]}],
         "relationships": in_the_student_center("lounge")}]
    contacts = [{"id": f"c-{key}", "source_key": "directory", "source_record_key": key,
                 "name": name, "office": room, "canonical_url": f"https://example.edu/{key}",
                 "collected_at": NOW, "freshness_sla_hours": 24}
                for key, name, room in (("registrar", "Registrar", "SC-120"),
                                        ("lounge", "Commuter Lounge", "SC-110"))]
    schedules = [{
        "id": f"hours-{day}", "source_key": "campus-hours", "source_record_key": f"Registrar:{day}",
        "name": "Registrar", "day": day,
        "schedule": "8:30am-4:30pm" if day not in ("Saturday", "Sunday") else "Hours unavailable",
        "source_url": "https://example.edu/registrar", "collected_at": NOW,
        "valid_from": "2026-08-26", "valid_until": "2026-12-16", "content_hash": f"h-{day}",
        "canonical_url": "https://example.edu/campus-hours", "freshness_sla_hours": 4_320,
    } for day in DAYS]
    records = {"buildings": [{
        "id": "buildings:1218428", "source_key": "campus-map", "source_record_key": "1218428",
        "name": "Student Center (SC)", "source_url": MAP, "room_prefixes": ["SC"],
        "category": "Academic Buildings", "collected_at": NOW, "content_hash": "map"}]}
    return MemoryEntityFacts(dataset_version="release-1", identity_hash="identities-1",
                             entities=entities, contacts=contacts, schedules=schedules,
                             records=records, now=lambda: NOW)


def ask(question: str) -> Any:
    request = ChatRequest.model_validate({"messages": [{"role": "user", "content": question}]})
    gateway = ScriptedGateway()  # Any model call would fail: none is scripted.
    engine = ChatEngine(gateway, student_center(), output="written")
    result = asyncio.run(engine.answer(intake(request, now=NOW), request))
    assert gateway.inputs == []
    return result


def lookups(result: Any) -> list[tuple[str, str | None, str]]:
    return [(item["arguments"]["query"], item.get("foundBy") or item.get("resolvedBy"),
             item["status"]) for item in result.trace["lookups"]]


def test_where_a_building_is_is_its_map_its_room_numbers_and_the_places_in_it() -> None:
    result = ask("Where is the Student Center?")
    assert result.trace["decidedBy"] == "code"
    assert lookups(result) == [("Student Center (SC)", "building", "ok")]
    assert result.body["answer"] == (
        f"Student Center (SC) is on the campus map: {MAP}. [1] Room numbers in Student Center "
        "(SC) start with SC. [1] Places in Student Center (SC) include Commuter Lounge and "
        f"Registrar. [2][3]\n\nSources: [1]({MAP}) [2](https://example.edu/lounge) "
        "[3](https://example.edu/registrar)")


def test_a_buildings_hours_are_the_hours_its_places_publish() -> None:
    result = ask("Is the Student Center open on Friday?")
    # The building, then each place in it that publishes hours (the lounge publishes none).
    assert lookups(result) == [("Student Center (SC)", "building", "ok"),
                               ("Registrar", "building_place", "ok")]
    answer = result.body["answer"]
    assert answer.startswith("I don't have hours for Student Center (SC) as a whole, but places "
                             "in it list their own.")
    assert "Registrar on Friday, October 2: 8:30am-4:30pm" in answer
    assert "campus map" not in answer and "Commuter Lounge" not in answer
    # Only the sources the reply cites are listed: not the map, the lounge or the directory.
    assert answer.endswith("[4]\n\nSources: [4](https://example.edu/registrar)")


def test_a_lookup_the_model_names_a_building_by_finds_the_building() -> None:
    lookup = completion("graph_lookup",
                        {"requests": [{"query": "student center", "fields": ["offices"]}]})
    request = ChatRequest.model_validate(
        {"messages": [{"role": "user", "content": "where da student center at"}]})
    engine = ChatEngine(ScriptedGateway(lookup, finish()), student_center())
    result = asyncio.run(engine.answer(intake(request, now=NOW), request))
    assert lookups(result) == [("student center", "building", "ok")]
    assert MAP in result.body["answer"] and "Registrar" in result.body["answer"]


def pine_hall() -> MemoryEntityFacts:
    """A residence hall: its office (the hall's own listing, on its first floor) is named like
    the building the campus map shows."""
    entities = [
        {"id": "b-ph", "kind": "building", "name": "Pine Hall", "aliases": [],
         "links": [{"collection": "buildings", "source_key": "campus-map",
                    "source_record_keys": ["1133459"]}]},
        {"id": "ph", "kind": "office", "name": "Pine Hall", "aliases": [],
         "links": [{"collection": "contacts", "source_key": "directory",
                    "source_record_keys": ["pine"]}],
         "relationships": [{"type": "located_at", "target_entity_id": "b-ph", "evidence": [
             {"collection": "buildings", "source_key": "campus-map", "field": "name",
              "source_record_key": "1133459", "source_url": "https://map.example.edu/ph"}]}]}]
    contacts = [{"id": "c-pine", "source_key": "directory", "source_record_key": "pine",
                 "name": "Pine Hall", "office": "First floor", "collected_at": NOW,
                 "canonical_url": "https://example.edu/reslife/pine-hall",
                 "freshness_sla_hours": 24}]
    records = {"buildings": [{
        "id": "buildings:1133459", "source_key": "campus-map", "source_record_key": "1133459",
        "name": "Pine Hall", "source_url": "https://map.example.edu/ph", "room_prefixes": [],
        "category": "Residence Halls", "collected_at": NOW, "content_hash": "map"}]}
    return MemoryEntityFacts(dataset_version="release-1", identity_hash="identities-1",
                             entities=entities, contacts=contacts, records=records,
                             now=lambda: NOW)


def test_where_a_hall_named_like_its_building_is_gives_the_map_and_its_offices_floor() -> None:
    # 11-03: "Where is Pine Hall?" said only "Pine Hall is in First floor."
    request = ChatRequest.model_validate(
        {"messages": [{"role": "user", "content": "Where is Pine Hall?"}]})
    gateway = ScriptedGateway()
    engine = ChatEngine(gateway, pine_hall(), output="written")
    result = asyncio.run(engine.answer(intake(request, now=NOW), request))
    assert gateway.inputs == []
    assert lookups(result) == [("Pine Hall", None, "ok"),
                               ("Pine Hall", "same_name_building", "ok")]
    # The building is said first; sources keep the order they were read in.
    assert result.body["answer"] == (
        "Pine Hall is on the campus map: https://map.example.edu/ph. [2] Pine Hall's office is on "
        "the first floor. [1]\n\nSources: [1](https://example.edu/reslife/pine-hall) "
        "[2](https://map.example.edu/ph)")
