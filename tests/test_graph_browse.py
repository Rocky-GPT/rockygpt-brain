"""The dev UI's whole-graph reader lists whatever node and link types the release holds."""

from typing import Any

import pytest

from rockygpt_brain.retrieval import MemoryEntityFacts
from rockygpt_brain.retrieval.entity_facts import DatasetChanged, InvalidFactRequest
from rockygpt_brain.retrieval.graph_browse import browse

HALL = "11111111-1111-1111-1111-111111111111"
REGISTRAR = "22222222-2222-2222-2222-222222222222"
PERSON = "33333333-3333-3333-3333-333333333333"
SUBJECT = "44444444-4444-4444-4444-444444444444"
COURSE = "55555555-5555-5555-5555-555555555555"
# A kind and a link type no code names: the reader must show them all the same.
LOT = "66666666-6666-6666-6666-666666666666"
PAGE = "https://www.ramapo.edu/parking/"


def facts() -> MemoryEntityFacts:
    return MemoryEntityFacts(
        dataset_version="release-1", identity_hash="identities-1", contacts=[],
        entities=[
            {"id": HALL, "kind": "building", "name": "Hall", "aliases": ["H"], "links": []},
            {"id": REGISTRAR, "kind": "office", "name": "Registrar", "aliases": [], "links": [],
             "relationships": [{"type": "located_at", "target_entity_id": HALL,
                                "evidence": []}]},
            {"id": PERSON, "kind": "person", "name": "Pat", "aliases": [], "links": [],
             "relationships": [{"type": "works_in", "target_entity_id": REGISTRAR,
                                "evidence": [{"field": "dept", "collection": "contacts",
                                              "source_key": "d", "source_record_key": "p",
                                              "source_url": PAGE}]}]},
            {"id": SUBJECT, "kind": "subject", "name": "Math (MATH)", "aliases": [],
             "links": [], "relationships": [
                 {"type": "includes_course", "evidence": [],
                  "target_record": {"collection": "courses", "source_key": "c",
                                    "source_record_key": "MATH 101"}},
                 {"type": "includes_course", "evidence": [],
                  "target_record": {"collection": "courses", "source_key": "c",
                                    "source_record_key": "MATH 999"}}]},
            {"id": COURSE, "kind": "course", "name": "MATH 101", "aliases": [],
             "links": [{"collection": "courses", "source_key": "c",
                        "source_record_keys": ["MATH 101"]}]},
            {"id": LOT, "kind": "parking_lot", "name": "Lot A", "aliases": [], "links": [],
             "relationships": [{"type": "near", "target_entity_id": HALL, "evidence": []}]},
        ])


def test_the_root_lists_every_node_type_and_link_type_in_the_data() -> None:
    root = browse(facts(), "ramapo")
    assert {k["label"]: k["count"] for k in root["kinds"]} == {
        "building": 1, "office": 1, "person": 1, "subject": 1, "course": 1, "parking_lot": 1}
    assert {(t["from"], t["type"], t["to"]): t["count"] for t in root["link_types"]} == {
        ("office", "located_at", "building"): 1, ("person", "works_in", "office"): 1,
        ("subject", "includes_course", "course"): 1, ("parking_lot", "near", "building"): 1}
    # A link to a record no node owns is counted, not dropped.
    assert root["unlinked_targets"] == {"includes_course": 1}
    assert root["entity_count"] == 6


def test_a_type_lists_its_nodes_and_a_node_shows_links_both_ways() -> None:
    graph = facts()
    assert [c["label"] for c in browse(graph, "kind:parking_lot")["children"]] == ["Lot A"]
    hall = browse(graph, f"entity:{HALL}")
    assert [p["id"] for p in hall["path"]] == ["ramapo", "kind:building", f"entity:{HALL}"]
    assert hall["node"]["aliases"] == ["H"]
    assert {(link["type"], link["node"]["label"]) for link in hall["incoming"]} == {
        ("located_at", "Registrar"), ("near", "Lot A")}
    person = browse(graph, f"entity:{PERSON}")
    assert person["outgoing"] == [{"type": "works_in", "node": {
        "id": f"entity:{REGISTRAR}", "label": "Registrar", "kind": "office",
        "entity_id": REGISTRAR}, "missing_id": None, "sources": [PAGE]}]
    assert browse(graph, f"entity:{REGISTRAR}")["evidence_node"] == f"office:{REGISTRAR}"
    assert person["evidence_node"] is None
    subject = browse(graph, f"entity:{SUBJECT}")
    assert [link["node"]["label"] if link["node"] else link["record"]["source_record_key"]
            for link in subject["outgoing"]] == ["MATH 101", "MATH 999"]
    assert [link["node"]["label"] for link in browse(graph, f"entity:{COURSE}")["incoming"]] \
        == ["Math (MATH)"]


def test_unknown_nodes_and_old_pins_are_refused() -> None:
    graph = facts()
    for node in ("kind:nothing", "entity:nope", "offices"):
        with pytest.raises(InvalidFactRequest):
            browse(graph, node)
    with pytest.raises(DatasetChanged):
        browse(graph, "ramapo", dataset_version="old", identity_hash="identities-1")


def test_a_node_card_shows_the_facts_in_every_record_it_links() -> None:
    page = "https://www.ramapo.edu/faculty/pat/"
    graph = MemoryEntityFacts(
        dataset_version="release-1", identity_hash="identities-1",
        contacts=[{"id": "c1", "source_key": "faculty", "source_record_key": "pat",
                   "name": "Pat", "email": "pat@ramapo.edu", "office": "A-207", "phone": None,
                   "content_hash": "x", "source_url": page}],
        records={"courses": [{"source_key": "c", "source_record_key": "MATH 101",
                              "title": "Calculus I", "credits": 4}]},
        entities=[
            {"id": PERSON, "kind": "person", "name": "Pat", "aliases": [], "links": [
                {"collection": "contacts", "source_key": "faculty", "source_record_keys": ["pat"]},
                {"collection": "schools", "source_key": "m", "source_record_keys": ["1"]}]},
            {"id": COURSE, "kind": "course", "name": "MATH 101", "aliases": [],
             "links": [{"collection": "courses", "source_key": "c",
                        "source_record_keys": ["MATH 101"]}]},
        ])
    # The person's directory row, minus bookkeeping and empty fields. A collection with no
    # reader (schools) is left off rather than shown as empty.
    assert browse(graph, f"entity:{PERSON}")["facts"] == [{"collection": "contacts", "total": 1,
        "rows": [{"name": "Pat", "email": "pat@ramapo.edu", "office": "A-207",
                  "source_url": page}]}]
    assert browse(graph, f"entity:{COURSE}")["facts"] == [{"collection": "courses", "total": 1,
        "rows": [{"title": "Calculus I", "credits": 4}]}]


VENUE = "77777777-7777-7777-7777-777777777777"
EVENT = "88888888-8888-8888-8888-888888888888"


def card_graph() -> MemoryEntityFacts:
    def menu(day: str, name: str) -> dict[str, object]:
        return {"source_key": "dining", "source_record_key": f"{day}:Lunch:Grill:{name}",
                "meal": "Lunch", "station": "Grill", "name": name, "valid_from": day,
                "valid_until": day}
    return MemoryEntityFacts(
        dataset_version="release-1", identity_hash="identities-1", contacts=[],
        records={
            "faculty": [{"source_key": "faculty", "source_record_key": "pat",
                         "profile": {"title": "Professor of Math",
                                     "imageUrl": "https://www.ramapo.edu/pat.jpg"}}],
            "courses": [{"source_key": "c", "source_record_key": "MATH 101",
                         "course": {"code": "MATH 101", "name": "CALCULUS I", "credits": 4}}],
            "events": [{"source_key": "e", "source_record_key": "e1", "date_label":
                        "Thu, Nov 5, 2026", "start_time": "7 PM", "end_time": "9 PM",
                        "listing": {"location": "C-220"}}],
            "menu": [menu("2026-11-01", "Soup"), menu("2026-11-02", "Burger"),
                     menu("2026-11-02", "Fries"), menu("2026-11-04", "Tacos")]},
        entities=[
            {"id": PERSON, "kind": "person", "name": "Pat", "aliases": [], "links": [
                {"collection": "faculty", "source_key": "faculty", "source_record_keys": ["pat"]}],
             "relationships": [{"type": "teaches_course", "target_entity_id": COURSE,
                                "evidence": []}]},
            {"id": COURSE, "kind": "course", "name": "MATH 101", "aliases": [],
             "links": [{"collection": "courses", "source_key": "c",
                        "source_record_keys": ["MATH 101"]}]},
            {"id": EVENT, "kind": "event", "name": "Game Night", "aliases": [],
             "links": [{"collection": "events", "source_key": "e", "source_record_keys": ["e1"]}],
             "relationships": [{"type": "organized_by", "target_entity_id": PERSON,
                                "evidence": []}]},
            {"id": VENUE, "kind": "venue", "name": "Birch", "aliases": [], "links": [
                {"collection": "menu", "source_key": "dining", "source_record_keys": [
                    "2026-11-01:Lunch:Grill:Soup", "2026-11-02:Lunch:Grill:Burger",
                    "2026-11-02:Lunch:Grill:Fries", "2026-11-04:Lunch:Grill:Tacos"]}]},
        ])


def test_each_linked_node_carries_a_brief_from_its_own_record() -> None:
    from datetime import UTC, datetime
    pat = browse(card_graph(), f"entity:{PERSON}", now=datetime(2026, 11, 2, 17, tzinfo=UTC))
    assert pat["outgoing"][0]["node"]["brief"] == {"title": "CALCULUS I", "credits": 4}
    assert pat["incoming"][0]["node"]["brief"] == {
        "date": "Thu, Nov 5, 2026", "start": "7 PM", "end": "9 PM", "where": "C-220"}
    course = browse(card_graph(), f"entity:{COURSE}")
    assert course["incoming"][0]["node"]["brief"] == {
        "title": "Professor of Math", "image_url": "https://www.ramapo.edu/pat.jpg"}


def test_a_node_can_be_opened_by_its_kind_and_exact_name() -> None:
    assert browse(card_graph(), "name:course:math 101")["node"]["entity_id"] == COURSE
    with pytest.raises(InvalidFactRequest):
        browse(card_graph(), "name:course:MATH 999")


def test_a_venue_card_shows_one_whole_menu_day_and_the_days_it_has() -> None:
    from datetime import UTC, date, datetime
    monday = datetime(2026, 11, 2, 17, tzinfo=UTC)

    def menu(on: date | None = None) -> dict[str, Any]:
        found = browse(card_graph(), f"entity:{VENUE}", now=monday, on=on)["facts"]
        return next(f for f in found if f["collection"] == "menu")
    today = menu()
    assert today["dates"] == ["2026-11-01", "2026-11-02", "2026-11-04"]
    assert today["date"] == "2026-11-02" and today["total"] == 4
    assert [row["name"] for row in today["rows"]] == ["Burger", "Fries"]
    assert [row["name"] for row in menu(on=date(2026, 11, 4))["rows"]] == ["Tacos"]
    # A day with no menu opens today's instead.
    assert menu(on=date(2026, 11, 3))["date"] == "2026-11-02"
