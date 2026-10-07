"""BRAIN_OUTPUT=json: a turn answers with the facts as typed JSON parts and no written answer.

The AI still picks the lookup and the finish parts; the code returns what it found and stops. The
status, citations and release pins are the same as in text mode, which stays the default.
"""

import asyncio
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

import rockygpt_brain.api.app as app_module
from rockygpt_brain.contract import ChatRequest
from rockygpt_brain.engine import ChatEngine, ChatResult
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


def parts(result: ChatResult) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = result.body["facts"]["parts"]
    return found


def lookup_of(query: str) -> Completion:
    return completion("graph_lookup", {"requests": [{"query": query, "fields": ["email"]}]})


def test_a_lookup_returns_the_readers_facts_as_json_and_no_written_answer() -> None:
    service = facts()
    result = run(ScriptedGateway(LOOKUP, finish()), service)
    assert result.status_code == 200 and result.body["answer"] == ""
    assert datetime.fromisoformat(result.body["facts"]["asOf"]) == NOW
    (part,) = parts(result)
    assert part["kind"] == "office_facts" and part["query"] == "Registrar"
    assert part["fields"] == ["email"]
    assert part["office"] == {"id": "registrar", "name": "Registrar"}
    email = next(p for p in part["facts"]["properties"] if p["key"] == "email")
    assert email["status"] == "known" and email["values"][0]["value"] == "published@example.edu"
    # Exactly what the shared reader returned: nothing is merged, chosen or reworded.
    assert part["facts"] == service.get_office_facts(
        "registrar", ["email"], "release-1", identity_hash="identities-1", as_of=NOW)


def test_status_citations_and_release_match_text_mode_and_text_stays_the_default() -> None:
    as_text = run(ScriptedGateway(LOOKUP, finish()), output="text")
    as_json = run(ScriptedGateway(LOOKUP, finish()))
    assert "facts" not in as_text.body and "published@example.edu" in as_text.body["answer"]
    for key in ("status", "citations", "datasetVersion"):
        assert as_json.body[key] == as_text.body[key]
    assert as_json.body["citations"]


def test_an_ambiguous_or_missing_office_is_a_typed_part_not_a_sentence() -> None:
    ambiguous = run(ScriptedGateway(lookup_of("student"), finish()), two_student_offices())
    (part,) = parts(ambiguous)
    assert ambiguous.body["status"] == "clarification" and ambiguous.body["answer"] == ""
    assert part["kind"] == "ambiguous" and part["query"] == "student" and part["truncated"] is False
    assert [c["name"] for c in part["candidates"]] == ["Student Accounts", "Student Conduct"]
    assert all(c["id"] and c["match"] for c in part["candidates"])
    missing = run(ScriptedGateway(lookup_of("Cafeteria"), finish()))
    assert parts(missing) == [{"kind": "not_found", "query": "Cafeteria"}]


def test_what_the_code_used_to_write_is_a_typed_part_with_no_text() -> None:
    assert parts(run(ScriptedGateway(finish("greeting")))) == [{"kind": "greeting"}]
    assert parts(run(ScriptedGateway(finish("thanks")))) == [{"kind": "thanks"}]
    assert parts(run(ScriptedGateway(finish("about")))) == [{"kind": "about"}]
    assert parts(run(ScriptedGateway(finish("unsupported")))) == [
        {"kind": "unsupported", "afterLookup": False}]
    assert parts(run(ScriptedGateway(finish("account_limit")))) == [
        {"kind": "account_limit", "afterLookup": False}]
    after = parts(run(ScriptedGateway(LOOKUP, finish("unsupported"))))
    assert [p["kind"] for p in after] == ["office_facts", "unsupported"]
    assert after[1]["afterLookup"] is True
    (clock,) = parts(run(ScriptedGateway(finish("clock"))))
    assert clock["kind"] == "clock" and datetime.fromisoformat(clock["campusNow"]) == NOW


def test_a_recall_returns_the_quoted_message_not_a_sentence_about_it() -> None:
    messages = [{"role": "user", "content": "hello there"}, {"role": "assistant", "content": "Hi!"},
                {"role": "user", "content": "what did I say first?"}]
    result = run(ScriptedGateway(finish("recall", message_index=0)), messages=messages)
    (part,) = parts(result)
    assert part == {"kind": "recall", "messageIndex": 0, "speaker": "user", "text": "hello there",
                    "shortened": False, "earlierMessagesOmitted": False}
    assert result.body["answer"] == ""


def emergency_office() -> MemoryEntityFacts:
    return source([office("ps", "Public Safety (Emergency)")], [contact("p1", "ps")])


def test_an_emergency_returns_the_kind_and_the_campus_numbers_as_facts() -> None:
    request = ChatRequest.model_validate(
        {"messages": [{"role": "user", "content": "my roommate collapsed"}]})
    engine = ChatEngine(ScriptedGateway(), emergency_office(), output="json")
    result = asyncio.run(engine.safety_reply(intake(request, now=NOW), "medical"))
    (part,) = parts(result)
    assert result.body["status"] == "partial" and result.body["answer"] == ""
    assert part["kind"] == "safety" and part["situation"] == "medical"
    (contacts,) = part["campusContacts"]
    assert contacts["kind"] == "office_facts"
    assert contacts["office"]["name"] == "Public Safety (Emergency)"
    assert any(p["key"] == "phones" for p in contacts["facts"]["properties"])
    # Nothing readable: the kind still goes out, with no numbers.
    bare = ChatEngine(ScriptedGateway(), facts(), output="json")
    assert parts(asyncio.run(bare.safety_reply(intake(request, now=NOW), "fire"))) == [
        {"kind": "safety", "situation": "fire", "campusContacts": []}]


def test_a_safety_part_from_the_model_comes_first_and_keeps_the_rest() -> None:
    gateway = ScriptedGateway(lookup_of("Public Safety (Emergency)"),
                              finish("safety", situation="fire"))
    result = run(gateway, emergency_office())
    kinds = [p["kind"] for p in parts(result)]
    assert kinds == ["safety", "office_facts"] and result.body["status"] == "partial"
    assert parts(result)[0]["situation"] == "fire" and len(parts(result)[0]["campusContacts"]) == 1


def test_a_turn_cut_short_keeps_its_facts_and_says_why() -> None:
    def provider_down() -> Completion:
        raise GatewayError("provider_unavailable")

    result = run(ScriptedGateway(LOOKUP, provider_down))
    assert result.status_code == 200 and result.body["status"] == "partial"
    assert result.body["limitation"] == {"code": "provider_unavailable"}
    assert [p["kind"] for p in parts(result)] == ["office_facts", "incomplete", "safety"]
    assert parts(result)[1] == {"kind": "incomplete", "code": "provider_unavailable"}


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
