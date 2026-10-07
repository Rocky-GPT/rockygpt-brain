"""BRAIN_OUTPUT=json: a turn answers with a validated Fact Packet and no written answer.

The AI still picks the lookup and the finish parts; the code resolves, reads, checks dates and
returns what it found. Nothing downstream (a template or a model) may add a fact. The status,
citations and release pins are the same as in text mode, which stays the default.
"""

import asyncio
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

import rockygpt_brain.api.app as app_module
import rockygpt_brain.engine as engine_module
from rockygpt_brain.contract import ChatRequest
from rockygpt_brain.engine import ChatEngine, ChatResult
from rockygpt_brain.fact_packet import PacketInvalid, validate_packet
from rockygpt_brain.provider import Completion, GatewayError
from rockygpt_brain.retrieval import MemoryEntityFacts, PostgresEntityFacts
from rockygpt_brain.turn import intake
from test_engine import LOOKUP, NOW, ScriptedGateway, completion, facts, finish, two_student_offices
from test_graph_store import contact, office, source

QUESTION = "What is the Registrar email?"


def run(gateway: ScriptedGateway, service: MemoryEntityFacts | None = None, *,
        output: str = "json", messages: list[dict[str, str]] | None = None) -> ChatResult:
    request = ChatRequest.model_validate(
        {"messages": messages or [{"role": "user", "content": QUESTION}]})
    engine = ChatEngine(gateway, service or facts(), output=output)  # type: ignore[arg-type]
    return asyncio.run(engine.answer(intake(request, now=NOW), request))


def packet(result: ChatResult) -> dict[str, Any]:
    found: dict[str, Any] = result.body["facts"]
    validate_packet(found)  # Whatever the turn did, the packet it sent meets the contract.
    return found


def notices(result: ChatResult) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = packet(result)["notices"]
    return found


def lookup_of(query: str) -> Completion:
    return completion("graph_lookup", {"requests": [{"query": query, "fields": ["email"]}]})


def test_a_lookup_returns_the_readers_facts_in_a_packet_and_no_written_answer() -> None:
    service = facts()
    result = run(ScriptedGateway(LOOKUP, finish()), service)
    assert result.status_code == 200 and result.body["answer"] == ""
    found = packet(result)
    assert found["version"] == "1.0" and found["status"] == "complete"
    assert datetime.fromisoformat(found["request"]["asOf"]) == NOW
    assert found["request"]["intent"] == "contact" and found["request"]["fields"] == ["email"]
    assert found["request"]["entities"] == [
        {"id": "registrar", "name": "Registrar", "kind": "office", "query": "Registrar"}]
    (fact,) = found["facts"]
    assert fact["subject"] == {"id": "registrar", "name": "Registrar", "kind": "office"}
    assert fact["predicate"] == "email" and fact["value"] == "published@example.edu"
    assert fact["status"] == "known" and fact["current"] is True
    # Exactly what the shared reader returned: nothing is merged, chosen or reworded.
    reader = service.get_office_facts(
        "registrar", ["email"], "release-1", identity_hash="identities-1", as_of=NOW)
    assert fact["value"] == reader["properties"][0]["values"][0]["value"]
    assert found["request"]["dataset"] == {"version": "release-1", "identityHash": "identities-1"}
    assert {s["id"] for s in found["sources"]} == set(fact["source_ids"])


def test_status_citations_and_release_match_text_mode_and_text_stays_the_default() -> None:
    as_text = run(ScriptedGateway(LOOKUP, finish()), output="text")
    as_json = run(ScriptedGateway(LOOKUP, finish()))
    assert "facts" not in as_text.body and "published@example.edu" in as_text.body["answer"]
    for key in ("status", "citations", "datasetVersion"):
        assert as_json.body[key] == as_text.body[key]
    assert as_json.body["citations"]


def test_an_ambiguous_or_missing_office_is_named_in_the_packet_not_written_as_a_sentence() -> None:
    ambiguous = run(ScriptedGateway(lookup_of("student"), finish()), two_student_offices())
    found = packet(ambiguous)
    assert ambiguous.body["status"] == "clarification" and ambiguous.body["answer"] == ""
    assert found["status"] == "ambiguous" and found["facts"] == []
    (entry,) = found["ambiguities"]
    assert entry["query"] == "student" and entry["truncated"] is False
    assert [c["name"] for c in entry["candidates"]] == ["Student Accounts", "Student Conduct"]
    assert all(c["id"] and c["match"] for c in entry["candidates"])
    missing = packet(run(ScriptedGateway(lookup_of("Cafeteria"), finish())))
    assert missing["status"] == "not_found"
    assert missing["unresolved"] == [{"query": "Cafeteria", "reason": "no_matching_office"}]


def test_what_the_code_used_to_write_is_a_notice_with_the_approved_wording() -> None:
    for kind in ("greeting", "thanks", "about"):
        (notice,) = notices(run(ScriptedGateway(finish(kind))))
        assert notice == {"type": kind, "approved_text": engine_module.FIXED_REPLIES[kind]}
        assert packet(run(ScriptedGateway(finish(kind))))["status"] == "no_facts_needed"
    (unsupported,) = notices(run(ScriptedGateway(finish("unsupported"))))
    assert unsupported == {"type": "unsupported", "afterLookup": False,
                           "approved_text": engine_module.UNSUPPORTED_MESSAGE}
    assert packet(run(ScriptedGateway(finish("unsupported"))))["status"] == "insufficient"
    (limit,) = notices(run(ScriptedGateway(finish("account_limit"))))
    assert limit["type"] == "account_limit"
    assert "personal student information" in limit["approved_text"]
    after = run(ScriptedGateway(LOOKUP, finish("unsupported")))
    assert packet(after)["status"] == "partial" and len(packet(after)["facts"]) == 1
    assert notices(after)[0]["afterLookup"] is True
    assert notices(after)[0]["approved_text"] == engine_module.UNSUPPORTED_AFTER_LOOKUP_MESSAGE
    (clock,) = notices(run(ScriptedGateway(finish("clock"))))
    assert clock["type"] == "clock" and datetime.fromisoformat(clock["campusNow"]) == NOW


def test_a_recall_carries_the_quoted_message_not_a_sentence_about_it() -> None:
    messages = [{"role": "user", "content": "hello there"}, {"role": "assistant", "content": "Hi!"},
                {"role": "user", "content": "what did I say first?"}]
    result = run(ScriptedGateway(finish("recall", message_index=0)), messages=messages)
    assert notices(result) == [{
        "type": "recall", "messageIndex": 0, "speaker": "user", "text": "hello there",
        "shortened": False, "earlierMessagesOmitted": False}]
    assert result.body["answer"] == "" and packet(result)["request"]["intent"] == "recall"


def emergency_office() -> MemoryEntityFacts:
    return source([office("ps", "Public Safety (Emergency)")], [contact("p1", "ps")])


def test_an_emergency_carries_the_kind_the_approved_guidance_and_the_campus_numbers() -> None:
    request = ChatRequest.model_validate(
        {"messages": [{"role": "user", "content": "my roommate collapsed"}]})
    engine = ChatEngine(ScriptedGateway(), emergency_office(), output="json")
    result = asyncio.run(engine.safety_reply(intake(request, now=NOW), "medical"))
    found = packet(result)
    assert result.body["status"] == "partial" and result.body["answer"] == ""
    assert found["status"] == "emergency" and found["request"]["intent"] == "safety"
    (notice,) = found["notices"]
    assert notice["type"] == "safety" and notice["situation"] == "medical"
    assert notice["approved_text"].startswith("Call 911 right now") and notice["contacts"] == ["ps"]
    (fact,) = found["facts"]
    assert fact["subject"]["name"] == "Public Safety (Emergency)" and fact["predicate"] == "phones"
    assert fact["purpose"] == "emergency_contact" and found["request"]["entities"] == []
    # Nothing readable: the kind and the guidance still go out, with no numbers.
    bare = ChatEngine(ScriptedGateway(), facts(), output="json")
    plain = packet(asyncio.run(bare.safety_reply(intake(request, now=NOW), "fire")))
    assert plain["status"] == "emergency" and plain["facts"] == []
    assert plain["notices"][0]["situation"] == "fire" and plain["notices"][0]["contacts"] == []
    assert plain["notices"][0]["approved_text"].startswith("If you can, get out")


def test_an_emergency_reply_never_fails_even_when_its_numbers_break_the_contract(
        monkeypatch: pytest.MonkeyPatch) -> None:
    real = engine_module.build_packet

    def refuse_numbers(as_of: str, parts: list[dict[str, Any]]) -> dict[str, Any]:
        if any(p.get("campusContacts") for p in parts):
            raise PacketInvalid("simulated")
        return real(as_of, parts)

    monkeypatch.setattr(engine_module, "build_packet", refuse_numbers)
    request = ChatRequest.model_validate(
        {"messages": [{"role": "user", "content": "my roommate collapsed"}]})
    engine = ChatEngine(ScriptedGateway(), emergency_office(), output="json")
    result = asyncio.run(engine.safety_reply(intake(request, now=NOW), "medical"))
    assert result.status_code == 200 and packet(result)["status"] == "emergency"
    assert packet(result)["facts"] == []


def test_a_safety_notice_from_the_model_comes_first_and_keeps_the_rest() -> None:
    gateway = ScriptedGateway(lookup_of("Public Safety (Emergency)"),
                              finish("safety", situation="fire"))
    result = run(gateway, emergency_office())
    found = packet(result)
    assert found["status"] == "emergency" and result.body["status"] == "partial"
    assert [n["type"] for n in found["notices"]] == ["safety"]
    assert found["notices"][0]["situation"] == "fire" and found["notices"][0]["contacts"] == ["ps"]
    assert sorted(f["predicate"] for f in found["facts"]) == ["email", "phones"]
    assert [f.get("purpose") for f in found["facts"] if f["predicate"] == "email"] == [None]
    assert [e["id"] for e in found["request"]["entities"]] == ["ps"]


def test_a_turn_cut_short_keeps_its_facts_and_says_why_without_calling_it_an_emergency() -> None:
    def provider_down() -> Completion:
        raise GatewayError("provider_unavailable")

    result = run(ScriptedGateway(LOOKUP, provider_down))
    found = packet(result)
    assert result.status_code == 200 and result.body["status"] == "partial"
    assert result.body["limitation"] == {"code": "provider_unavailable"}
    assert found["status"] == "partial" and len(found["facts"]) == 1
    assert [n["type"] for n in found["notices"]] == ["incomplete", "emergency_reminder"]
    assert found["notices"][0]["code"] == "provider_unavailable"
    assert found["notices"][0]["approved_text"] == engine_module.INCOMPLETE_TEXT
    assert found["notices"][1]["approved_text"] == engine_module.SAFETY_MESSAGE


def test_a_packet_that_breaks_the_contract_is_an_error_never_sent_to_a_writer(
        monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(as_of: str, parts: list[dict[str, Any]]) -> dict[str, Any]:
        raise PacketInvalid("simulated")

    monkeypatch.setattr(engine_module, "build_packet", broken)
    result = run(ScriptedGateway(LOOKUP, finish()))
    assert result.status_code == 503 and "facts" not in result.body
    assert result.body["error"]["code"] == "invalid_fact_packet"
    text = run(ScriptedGateway(LOOKUP, finish()), output="text")  # Text mode never builds one.
    assert text.status_code == 200


def test_a_failure_is_still_the_usual_error_not_facts() -> None:
    def provider_down() -> Completion:
        raise GatewayError("provider_unavailable")

    result = run(ScriptedGateway(provider_down))
    assert result.status_code == 503 and "facts" not in result.body
    assert result.body["error"]["code"] == "provider_unavailable"


def configured(monkeypatch: pytest.MonkeyPatch, output: str | None) -> ChatEngine:
    monkeypatch.setattr(app_module, "ProviderSettings",
                        SimpleNamespace(from_env=lambda: SimpleNamespace(max_turn_nusd=1)))
    monkeypatch.setattr(app_module, "Gateway", lambda settings: object())
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/test")
    monkeypatch.delenv("BRAIN_GRAPH_DIR", raising=False)
    monkeypatch.delenv("BRAIN_GRAPH_ONLY_DIR", raising=False)
    if output is None:
        monkeypatch.delenv("BRAIN_OUTPUT", raising=False)
    else:
        monkeypatch.setenv("BRAIN_OUTPUT", output)
    return app_module._configured_engine()  # noqa: SLF001 - the wiring under test.


@pytest.mark.parametrize("setting,expected", [(None, "text"), ("", "text"), ("text", "text"),
                                              ("json", "json"), (" JSON ", "json")])
def test_the_output_mode_comes_from_the_environment_and_defaults_to_text(
        monkeypatch: pytest.MonkeyPatch, setting: str | None, expected: str) -> None:
    engine = configured(monkeypatch, setting)
    assert engine.output == expected and isinstance(engine.facts, PostgresEntityFacts)
    assert engine.runtime()["output"] == expected
