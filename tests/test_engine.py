"""Behavioral boundaries: evidence, history, tool recovery, and bounded execution."""

import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

from rockygpt_brain.contracts import Answer, ChatMessage
from rockygpt_brain.engine import MAX_MODEL_CALLS, InvalidAnswer, render_answer, run_turn

NOW = datetime(2026, 9, 4, 12, tzinfo=ZoneInfo("America/New_York"))
RECORD = {
    "id": "contacts:registrar",
    "title": "Registrar",
    "url": "https://www.ramapo.edu/registrar/",
    "collection": "contacts",
    "freshness": "fresh",
    "content": "Office: D-224",
}


def answer(
    text: str = "Try a study schedule.",
    kind: str = "guidance",
    ids: list[str] | None = None,
    status: str = "answered",
) -> SimpleNamespace:
    return SimpleNamespace(
        status="completed",
        model="test-model",
        output=[],
        output_text=json.dumps(
            {
                "status": status,
                "parts": [
                    {"kind": kind, "text": text, "evidence_ids": ids or []},
                ],
            }
        ),
    )


def search(call_id: str = "lookup", collection: str = "contacts") -> SimpleNamespace:
    return SimpleNamespace(
        type="function_call",
        name="search_campus",
        call_id=call_id,
        arguments=json.dumps(
            {
                "collection": collection,
                "query": "registrar",
                "date_from": None,
                "date_to": None,
                "limit": 4,
            }
        ),
    )


def tools(*calls: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(status="completed", model="test-model", output=list(calls))


def test_full_conversation_is_preserved_and_evidence_is_cited() -> None:
    messages = [
        ChatMessage(role="user", content="Where is the registrar?"),
        ChatMessage(role="assistant", content="Do you mean their campus office?"),
        ChatMessage(role="user", content="Yes, and how can I reach them?"),
    ]
    client, data = Mock(), Mock()
    client.responses.create.side_effect = [
        tools(search("office"), search("contact")),
        answer("The office is D-224.", "campus_fact", [RECORD["id"]]),
    ]
    data.search.return_value = {"status": "ok", "dataset_version": "release-1", "records": [RECORD]}
    result = run_turn(messages, client=client, data=data, model="test", now=NOW)
    first = client.responses.create.call_args_list[0].kwargs
    assert first["input"][:3] == [message.model_dump() for message in messages]
    assert first["store"] is False
    assert NOW.isoformat() in first["instructions"]
    assert "Friday, September 04, 2026" in first["instructions"]
    assert "2026-08-31 through 2026-09-06" in first["instructions"]
    second = client.responses.create.call_args_list[1].kwargs["input"]
    assert [
        x["call_id"]
        for x in second
        if isinstance(x, dict) and x.get("type") == "function_call_output"
    ] == ["office", "contact"]
    assert result["datasetVersion"] == "release-1"
    assert len(result["trace"]) == 2
    assert result["citations"][0]["id"] == RECORD["id"]
    assert RECORD["url"] in result["answer"]


def test_general_help_needs_no_database() -> None:
    client, data = Mock(), Mock()
    client.responses.create.return_value = answer()
    result = run_turn(
        [ChatMessage(role="user", content="Help me plan my study time")],
        client=client,
        data=data,
        model="test",
        now=NOW,
    )
    assert result["status"] == "answered"
    assert result["citations"] == []
    assert result["datasetVersion"] is None
    data.search.assert_not_called()


@pytest.mark.parametrize(
    ("ids", "freshness", "text"),
    [
        ([], "fresh", "The office is open."),
        (["invented"], "fresh", "The office is open."),
        ([RECORD["id"]], "stale", "The office is open."),
        ([RECORD["id"]], "unknown", "The office is open."),
        ([RECORD["id"]], "fresh", "See https://untrusted.example/"),
        ([RECORD["id"]], "fresh", "[click](javascript:alert(1))"),
        ([RECORD["id"]], "fresh", "Visit www.unverified.example"),
        ([RECORD["id"]], "fresh", "[help][ref]\n\n[ref]: //unverified.example"),
    ],
)
def test_invalid_grounding_is_rejected(ids: list[str], freshness: str, text: str) -> None:
    record = {**RECORD, "freshness": freshness}
    payload = Answer.model_validate_json(answer(text, "campus_fact", ids).output_text)
    with pytest.raises(InvalidAnswer):
        render_answer(payload, {RECORD["id"]: record})


def test_stale_evidence_can_explain_a_limitation() -> None:
    payload = Answer.model_validate_json(
        answer(
            "The published contact information is stale; I cannot confirm it.",
            "limitation",
            [RECORD["id"]],
            "unavailable",
        ).output_text
    )
    result = render_answer(payload, {RECORD["id"]: {**RECORD, "freshness": "stale"}})
    assert result["status"] == "unavailable"


def test_rendered_answers_fit_in_followup_history() -> None:
    payload = Answer.model_validate(
        {
            "status": "answered",
            "parts": [
                {"kind": "guidance", "text": "x" * 6000, "evidence_ids": []},
            ]
            * 3,
        }
    )
    with pytest.raises(InvalidAnswer, match="too long"):
        render_answer(payload, {})


def test_citations_display_source_titles_and_preserve_record_identity() -> None:
    record = {**RECORD, "title": "password.reset_url", "source_title": "Password Reset"}
    payload = Answer.model_validate_json(
        answer(
            "Use the official password reset service.", "campus_fact", [RECORD["id"]]
        ).output_text
    )
    result = render_answer(payload, {RECORD["id"]: record})
    assert "[Password Reset]" in result["answer"]
    assert result["citations"][0]["record_title"] == "password.reset_url"
    assert result["citations"][0]["id"] == RECORD["id"]


def test_plain_angle_brackets_are_allowed_in_general_explanations() -> None:
    payload = Answer.model_validate_json(answer("In this example, x < y > z.").output_text)
    assert render_answer(payload, {})["answer"] == "In this example, x < y > z."


def test_database_failure_is_not_empty_data_and_does_not_leak_secrets() -> None:
    client, data = Mock(), Mock()
    client.responses.create.side_effect = [
        tools(search()),
        answer("I cannot reach campus data right now.", "limitation", status="unavailable"),
    ]
    data.search.side_effect = RuntimeError("postgres://SECRET_HOST/SECRET_PASSWORD")
    result = run_turn(
        [ChatMessage(role="user", content="Where is the registrar?")],
        client=client,
        data=data,
        model="test",
        now=NOW,
    )
    assert result["status"] == "unavailable"
    assert result["trace"][0]["status"] == "unavailable"
    assert "SECRET" not in json.dumps(result)
    history = client.responses.create.call_args.kwargs["input"]
    tool_output = next(
        x["output"]
        for x in history
        if isinstance(x, dict) and x.get("type") == "function_call_output"
    )
    assert json.loads(tool_output)["reason"] == "campus_data_unavailable"
    assert "SECRET" not in tool_output


def test_missing_schedule_date_returns_actionable_validation_without_query_echo() -> None:
    client, data = Mock(), Mock()
    call = search(collection="shuttle")
    call.arguments = json.dumps(
        {
            "collection": "shuttle",
            "query": "PRIVATE_QUERY",
            "date_from": None,
            "date_to": None,
            "limit": 4,
        }
    )
    client.responses.create.side_effect = [
        tools(call),
        answer("Please specify the travel date.", "clarification", status="clarification"),
    ]
    run_turn(
        [ChatMessage(role="user", content="A schedule question")],
        client=client,
        data=data,
        model="test",
        now=NOW,
    )
    data.search.assert_not_called()
    history = client.responses.create.call_args.kwargs["input"]
    tool_output = next(
        x["output"]
        for x in history
        if isinstance(x, dict) and x.get("type") == "function_call_output"
    )
    assert "date_from" in tool_output
    assert "PRIVATE_QUERY" not in tool_output
    assert json.loads(tool_output)["status"] == "invalid_request"


def test_invalid_output_is_repaired_without_accepting_fake_citations() -> None:
    client, data = Mock(), Mock()
    client.responses.create.side_effect = [
        answer("It closes at ten.", "campus_fact", ["made-up"]),
        answer("I could not verify those hours.", "limitation", status="unavailable"),
    ]
    result = run_turn(
        [ChatMessage(role="user", content="When does it close?")],
        client=client,
        data=data,
        model="test",
        now=NOW,
    )
    assert result["status"] == "unavailable"
    assert result["citations"] == []


def test_repeated_invalid_output_stops_at_budget() -> None:
    client, data = Mock(), Mock()
    client.responses.create.return_value = answer("Unverified", "campus_fact", ["fake"])
    with pytest.raises(InvalidAnswer):
        run_turn(
            [ChatMessage(role="user", content="A question")],
            client=client,
            data=data,
            model="test",
            now=NOW,
        )
    assert client.responses.create.call_count == MAX_MODEL_CALLS
    assert client.responses.create.call_args.kwargs["tool_choice"] == "none"


def test_incomplete_provider_response_is_not_presented_as_an_answer() -> None:
    client, data = Mock(), Mock()
    incomplete = answer()
    incomplete.status = "incomplete"
    client.responses.create.return_value = incomplete
    with pytest.raises(InvalidAnswer):
        run_turn(
            [ChatMessage(role="user", content="A question")],
            client=client,
            data=data,
            model="test",
            now=NOW,
        )


def test_evidence_cannot_leak_between_turns() -> None:
    client, data = Mock(), Mock()
    client.responses.create.side_effect = [
        tools(search()),
        answer("D-224", "campus_fact", [RECORD["id"]]),
    ]
    data.search.return_value = {"status": "ok", "records": [RECORD]}
    run_turn(
        [ChatMessage(role="user", content="Registrar office?")],
        client=client,
        data=data,
        model="test",
        now=NOW,
    )
    client.responses.create.side_effect = None
    client.responses.create.return_value = answer("D-224", "campus_fact", [RECORD["id"]])
    with pytest.raises(InvalidAnswer):
        run_turn(
            [ChatMessage(role="user", content="Registrar office?")],
            client=client,
            data=data,
            model="test",
            now=NOW,
        )
