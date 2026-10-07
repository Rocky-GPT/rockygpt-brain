"""The Fact Packet contract: what the Brain hands on, and what it refuses to hand on."""

from copy import deepcopy
from datetime import UTC, timedelta
from typing import Any

import pytest

from rockygpt_brain.fact_packet import PACKET_VERSION, PacketInvalid, build_packet, validate_packet
from rockygpt_brain.retrieval import MemoryEntityFacts
from test_graph_store import IDENTITY, NOW, VERSION, contact, office, source

AS_OF = NOW.isoformat()


def part_for(service: MemoryEntityFacts, entity_id: str, name: str, fields: list[str],
             query: str | None = None) -> dict[str, Any]:
    reader = service.get_office_facts(entity_id, fields, VERSION, identity_hash=IDENTITY, as_of=NOW)
    return {"kind": "office_facts", "query": query or name, "fields": fields,
            "office": {"id": entity_id, "name": name}, "facts": reader}


def registrar(**changes: Any) -> MemoryEntityFacts:
    return source([office("registrar", "Registrar")], [contact("r1", **changes)])


def test_the_reader_s_properties_become_subject_predicate_value_facts() -> None:
    service = registrar()
    packet = build_packet(AS_OF, [part_for(service, "registrar", "Registrar", ["email", "phones"])])
    assert packet["version"] == PACKET_VERSION == "1.0" and packet["status"] == "complete"
    assert packet["request"]["intent"] == "contact" and packet["request"]["fields"] == [
        "email", "phones"]
    assert packet["request"]["entities"] == [
        {"id": "registrar", "name": "Registrar", "kind": "office", "query": "Registrar"}]
    assert packet["request"]["asOf"] == AS_OF
    assert packet["request"]["dataset"] == {"version": VERSION, "identityHash": IDENTITY}
    email = next(f for f in packet["facts"] if f["predicate"] == "email")
    assert email["subject"] == {"id": "registrar", "name": "Registrar", "kind": "office"}
    assert email["value"] == "published@example.edu"
    assert email["status"] == "known" and email["current"] is True
    assert set(email["source_ids"]) <= {s["id"] for s in packet["sources"]}
    assert packet["derived_facts"] == [] and packet["missing"] == []
    # The value is exactly what the reader returned, not a restatement.
    reader = part_for(service, "registrar", "Registrar", ["email"])["facts"]
    assert email["value"] == reader["properties"][0]["values"][0]["value"]


def test_a_source_carries_its_dates_freshness_and_secure_links() -> None:
    packet = build_packet(AS_OF, [part_for(registrar(), "registrar", "Registrar", ["email"])])
    (source_,) = packet["sources"]
    assert source_["title"] == "directory" and source_["collection"] == "contacts"
    assert source_["urls"] == ["https://example.edu/directory"]
    assert source_["freshness"] == "fresh" and source_["current"] is True
    assert source_["captured_at"] and source_["validity"] in {"current", "unspecified"}
    insecure = build_packet(AS_OF, [part_for(registrar(canonical_url="http://example.edu/x"),
                                             "registrar", "Registrar", ["email"])])
    assert insecure["sources"][0]["urls"] == []  # Listed, but with no link a writer could use.


def test_an_old_capture_is_a_fact_that_is_not_current_and_the_packet_is_partial() -> None:
    old = registrar(collected_at=NOW - timedelta(days=30))
    packet = build_packet(AS_OF, [part_for(old, "registrar", "Registrar", ["email"])])
    (fact,) = packet["facts"]
    assert fact["current"] is False and fact["value"] == "published@example.edu"
    assert packet["sources"][0]["freshness"] == "stale" and packet["sources"][0]["current"] is False
    assert packet["status"] == "partial"


def test_conflicting_values_are_listed_side_by_side_and_nothing_is_chosen() -> None:
    service = source([office("registrar", "Registrar")],
                     [contact("r1"), contact("r2", email="other@example.edu")])
    packet = build_packet(AS_OF, [part_for(service, "registrar", "Registrar", ["email"])])
    emails = [f for f in packet["facts"] if f["predicate"] == "email"]
    assert sorted(f["value"] for f in emails) == ["other@example.edu", "published@example.edu"]
    assert {f["status"] for f in emails} == {"conflicting"} and packet["status"] == "partial"


def test_a_requested_fact_the_evidence_does_not_hold_is_missing_not_blank() -> None:
    both = part_for(registrar(), "registrar", "Registrar", ["email", "hours"])
    packet = build_packet(AS_OF, [both])
    assert packet["request"]["intent"] == "contact_and_hours"
    assert [(m["predicate"], m["reason"]) for m in packet["missing"]] == [
        ("hours", "not_published")]
    assert packet["missing"][0]["subject"]["id"] == "registrar"
    assert not any(f["predicate"] == "hours" for f in packet["facts"])
    assert packet["status"] == "partial"
    only_hours = build_packet(AS_OF, [part_for(registrar(), "registrar", "Registrar", ["hours"])])
    assert only_hours["request"]["intent"] == "hours" and only_hours["status"] == "insufficient"


def test_two_offices_share_one_source_list_without_repeats() -> None:
    service = source([office("registrar", "Registrar"), office("bursar", "Bursar")],
                     [contact("r1"), contact("b1", "bursar")])
    one = part_for(service, "registrar", "Registrar", ["email"])
    two = part_for(service, "bursar", "Bursar", ["email"])
    packet = build_packet(AS_OF, [one, two, one])
    ids = [s["id"] for s in packet["sources"]]
    assert len(ids) == len(set(ids)) == 2
    assert {e["id"] for e in packet["request"]["entities"]} == {"registrar", "bursar"}


def test_the_packet_names_what_could_not_be_resolved() -> None:
    ambiguous = build_packet(AS_OF, [{"kind": "ambiguous", "query": "student", "truncated": False,
                                      "candidates": [{"id": "a", "name": "A", "match": "exact"}]}])
    assert ambiguous["status"] == "ambiguous" and ambiguous["request"]["intent"] == "office_lookup"
    assert ambiguous["ambiguities"][0]["candidates"][0]["id"] == "a" and ambiguous["facts"] == []
    missing = build_packet(AS_OF, [{"kind": "not_found", "query": "Cafeteria"}])
    assert missing["status"] == "not_found"
    assert missing["unresolved"] == [{"query": "Cafeteria", "reason": "no_matching_office"}]


def test_replies_with_no_facts_are_notices_and_say_whether_anything_was_answerable() -> None:
    assert build_packet(AS_OF, [{"kind": "greeting"}])["status"] == "no_facts_needed"
    clock = build_packet(AS_OF, [{"kind": "clock", "campusNow": AS_OF}])
    assert clock["request"]["intent"] == "clock"
    unsupported = build_packet(AS_OF, [{"kind": "unsupported", "afterLookup": False}])
    assert unsupported["status"] == "insufficient"
    assert unsupported["notices"] == [{"type": "unsupported", "afterLookup": False}]
    mixed = build_packet(AS_OF, [part_for(registrar(), "registrar", "Registrar", ["email"]),
                                 {"kind": "unsupported", "afterLookup": True}])
    assert mixed["status"] == "partial" and len(mixed["facts"]) == 1


def test_an_emergency_carries_the_kind_and_the_campus_numbers_as_marked_facts() -> None:
    service = source([office("ps", "Public Safety (Emergency)")], [contact("p1", "ps")])
    contacts = [part_for(service, "ps", "Public Safety (Emergency)", ["phones"])]
    packet = build_packet(AS_OF, [{"kind": "safety", "situation": "medical",
                                   "campusContacts": contacts, "approved_text": "Call 911."}])
    assert packet["status"] == "emergency" and packet["request"]["intent"] == "safety"
    assert packet["notices"] == [{"type": "safety", "situation": "medical", "contacts": ["ps"],
                                  "approved_text": "Call 911."}]
    (fact,) = packet["facts"]
    assert fact["predicate"] == "phones" and fact["purpose"] == "emergency_contact"
    assert packet["request"]["entities"] == []  # The student asked about no office.


def test_words_the_brain_approved_travel_in_the_packet_so_a_writer_never_composes_them() -> None:
    greeting = build_packet(AS_OF, [{"kind": "greeting", "approved_text": "Hi! I'm RockyGPT."}])
    assert greeting["notices"] == [{"type": "greeting", "approved_text": "Hi! I'm RockyGPT."}]
    # The standing reminder on a cut-short reply is not an emergency.
    reminder = build_packet(AS_OF, [
        part_for(registrar(), "registrar", "Registrar", ["email"]),
        {"kind": "incomplete", "code": "provider_unavailable", "approved_text": "Not finished."},
        {"kind": "emergency_reminder", "approved_text": "In danger? Call 911."}])
    assert reminder["status"] == "partial" and reminder["request"]["intent"] == "contact"
    assert [n["type"] for n in reminder["notices"]] == ["incomplete", "emergency_reminder"]


def test_a_safety_notice_without_its_approved_text_is_refused() -> None:
    with pytest.raises(PacketInvalid):
        build_packet(AS_OF, [{"kind": "safety", "situation": "fire", "campusContacts": [],
                              "approved_text": ""}])
    with pytest.raises(KeyError):  # An engine that forgot the key fails loudly, never quietly.
        build_packet(AS_OF, [{"kind": "safety", "situation": "fire", "campusContacts": []}])


@pytest.fixture
def valid() -> dict[str, Any]:
    return build_packet(AS_OF, [part_for(registrar(), "registrar", "Registrar", ["email"])])


def broken(packet: dict[str, Any], change: Any) -> dict[str, Any]:
    copy = deepcopy(packet)
    change(copy)
    return copy


@pytest.mark.parametrize("name,change", [
    ("wrong version", lambda p: p.update(version="2.0")),
    ("unknown status", lambda p: p.update(status="great")),
    ("no intent", lambda p: p["request"].update(intent="")),
    ("no time", lambda p: p["request"].pop("asOf")),
    ("facts not a list", lambda p: p.update(facts={})),
    ("a fact missing a key", lambda p: p["facts"][0].pop("current")),
    ("a fact without sources", lambda p: p["facts"][0].update(source_ids=[])),
    ("a fact with an unlisted source", lambda p: p["facts"][0].update(source_ids=["x"])),
    ("duplicate fact ids", lambda p: p["facts"].append(deepcopy(p["facts"][0]))),
    ("unknown fact status", lambda p: p["facts"][0].update(status="maybe")),
    ("current is not a boolean", lambda p: p["facts"][0].update(current="yes")),
    ("duplicate source ids", lambda p: p["sources"].append(deepcopy(p["sources"][0]))),
    ("a value that is not plain JSON", lambda p: p["facts"][0].update(value=float("nan"))),
    ("an incomplete missing entry", lambda p: p["missing"].append({"predicate": "x"})),
])
def test_a_packet_that_breaks_the_contract_is_refused(
        valid: dict[str, Any], name: str, change: Any) -> None:
    validate_packet(valid)
    with pytest.raises(PacketInvalid):
        validate_packet(broken(valid, change))


def test_the_clock_used_for_freshness_is_the_one_the_reader_was_given() -> None:
    later = NOW + timedelta(days=3)
    reader = registrar().get_office_facts("registrar", ["email"], VERSION, identity_hash=IDENTITY,
                                          as_of=later)
    part = {"kind": "office_facts", "query": "Registrar", "fields": ["email"],
            "office": {"id": "registrar", "name": "Registrar"}, "facts": reader}
    packet = build_packet(later.astimezone(UTC).isoformat(), [part])
    assert packet["facts"][0]["current"] is False  # 24 h limit, three days on.
