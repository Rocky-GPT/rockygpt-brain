"""A field the office's own pages were confirmed not to publish is a recorded answer, not a gap.

The data pipeline writes the confirmation into the contact row (which sections were read, and
when). The reader turns it into the status `not_published` only when no value exists; anything
malformed confirms nothing and the field stays `unknown`.
"""

import asyncio
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
                                "checked_at": CHECK["checked_at"]}
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
    assert "Email: not published on Ramapo's pages (checked 2026-10-05)" in rendered.text
    assert "[directory](" in rendered.text
    assert rendered.supported and rendered.complete
    assert any(c["id"] == "r1:email:not_published" and c["urls"] == [CHECK["url"]]
               for c in rendered.citations)
    unknown = render_facts(read(registrar(), "email"))
    assert "Email: not published in the available evidence." in unknown.text
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
    service = registrar(claim("email"), collected_at=NOW)  # Fresh at the engine tests' clock.
    engine = ChatEngine(ScriptedGateway(LOOKUP, finish()), service, output="json")
    result = asyncio.run(engine.answer(intake(request, now=NOW), request))
    packet = result.body["facts"]
    assert result.status_code == 200 and packet["status"] == "complete"
    assert [e["predicate"] for e in packet["not_published"]] == ["email"]
    assert packet["facts"] == [] and packet["missing"] == []
    text = ChatEngine(ScriptedGateway(LOOKUP, finish()), service, output="text")
    answer = asyncio.run(text.answer(intake(request, now=NOW), request)).body
    assert "not published on Ramapo's pages (checked 2026-10-05)" in answer["answer"]
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
