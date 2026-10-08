"""A field the office's own pages were confirmed not to publish is a recorded answer, not a gap.

The data pipeline writes the confirmation into the contact row (which sections were read, and
when). The reader turns it into the status `not_published` only when no value exists; anything
malformed confirms nothing and the field stays `unknown`.
"""

import asyncio
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest

from rockygpt_brain.answers import render_facts
from rockygpt_brain.contract import ChatRequest
from rockygpt_brain.engine import ChatEngine
from rockygpt_brain.fact_packet import PacketInvalid, build_packet
from rockygpt_brain.retrieval import MemoryEntityFacts
from rockygpt_brain.turn import intake
from test_engine import LOOKUP, NOW, ScriptedGateway, finish
from test_graph_store import IDENTITY, VERSION, contact, office, source, week
from test_graph_store import NOW as GRAPH_NOW

CHECK = {"url": "https://example.edu/registrar/", "section": "Contact Us",
         "checked_at": "2026-10-05T08:00:00+00:00"}


def claim(field: str, *checks: dict[str, Any]) -> dict[str, Any]:
    return {"field": field, "checks": list(checks) if checks else [CHECK]}


def meta(*claims: Any) -> dict[str, Any]:
    return {"evidence": {"source_urls": [CHECK["url"]], "not_published": list(claims)}}


def registrar(*claims: Any, schedules: Any = None, **changes: Any) -> MemoryEntityFacts:
    changes = {"freshness_sla_hours": 168, **changes}
    row = contact("r1", email=None, normalization_metadata=meta(*claims), **changes)
    return source([office("registrar", "Registrar", schedules=["Regular"] if schedules else [])],
                  [row], schedules=schedules)


def read(service: MemoryEntityFacts, *fields: str) -> dict[str, Any]:
    return service.get_office_facts("registrar", list(fields), VERSION, identity_hash=IDENTITY,
                                    as_of=GRAPH_NOW)


def prop(result: dict[str, Any], key: str) -> dict[str, Any]:
    return next(p for p in result["properties"] if p["key"] == key)


def test_a_field_with_no_value_and_a_confirmed_absence_is_not_published_with_its_proof() -> None:
    email = prop(read(registrar(claim("email")), "email"), "email")
    # The empty observation stays visible; nothing in it is a value.
    assert email["status"] == "not_published" and email["values"] == []
    assert all(a["value"] is None for a in email["assertions"])
    assert email["absence"] == {"source_ids": ["r1"], "checks": [CHECK],
                                "checked_at": CHECK["checked_at"], "current": True}
    # Nothing was claimed about the phone, so the neighbouring field is unaffected.
    other = read(registrar(claim("email")), "email", "phones")
    assert prop(other, "phones")["status"] == "known" and "absence" not in prop(other, "phones")
    # The row that carries the proof is one of the sources the result lists.
    assert {s["id"] for s in other["sources"]} >= {"r1"}


def test_an_unclaimed_empty_field_stays_unknown_and_a_value_always_wins() -> None:
    assert prop(read(registrar(), "email"), "email")["status"] == "unknown"
    assert "absence" not in prop(read(registrar(), "email"), "email")
    with_value = registrar(claim("email"))
    with_value = source([office("registrar", "Registrar")],
                        [contact("r1", normalization_metadata=meta(claim("email")))])
    email = prop(read(with_value, "email"), "email")
    assert email["status"] == "known" and "absence" not in email


@pytest.mark.parametrize("bad", [
    "not a list", [{"field": "email"}], [{"field": "email", "checks": []}],
    [{"field": "email", "checks": [{"url": "https://example.edu/", "section": "x"}]}],
    [{"field": "email", "checks": [{**CHECK, "checked_at": "yesterday"}]}],
    [{"field": "email", "checks": [CHECK, {**CHECK, "section": ""}]}],
    [{"field": "email", "checks": [{**CHECK, "url": 7}]}],
    [{"field": "mail", "checks": [CHECK]}], ["email"], [None],
])
def test_a_malformed_confirmation_confirms_nothing(bad: Any) -> None:
    row = contact("r1", email=None, normalization_metadata={"evidence": {"not_published": bad}})
    service = source([office("registrar", "Registrar")], [row])
    assert prop(read(service, "email"), "email")["status"] == "unknown"


def test_each_field_reads_its_own_claim_and_only_contact_details_and_hours_can_be_absent() -> None:
    service = registrar(claim("phone"), claim("office"), claim("hours"), claim("name"),
                        phone=None, office=None)
    result = read(service, "phones", "offices", "hours", "name", "email")
    assert {p["key"]: p["status"] for p in result["properties"]} == {
        "phones": "not_published", "offices": "not_published", "hours": "not_published",
        "name": "known", "email": "unknown"}
    assert prop(result, "name")["values"]


def test_hours_with_a_schedule_stay_known_and_without_one_can_be_confirmed_absent() -> None:
    schedules = week("Regular", "a", "8am-5pm")
    assert prop(read(registrar(claim("hours"), schedules=schedules), "hours"), "hours")[
        "status"] == "known"
    assert prop(read(registrar(claim("hours")), "hours"), "hours")["status"] == "not_published"
    assert prop(read(registrar(), "hours"), "hours")["status"] == "unknown"


def test_the_earliest_check_dates_the_confirmation_and_every_section_is_kept() -> None:
    older = {**CHECK, "section": "Location", "checked_at": "2026-10-01T08:00:00+00:00"}
    email = prop(read(registrar(claim("email", CHECK, older)), "email"), "email")
    assert email["absence"]["checked_at"] == older["checked_at"]
    assert email["absence"]["checks"] == [CHECK, older]


def test_the_text_answer_says_the_pages_were_read_and_a_missing_one_is_still_unknown() -> None:
    rendered = render_facts(read(registrar(claim("email")), "email", "phones"))
    assert ("Email: not published on the reviewed official pages (checked 2026-10-05)"
            in rendered.text)
    assert "[directory](" in rendered.text
    assert rendered.supported and rendered.complete
    assert any(c["id"] == "r1:email:not_published" and c["urls"] == [CHECK["url"]]
               for c in rendered.citations)
    unknown = render_facts(read(registrar(), "email"))
    assert "Email: I have no published information about this." in unknown.text
    assert not unknown.complete


def test_a_stale_confirmation_is_marked_dated_and_incomplete() -> None:
    service = registrar(claim("email"))
    late = service.get_office_facts("registrar", ["email"], VERSION, identity_hash=IDENTITY,
                                    as_of=GRAPH_NOW + timedelta(days=30))
    rendered = render_facts(late)
    assert "dated observation; current status unverified" in rendered.text
    assert not rendered.complete and not rendered.supported


def test_the_packet_lists_a_confirmed_absence_apart_from_what_is_unknown() -> None:
    part = {"kind": "office_facts", "query": "Registrar", "fields": ["email", "hours"],
            "office": {"id": "registrar", "name": "Registrar"},
            "facts": read(registrar(claim("email")), "email", "hours")}
    packet = build_packet(GRAPH_NOW.isoformat(), [part])
    (absent,) = packet["not_published"]
    assert absent["predicate"] == "email" and absent["checks"] == [CHECK]
    assert absent["checked_at"] == CHECK["checked_at"] and absent["current"] is True
    assert absent["source_ids"] == ["r1"] and absent["subject"]["id"] == "registrar"
    assert [(m["predicate"], m["reason"]) for m in packet["missing"]] == [("hours", "unknown")]
    assert packet["facts"] == [] and packet["status"] == "partial"  # Hours are still unknown.
    only = {**part, "fields": ["email"], "facts": read(registrar(claim("email")), "email")}
    done = build_packet(GRAPH_NOW.isoformat(), [only])
    assert done["status"] == "complete" and done["missing"] == []
    assert len(done["not_published"]) == 1


def test_a_confirmation_that_is_no_longer_current_makes_the_packet_partial() -> None:
    late = registrar(claim("email")).get_office_facts(
        "registrar", ["email"], VERSION, identity_hash=IDENTITY,
        as_of=GRAPH_NOW + timedelta(days=30))
    part = {"kind": "office_facts", "query": "Registrar", "fields": ["email"],
            "office": {"id": "registrar", "name": "Registrar"}, "facts": late}
    packet = build_packet((GRAPH_NOW + timedelta(days=30)).isoformat(), [part])
    assert packet["not_published"][0]["current"] is False and packet["status"] == "partial"


def test_a_packet_whose_absence_names_no_listed_source_is_refused() -> None:
    facts = read(registrar(claim("email")), "email")
    prop(facts, "email")["absence"]["source_ids"] = ["nowhere"]
    part = {"kind": "office_facts", "query": "Registrar", "fields": ["email"],
            "office": {"id": "registrar", "name": "Registrar"}, "facts": facts}
    with pytest.raises(PacketInvalid):
        build_packet(GRAPH_NOW.isoformat(), [part])


def test_a_chat_turn_answers_with_the_recorded_absence() -> None:
    request = ChatRequest.model_validate(
        {"messages": [{"role": "user", "content": "What is the Registrar email?"}]})
    before_the_turn = {**CHECK, "checked_at": "2026-09-30T08:00:00+00:00"}
    # Fresh at the engine tests' clock.
    service = registrar(claim("email", before_the_turn), collected_at=NOW)
    engine = ChatEngine(ScriptedGateway(LOOKUP, finish()), service, output="json")
    result = asyncio.run(engine.answer(intake(request, now=NOW), request))
    packet = result.body["facts"]
    assert result.status_code == 200 and packet["status"] == "complete"
    assert [e["predicate"] for e in packet["not_published"]] == ["email"]
    assert packet["facts"] == [] and packet["missing"] == []
    text = ChatEngine(ScriptedGateway(LOOKUP, finish()), service, output="text")
    answer = asyncio.run(text.answer(intake(request, now=NOW), request)).body
    assert "not published on the reviewed official pages (checked 2026-09-30)" in answer["answer"]
    assert answer["status"] == "answered"  # An absence is an answer.


def absent_packet() -> dict[str, Any]:
    part = {"kind": "office_facts", "query": "Registrar", "fields": ["email"],
            "office": {"id": "registrar", "name": "Registrar"},
            "facts": read(registrar(claim("email")), "email")}
    return build_packet(GRAPH_NOW.isoformat(), [part])


@pytest.mark.parametrize("name,change", [
    ("no pages checked", lambda e: e.update(checks=[])),
    ("pages not a list", lambda e: e.update(checks="x")),
    ("no sources", lambda e: e.update(source_ids=[])),
    ("a source the packet does not list", lambda e: e.update(source_ids=["nowhere"])),
    ("current not a boolean", lambda e: e.update(current="yes")),
    ("no date", lambda e: e.pop("checked_at")),
    ("no subject", lambda e: e.pop("subject")),
])
def test_a_not_published_entry_that_breaks_the_contract_is_refused(name: str, change: Any) -> None:
    from copy import deepcopy

    from rockygpt_brain.fact_packet import validate_packet
    packet = absent_packet()
    validate_packet(packet)
    broken = deepcopy(packet)
    change(broken["not_published"][0])
    with pytest.raises(PacketInvalid):
        validate_packet(broken)


def test_a_claim_with_no_field_or_for_a_field_that_cannot_be_absent_confirms_nothing() -> None:
    row = contact("r1", normalization_metadata=meta({"checks": [CHECK]}, claim("website"),
                                                     {"field": None, "checks": [CHECK]}))
    service = source([office("registrar", "Registrar")], [row])
    assert prop(read(service, "website"), "website")["status"] == "unknown"


def test_the_text_answer_refuses_an_absence_whose_sources_are_not_all_listed() -> None:
    from rockygpt_brain.retrieval import EvidenceUnavailable
    facts = read(registrar(claim("email")), "email")
    prop(facts, "email")["absence"]["source_ids"] = ["r1", "ghost"]
    with pytest.raises(EvidenceUnavailable):
        render_facts(facts)


@pytest.mark.parametrize("name,bad", [
    ("an insecure page", {**CHECK, "url": "http://example.edu/registrar/"}),
    ("a script link", {**CHECK, "url": "javascript:alert(1)"}),
    ("a blank section", {**CHECK, "section": "   "}),
    ("a control character", {**CHECK, "section": "Contact\u0007 Us"}),
    ("a section that is far too long", {**CHECK, "section": "x" * 301}),
    ("a check dated after the clock", {**CHECK, "checked_at": "2026-10-07T08:00:00+00:00"}),
])
def test_a_check_that_is_malformed_insecure_or_from_the_future_confirms_nothing(
        name: str, bad: dict[str, Any]) -> None:
    assert prop(read(registrar(claim("email", bad)), "email"), "email")["status"] == "unknown", name
    # One bad check spoils the whole claim, even beside a good one.
    spoiled = read(registrar(claim("email", CHECK, bad)), "email")
    assert prop(spoiled, "email")["status"] == "unknown"


def test_a_claim_with_more_than_sixteen_checks_confirms_nothing() -> None:
    many = [{**CHECK, "section": f"Part {i}"} for i in range(17)]
    assert prop(read(registrar(claim("email", *many)), "email"), "email")["status"] == "unknown"
    sixteen = read(registrar(claim("email", *many[:16])), "email")
    assert prop(sixteen, "email")["status"] == "not_published"


def test_the_publishers_own_doubt_about_a_field_cancels_its_confirmation() -> None:
    issue = {"kind": "contradicted", "reason": "x"}
    doubt = {"evidence": {"not_published": [claim("email")],
                          "absence_issues": [{"field": "email", **issue}]}}
    row = contact("r1", email=None, freshness_sla_hours=168, normalization_metadata=doubt)
    assert prop(read(source([office("registrar", "Registrar")], [row]), "email"), "email")[
        "status"] == "unknown"
    other = {"evidence": {"not_published": [claim("email")],
                          "absence_issues": [{"field": "hours", **issue}]}}
    row = contact("r1", email=None, freshness_sla_hours=168, normalization_metadata=other)
    assert prop(read(source([office("registrar", "Registrar")], [row]), "email"), "email")[
        "status"] == "not_published"
    dropped = {"field": "phone", "value": "(201) 555-0100", "reason": "x"}
    withheld = {"evidence": {"not_published": [claim("phone")], "withheld": [dropped]}}
    row = contact("r1", phone=None, phones=None, freshness_sla_hours=168,
                  normalization_metadata=withheld)
    assert prop(read(source([office("registrar", "Registrar")], [row]), "phones"), "phones")[
        "status"] == "unknown"


def test_a_row_that_holds_any_raw_value_for_the_field_is_never_called_not_published() -> None:
    held = (("phones", "phones", "(201) 555-0100"), ("office", "offices", "D-224"),
            ("offices", "offices", ["D-224"]))
    for column, key, value in held:
        field = "phone" if key == "phones" else "office"
        columns: dict[str, Any] = {
            "email": None, "phone": None, "phones": None, "office": None, column: value}
        row = contact("r1", freshness_sla_hours=168, normalization_metadata=meta(claim(field)),
                      **columns)
        service = source([office("registrar", "Registrar")], [row])
        assert prop(read(service, key), key)["status"] != "not_published", column


def test_a_linked_schedule_that_could_not_be_read_is_a_gap_not_an_absence() -> None:
    row = contact("r1", freshness_sla_hours=168, normalization_metadata=meta(claim("hours")))
    linked = source([office("registrar", "Registrar", schedules=["Regular"])], [row])
    assert prop(read(linked, "hours"), "hours")["status"] == "unknown"
    unlinked = source([office("registrar", "Registrar")], [row])
    assert prop(read(unlinked, "hours"), "hours")["status"] == "not_published"


def test_a_check_older_than_the_rows_allowance_is_not_current_and_oldest_is_by_instant() -> None:
    stale = {**CHECK, "checked_at": "2026-09-20T08:00:00+00:00"}  # 16 days before the clock.
    result = read(registrar(claim("email", stale)), "email")
    assert prop(result, "email")["absence"]["current"] is False
    rendered = render_facts(result)
    assert "dated observation; current status unverified" in rendered.text
    assert not rendered.supported
    packet = build_packet(GRAPH_NOW.isoformat(), [{"kind": "office_facts", "query": "Registrar",
        "fields": ["email"], "office": {"id": "registrar", "name": "Registrar"}, "facts": result}])
    assert packet["not_published"][0]["current"] is False and packet["status"] == "partial"
    # 03:00-05:00 is 08:00Z; 06:00Z is the true oldest, though its text sorts after.
    mixed = [{**CHECK, "section": "A", "checked_at": "2026-10-05T03:00:00-05:00"},
             {**CHECK, "section": "B", "checked_at": "2026-10-05T06:00:00+00:00"}]
    oldest = prop(read(registrar(claim("email", *mixed)), "email"), "email")["absence"]
    assert oldest["checked_at"] == "2026-10-05T06:00:00+00:00"


def test_confirmations_from_two_rows_are_gathered_and_a_stale_source_is_not_current() -> None:
    rows = [contact("r1", email=None, freshness_sla_hours=168,
                    normalization_metadata=meta(claim("email", {**CHECK, "section": "One"}))),
            contact("r2", email=None, freshness_sla_hours=168,
                    normalization_metadata=meta(claim("email", {**CHECK, "section": "Two"})))]
    result = read(source([office("registrar", "Registrar")], rows), "email")
    absence = prop(result, "email")["absence"]
    assert absence["source_ids"] == ["r1", "r2"]
    assert [c["section"] for c in absence["checks"]] == ["One", "Two"]
    assert absence["current"] is True
    two = meta(claim("email", {**CHECK, "section": "Two"}))
    old = GRAPH_NOW - timedelta(days=3)
    stale = [rows[0], contact("r2", email=None, freshness_sla_hours=1, collected_at=old,
                              normalization_metadata=two)]
    assert prop(read(source([office("registrar", "Registrar")], stale), "email"), "email")[
        "absence"]["current"] is False


def test_the_packet_validator_refuses_a_confirmed_absence_that_does_not_hold_up() -> None:
    from copy import deepcopy

    from rockygpt_brain.fact_packet import validate_packet
    packet = absent_packet()
    entry = packet["not_published"][0]
    changes: dict[str, Callable[[dict[str, Any]], Any]] = {
        "a check that is not an object": lambda e: e.update(checks=[None]),
        "an empty check": lambda e: e.update(checks=[{}]),
        "an insecure url": lambda e: e["checks"][0].update(url="http://example.edu/"),
        "a check date that is not a date": lambda e: e["checks"][0].update(checked_at="tomorrow"),
        "an entry date that is not a date": lambda e: e.update(checked_at="soon"),
        "an empty entry date": lambda e: e.update(checked_at=""),
        "a field that cannot be absent": lambda e: e.update(predicate="name"),
        "a predicate that is not text": lambda e: e.update(predicate=5),
        "no subject": lambda e: e.update(subject=None),
        "a subject without a name": lambda e: e["subject"].update(name=""),
        "source ids that are not text": lambda e: e.update(source_ids=[[1]]),
    }
    for name, change in changes.items():
        broken = deepcopy(packet)
        change(broken["not_published"][0])
        with pytest.raises(PacketInvalid):
            validate_packet(broken)
        assert entry == packet["not_published"][0], name
    # An answered field cannot also be confirmed not published (or unknown).
    both = deepcopy(packet)
    both["facts"] = [{"id": "f1", "subject": dict(entry["subject"]),
                      "predicate": entry["predicate"], "value": "x@example.edu",
                      "status": "known", "current": True, "source_ids": entry["source_ids"]}]
    with pytest.raises(PacketInvalid):
        validate_packet(both)
    unknown_too = deepcopy(packet)
    unknown_too["missing"] = [{"subject": dict(entry["subject"]), "predicate": entry["predicate"],
                               "reason": "unknown"}]
    with pytest.raises(PacketInvalid):
        validate_packet(unknown_too)


def test_a_confirmed_absence_for_an_emergency_number_never_changes_the_question_asked() -> None:
    from rockygpt_brain.fact_packet import build_packet as build
    numbers = read(registrar(claim("phone"), phone=None, phones=None), "phones")
    contact_part = {"kind": "office_facts", "query": "Public Safety", "fields": ["phones"],
                    "office": {"id": "registrar", "name": "Registrar"}, "facts": numbers}
    part = {"kind": "safety", "situation": "medical", "approved_text": "Call 911.",
            "campusContacts": [contact_part]}
    packet = build(GRAPH_NOW.isoformat(), [part])
    assert packet["status"] == "emergency" and packet["request"]["entities"] == []
    assert [entry.get("purpose") for entry in packet["not_published"]] == ["emergency_contact"]
