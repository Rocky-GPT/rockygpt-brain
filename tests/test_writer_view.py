"""The writer view: what a template or a model with no other context is handed.

It must hold every value a student could be told and none of the Brain's own bookkeeping, and it
must stay that way when the packet grows a field.
"""

import json
from collections.abc import Iterator
from copy import deepcopy
from datetime import timedelta
from typing import Any

from rockygpt_brain.fact_packet import build_packet
from rockygpt_brain.retrieval import MemoryEntityFacts
from rockygpt_brain.writer_view import NO_VALIDITY_NOTE, writer_view
from test_fact_packet import AS_OF, part_for
from test_graph_store import NOW, contact, office, source, week
from test_not_published import claim
from test_not_published import registrar as absent_registrar


def registrar_with(*, hours: bool = False, **changes: Any) -> MemoryEntityFacts:
    schedules = week("Regular", "w1", "8am-5pm") if hours else None
    return source([office("registrar", "Registrar", schedules=["Regular"] if hours else [])],
                  [contact("r1", **changes)], schedules=schedules)


def packet_of(*parts: dict[str, Any]) -> dict[str, Any]:
    return build_packet(AS_OF, list(parts))


def contact_packet() -> dict[str, Any]:
    return packet_of(part_for(registrar_with(), "registrar", "Registrar", ["email", "phones"]))


def every_shape() -> dict[str, dict[str, Any]]:
    """One packet for each shape a turn can end in."""
    both = source([office("registrar", "Registrar"), office("bursar", "Bursar")],
                  [contact("r1"), contact("b1", "bursar")])
    conflicting = source([office("registrar", "Registrar")],
                         [contact("r1"), contact("r2", email="other@example.edu")])
    emergency = part_for(registrar_with(), "registrar", "Registrar", ["phones"])
    return {
        "contact": contact_packet(),
        "hours with a validity window and a note": packet_of(
            part_for(registrar_with(hours=True), "registrar", "Registrar", ["hours"])),
        "a day asked": packet_of({**part_for(
            registrar_with(hours=True), "registrar", "Registrar", ["hours"]),
            "when": {"day": "saturday", "date": "2026-10-10"}}),
        "a day asked, outside the schedule's window": packet_of({**part_for(
            registrar_with(hours=True), "registrar", "Registrar", ["hours"]),
            "when": {"day": "tomorrow", "date": "2027-01-05"}}),
        "a stale capture": packet_of(part_for(
            registrar_with(collected_at=NOW - timedelta(days=30)), "registrar", "Registrar",
            ["email"])),
        "conflicting values": packet_of(part_for(conflicting, "registrar", "Registrar", ["email"])),
        "an unknown field": packet_of(part_for(
            registrar_with(), "registrar", "Registrar", ["email", "hours"])),
        "a confirmed absence": packet_of(part_for(
            absent_registrar(claim("email")), "registrar", "Registrar", ["email"])),
        "two offices": packet_of(part_for(both, "registrar", "Registrar", ["email"]),
                                 part_for(both, "bursar", "Bursar", ["email"])),
        "choices, truncated": packet_of({"kind": "ambiguous", "query": "the center",
                                         "truncated": True, "candidates": [
                                             {"id": "a", "name": "A Center", "match": "partial"},
                                             {"id": "b", "name": "B Center", "match": "partial"}]}),
        "no office": packet_of({"kind": "not_found", "query": "Cafeteria"}),
        "fixed wording": packet_of(
            {"kind": "unsupported", "afterLookup": False, "approved_text": "Not supported."},
            {"kind": "recall", "messageIndex": 0, "speaker": "user", "text": "hi",
             "shortened": False, "earlierMessagesOmitted": False},
            {"kind": "clock", "campusNow": AS_OF},
            {"kind": "greeting", "approved_text": "Hello."}),
        "an emergency": packet_of({"kind": "safety", "situation": "medical",
                                   "campusContacts": [emergency], "approved_text": "Call 911."}),
    }


# --- what a writer is given -----------------------------------------------------------------

def test_a_contact_packet_becomes_the_values_to_say_and_the_pages_to_cite() -> None:
    assert writer_view(contact_packet()) == {
        "asked": {"intent": "contact", "offices": ["Registrar"], "fields": ["email", "phones"],
                  "asOf": AS_OF},
        "status": "complete",
        "facts": [
            {"subject": "Registrar", "predicate": "email", "value": "published@example.edu",
             "sources": [1]},
            {"subject": "Registrar", "predicate": "phones", "value": [{"number": "+12015550100"}],
             "sources": [1]},
        ],
        "sources": [{"n": 1, "title": "directory", "urls": ["https://example.edu/directory"],
                     "captured_at": contact_packet()["sources"][0]["captured_at"]}],
    }


def test_the_unusual_is_said_and_the_usual_is_left_out() -> None:
    stale = writer_view(every_shape()["a stale capture"])
    (fact,) = stale["facts"]
    assert fact["current"] is False and "status" not in fact
    assert stale["sources"][0]["freshness"] == "stale"
    assert stale["status"] == "partial"
    conflicting = writer_view(every_shape()["conflicting values"])
    assert {f["status"] for f in conflicting["facts"]} == {"conflicting"}
    assert sorted(f["value"] for f in conflicting["facts"]) == [
        "other@example.edu", "published@example.edu"]
    plain = writer_view(contact_packet())
    assert all("status" not in f and "current" not in f for f in plain["facts"])
    assert "freshness" not in plain["sources"][0] and "limitations" not in plain["sources"][0]


def test_a_validity_window_and_a_published_note_survive() -> None:
    view = writer_view(every_shape()["hours with a validity window and a note"])
    (source_,) = [s for s in view["sources"] if "valid_from" in s]
    assert source_["valid_from"] == "2026-08-26" and source_["valid_until"] == "2026-12-16"
    hours = next(f for f in view["facts"] if f["predicate"] == "hours")
    assert "Regular" in json.dumps(hours["value"]) and "8am-5pm" in json.dumps(hours["value"])
    assert "Regular note" in json.dumps(hours["value"])


def test_unknown_and_confirmed_not_published_stay_two_different_things() -> None:
    unknown = writer_view(every_shape()["an unknown field"])
    assert unknown["missing"] == [{"subject": "Registrar", "predicate": "hours"}]
    assert "not_published" not in unknown
    absence = writer_view(every_shape()["a confirmed absence"])
    (entry,) = absence["not_published"]
    assert entry["subject"] == "Registrar" and entry["predicate"] == "email"
    assert entry["checked_at"] and entry["pages"] == ["https://example.edu/registrar/"]
    assert "section" not in json.dumps(entry) and "current" not in entry
    assert "missing" not in absence and "facts" not in absence


def test_what_to_ask_and_what_was_not_found_carry_the_students_words() -> None:
    choices = writer_view(every_shape()["choices, truncated"])
    assert choices["ambiguities"] == [{"query": "the center", "choices": ["A Center", "B Center"],
                                       "truncated": True}]
    assert choices["status"] == "ambiguous" and "facts" not in choices
    none = writer_view(every_shape()["no office"])
    assert none["unresolved"] == [{"query": "Cafeteria"}] and none["status"] == "not_found"


def test_fixed_wording_and_the_emergency_text_are_carried_verbatim() -> None:
    wording = writer_view(every_shape()["fixed wording"])
    notices = {n["type"]: n for n in wording["notices"]}
    assert notices["unsupported"]["approved_text"] == "Not supported."
    assert notices["greeting"]["approved_text"] == "Hello."
    assert notices["recall"]["text"] == "hi" and notices["recall"]["speaker"] == "user"
    assert notices["clock"]["campusNow"] == AS_OF
    emergency = writer_view(every_shape()["an emergency"])
    (safety,) = emergency["notices"]
    assert safety["approved_text"] == "Call 911." and safety["situation"] == "medical"
    assert "contacts" not in safety
    # The campus contact is still a fact, marked for what it is.
    assert emergency["facts"][0]["purpose"] == "emergency_contact"
    assert emergency["status"] == "emergency"


def test_empty_lists_are_left_out_and_the_view_is_much_smaller() -> None:
    view = writer_view(contact_packet())
    assert set(view) == {"asked", "status", "facts", "sources"}
    full = len(json.dumps(contact_packet(), separators=(",", ":")))
    assert len(json.dumps(view, separators=(",", ":"))) < full * 0.6


# --- what a writer is never given -----------------------------------------------------------

def strings(node: Any) -> Iterator[str]:
    if isinstance(node, dict):
        for key, value in node.items():
            yield str(key)
            yield from strings(value)
    elif isinstance(node, list):
        for item in node:
            yield from strings(item)
    elif isinstance(node, str):
        yield node


def test_no_id_reaches_the_writer() -> None:
    for name, packet in every_shape().items():
        ids = {packet_id for source_ in packet["sources"] for packet_id in [source_["id"]]}
        ids |= {fact["id"] for fact in packet["facts"]}
        ids |= {entity["id"] for entity in packet["request"]["entities"]}
        ids |= {c["id"] for a in packet["ambiguities"] for c in a["candidates"]}
        ids |= {fact["subject"]["id"] for fact in packet["facts"]}
        text = json.dumps(writer_view(packet))
        assert all(f'"{packet_id}"' not in text for packet_id in ids), name
        assert "identityHash" not in text and "release-1" not in text, name
        assert "source_ids" not in text and '"kind"' not in text, name


# --- nothing is lost, and a new field is never silently lost --------------------------------

# Dropped on purpose (see the module): ids and the Brain's own bookkeeping.
DROPPED = {"id", "source_ids", "kind", "collection", "match", "original_record_id", "contacts",
           "identityHash", "version", "dataset", "from"}
# Dropped on purpose when they hold the usual value.
USUAL = {("status", "known"), ("freshness", "fresh"), ("validity", "unspecified"),
         ("truncated", False), ("reason", "unknown"), ("reason", "no_matching_office"),
         ("limitations", NO_VALIDITY_NOTE)}
# A check's own time and section are the proof; the entry keeps the oldest time and the page.
PROOF = {("not_published", "checks", "checked_at"), ("not_published", "checks", "section")}


def leaves(node: Any, path: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], Any]]:
    if isinstance(node, dict):
        for key, value in node.items():
            yield from leaves(value, (*path, key))
    elif isinstance(node, list):
        for item in node:
            yield from leaves(item, path)
    else:
        yield path, node


def without_uncited(packet: dict[str, Any]) -> dict[str, Any]:
    """The packet minus the sources nothing cites: those are dropped on purpose."""
    cited = {sid for kind in ("facts", "not_published", "derived_facts") for entry in packet[kind]
             for sid in entry["source_ids"]}
    replaced = {fid for d in packet["derived_facts"] for fid in d["from"]}
    return {**packet, "facts": [f for f in packet["facts"] if f["id"] not in replaced],
            "sources": [s for s in packet["sources"] if s["id"] in cited]}


def test_every_value_in_the_packet_is_in_the_view_or_dropped_on_purpose() -> None:
    for name, full in every_shape().items():
        packet = without_uncited(full)
        said = {json.dumps(value) for _, value in leaves(writer_view(full))}
        for path, value in leaves(packet):
            key = path[-1]
            if value is None or value == "" or isinstance(value, bool):
                continue
            if key in DROPPED or (key, value) in USUAL or path[:1] + path[-2:] in PROOF:
                continue
            if path[0] == "sources" and key == "current":
                continue
            assert json.dumps(value) in said, (name, path, value)


def test_a_current_flag_that_is_false_is_never_lost() -> None:
    for name, packet in every_shape().items():
        replaced = {fid for d in packet["derived_facts"] for fid in d["from"]}
        stale = sum(1 for kind in ("facts", "not_published", "derived_facts")
                    for x in packet[kind]
                    if x["current"] is False and x.get("id") not in replaced
                    # A date outside the schedule's window is said by `applies`, not by `current`.
                    and (kind != "derived_facts" or x["applies"] or "applicability" in x))
        assert json.dumps(writer_view(packet)).count('"current": false') == stale, name


def test_a_field_the_packet_gains_later_is_carried_not_lost() -> None:
    packet = contact_packet()
    packet["facts"][0]["as_read_by"] = "reader-2"
    packet["sources"][0]["publisher"] = "Registrar"
    packet["notices"].append({"type": "later", "detail": "x", "subject": {
        "id": "registrar", "name": "Registrar", "kind": "office"}})
    view = writer_view(packet)
    assert view["facts"][0]["as_read_by"] == "reader-2"
    assert view["sources"][0]["publisher"] == "Registrar"
    assert view["notices"] == [{"type": "later", "subject": "Registrar", "detail": "x"}]


def test_the_view_is_plain_json_and_leaves_the_packet_as_it_was() -> None:
    for name, packet in every_shape().items():
        before = deepcopy(packet)
        view = writer_view(packet)
        assert packet == before, name
        assert json.loads(json.dumps(view, allow_nan=False)) == view, name


def test_only_the_sources_somebody_cites_reach_the_writer_and_they_are_numbered_from_one() -> None:
    # The hours question reads the contact row too (it is the same office) and a stale old row; the
    # writer is told only the schedule, so only the schedule's source is listed.
    packet = every_shape()["hours with a validity window and a note"]
    cited = {sid for fact in packet["facts"] for sid in fact["source_ids"]}
    assert len(packet["sources"]) > len(cited)
    view = writer_view(packet)
    assert [s["n"] for s in view["sources"]] == list(range(1, len(cited) + 1))
    assert all(set(f["sources"]) <= {s["n"] for s in view["sources"]} for f in view["facts"])
    # Nothing is cited, so no source is listed.
    assert "sources" not in writer_view(every_shape()["no office"])


def test_the_emergency_wording_is_the_first_thing_a_writer_reads() -> None:
    view = writer_view(every_shape()["an emergency"])
    assert list(view)[:3] == ["asked", "status", "notices"]
    # Other fixed wording keeps its usual place after the facts.
    wording = writer_view(every_shape()["fixed wording"])
    assert list(wording).index("notices") > list(wording).index("status")


def test_a_field_the_packet_gains_never_overwrites_a_key_the_view_sets() -> None:
    packet = contact_packet()
    packet["facts"][0]["sources"] = "mine"
    packet["facts"][0]["subject"] = {"id": "registrar", "name": "Registrar", "kind": "office"}
    packet["sources"][0]["n"] = 99
    view = writer_view(packet)
    assert view["facts"][0]["sources"] == [1] and view["sources"][0]["n"] == 1


def test_a_day_that_was_asked_hands_the_writer_that_day_and_not_the_week() -> None:
    packet = every_shape()["a day asked"]
    assert len(next(f for f in packet["facts"] if f["predicate"] == "hours")["value"]["days"]) == 7
    view = writer_view(packet)
    (day,) = view["derived_facts"]
    assert (day["predicate"], day["day"], day["date"]) == ("hours_on", "Saturday", "2026-10-10")
    # What the schedule publishes for that day, as published; the note travels with it.
    assert day["value"] == {"schedule": "Regular", "hours": None, "notes": ["Regular note"]}
    # The schedule publishes nothing for Saturday and no page proves it: not a current answer.
    assert "applies" not in day and day["current"] is False
    assert "facts" not in view  # The full week is the record, not what a writer is told.
    assert "Monday" not in json.dumps(view) and "8am-5pm" not in json.dumps(view)
    assert [s["n"] for s in view["sources"]] == day["sources"]
    # The full packet keeps the week and says where the day came from.
    assert packet["derived_facts"][0]["from"] == [
        next(f["id"] for f in packet["facts"] if f["predicate"] == "hours")]


def test_a_date_the_schedule_does_not_cover_gets_the_window_and_no_hours() -> None:
    view = writer_view(every_shape()["a day asked, outside the schedule's window"])
    (day,) = view["derived_facts"]
    assert day["applies"] is False and "current" not in day
    assert day["value"]["hours"] is None
    assert day["value"]["window"] == {"from": "2026-08-26", "until": "2026-12-16"}
    assert view["status"] == "partial"


def test_the_other_fields_of_the_same_office_are_still_facts_beside_the_day() -> None:
    both = packet_of({**part_for(registrar_with(hours=True), "registrar", "Registrar",
                                 ["email", "hours"]),
                      "when": {"day": "friday", "date": "2026-10-09"}})
    view = writer_view(both)
    assert [f["predicate"] for f in view["facts"]] == ["email"]
    assert view["derived_facts"][0]["value"]["hours"] == "8am-5pm"


def proof(section: str, *, current: bool = True, source_id: str = "s") -> dict[str, Any]:
    check = {"url": "https://example.edu/hours/", "section": section,
             "checked_at": "2026-10-07T18:44:05.521Z", "html_sha256": "ab" * 32}
    return {"status": "not_published", "checks": [check, dict(check)],
            "checked_at": check["checked_at"], "current": current, "source_ids": [source_id],
            "scope": "Regular source timetable",
            "reason": "The reviewed section does not publish the listed weekday opening hours."}


def test_a_proof_that_something_is_not_published_reaches_the_writer_as_when_and_where() -> None:
    packet = deepcopy(every_shape()["hours with a validity window and a note"])
    (fact,) = [f for f in packet["facts"] if f["predicate"] == "hours"]
    sid = fact["source_ids"][0]
    fact["value"]["season"] = "Fall"
    fact["value"]["validity_absence"] = {"valid_from": proof("Fall", source_id=sid),
                                         "valid_until": proof("Fall", current=False, source_id=sid)}
    saturday = next(d for d in fact["value"]["days"] if d["day"] == "Saturday")
    saturday.update(hours=None, status="not_published", absence=proof("Fall", source_id=sid))
    packet["sources"][0].update(season="Fall",
                                validity_absence=deepcopy(fact["value"]["validity_absence"]))
    view = writer_view(packet)
    text = json.dumps(view)
    # Only when it was checked and which page was read: no hash, section, scope or reason sentence.
    for audit in ("html_sha256", "ab" * 8, "Regular source timetable", "does not publish", sid,
                  "validity_absence", "section"):
        assert audit not in text, audit
    (said,) = [f for f in view["facts"] if f["predicate"] == "hours"]
    assert said["value"]["season"] == "Fall"
    assert said["value"]["dates_not_published"] == {
        "checked_at": "2026-10-07T18:44:05.521Z", "pages": ["https://example.edu/hours/"],
        "which": ["start", "end"], "current": False}
    day = next(d for d in said["value"]["days"] if d["day"] == "Saturday")
    assert day == {"day": "Saturday", "hours": None, "status": "not_published", "absence": {
        "checked_at": "2026-10-07T18:44:05.521Z", "pages": ["https://example.edu/hours/"]}}
    # A source repeats neither the season nor the proof: the fact says both.
    assert "season" not in view["sources"][0] and "validity_absence" not in view["sources"][0]
    # The packet, the Brain's record, still has all of it.
    assert fact["value"]["validity_absence"]["valid_from"]["checks"][0]["html_sha256"]


def test_an_unverified_seasonal_date_says_so_once_and_has_no_empty_window() -> None:
    packet = deepcopy(every_shape()["a day asked"])
    (derived,) = packet["derived_facts"]
    derived.update(applies=False, current=False, applicability="unverified",
                   applicability_reason="Its applicability to this date is unverified.")
    derived["value"]["window"] = {}
    (said,) = writer_view(packet)["derived_facts"]
    assert said["applicability"] == "unverified" and said["current"] is False
    assert "applies" not in said and "window" not in said["value"]
    assert said["applicability_reason"] == "Its applicability to this date is unverified."
