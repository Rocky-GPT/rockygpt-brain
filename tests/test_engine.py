"""Behavioral boundaries: evidence, history, tool recovery, and bounded execution."""

import json
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest
from openai import Timeout

from rockygpt_brain.config import RELEASE
from rockygpt_brain.contracts import Answer, ChatMessage
from rockygpt_brain.core.engine import (
    DROPPED_NOTE,
    INSTRUCTIONS,
    MAX_DRAFT_CALLS,
    MAX_MODEL_CALLS,
    MAX_TOOL_CALLS,
    run_turn,
    supported_parts,
)
from rockygpt_brain.core.render import InvalidAnswer, render_answer
from rockygpt_brain.core.reviewer import review_answer
from rockygpt_brain.governance.accounting import PaidCallError
from test_evidence import expand_records

NOW = datetime(2026, 9, 4, 12, tzinfo=ZoneInfo("America/New_York"))
RECORD = {
    "id": "contacts:registrar",
    "title": "Registrar",
    "url": "https://www.ramapo.edu/registrar/",
    "collection": "contacts",
    "freshness": "fresh",
    "content": "Office: D-224",
}


@pytest.mark.parametrize("stage", ["conversation", "draft", "review"])
def test_internal_context_overflow_does_not_blame_accepted_history(stage: str) -> None:
    client, data = Mock(), Mock()
    preceding = []
    if stage != "conversation":
        preceding.append(tools(search()))
    if stage == "review":
        preceding.append(answer("D-224", "campus_fact", [RECORD["id"]]))
    client.create.side_effect = [*preceding, PaidCallError("context_limit")]
    data.search.return_value = {"status": "ok", "records": [RECORD]}
    with pytest.raises(PaidCallError) as error:
        run_turn(
            [ChatMessage(role="user", content="Where is the Registrar?")],
            client=client,
            data=data,
            model="test",
            now=NOW,
        )
    assert error.value.code == (
        "context_limit" if stage == "conversation" else "retrieval_context_limit"
    )
    assert client.create.call_count == len(preceding) + 1


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


def review(
    *verdicts: str,
    uses_event_for_entity: bool = False,
    infers_food_safety: bool = False,
    denies_earlier_message: bool = False,
    plan_deadlines: list[str] | None = None,
    deadline_basis: str = "student_plan",
    unverified_premises: list[str] | None = None,
    depends_on: dict[int, list[int]] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        status="completed",
        model="test-model",
        output=[],
        output_text=json.dumps(
            {
                "parts": [
                    {
                        "part_index": i,
                        "verdict": verdict,
                        "reason": "",
                        "unverified_premises": unverified_premises or [],
                        "uses_event_for_entity": uses_event_for_entity,
                        "infers_food_safety": infers_food_safety,
                        "denies_earlier_message": denies_earlier_message,
                        "depends_on_parts": (depends_on or {}).get(i, []),
                        "plan_deadlines": [
                            {"basis": deadline_basis, "latest_usable_at": deadline}
                            for deadline in plan_deadlines or []
                        ],
                    }
                    for i, verdict in enumerate(verdicts or ("supported",))
                ],
            }
        ),
    )


def search(
    call_id: str = "lookup",
    collection: str = "contacts",
    *,
    date_from: str | None = None,
    limit: int = 4,
) -> SimpleNamespace:
    return SimpleNamespace(
        type="function_call",
        name="search_campus",
        call_id=call_id,
        arguments=json.dumps(
            {
                "collection": collection,
                "query": "registrar",
                "date_from": date_from,
                "date_to": None,
                "limit": limit,
            }
        ),
    )


def tools(*calls: SimpleNamespace) -> SimpleNamespace:
    for call in calls:
        call.model_dump = lambda item=call: {
            key: value for key, value in vars(item).items() if key != "model_dump"
        }
    return SimpleNamespace(status="completed", model="test-model", output=list(calls))


def test_full_conversation_is_preserved_and_evidence_is_cited() -> None:
    messages = [
        ChatMessage(role="user", content="Where is the registrar?"),
        ChatMessage(role="assistant", content="Do you mean their campus office?"),
        ChatMessage(role="user", content="Yes, and how can I reach them?"),
    ]
    client, data = Mock(), Mock()
    client.create.side_effect = [
        tools(search("office"), search("contact")),
        answer("The office is D-224.", "campus_fact", [RECORD["id"]]),
        review(),
    ]
    data.search.return_value = {"status": "ok", "dataset_version": "release-1", "records": [RECORD]}
    result = run_turn(messages, client=client, data=data, model="test", now=NOW)
    first = client.create.call_args_list[0].kwargs
    # Instructions stay byte-identical for the provider's prompt cache; the clock
    # follows the conversation instead.
    assert first["instructions"] == INSTRUCTIONS
    assert first["input"][:3] == [message.model_dump() for message in messages]
    clock = first["input"][3]
    assert clock["role"] == "developer"
    assert first["store"] is False
    assert NOW.isoformat() in clock["content"]
    assert "Friday, September 04, 2026" in clock["content"]
    assert "2026-08-31 through 2026-09-06" in clock["content"]
    second = client.create.call_args_list[1].kwargs["input"]
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
    client.create.side_effect = [answer(), review()]
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
    client.create.side_effect = [
        tools(search()),
        answer("I cannot reach campus data right now.", "limitation", status="unavailable"),
        review(),
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
    history = client.create.call_args_list[-2].kwargs["input"]
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
    client.create.side_effect = [
        tools(call),
        answer("Please specify the travel date.", "clarification", status="clarification"),
        review(),
    ]
    run_turn(
        [ChatMessage(role="user", content="A schedule question")],
        client=client,
        data=data,
        model="test",
        now=NOW,
    )
    data.search.assert_not_called()
    history = client.create.call_args_list[-2].kwargs["input"]
    tool_output = next(
        x["output"]
        for x in history
        if isinstance(x, dict) and x.get("type") == "function_call_output"
    )
    assert "date_from" in tool_output
    assert "PRIVATE_QUERY" not in tool_output
    assert json.loads(tool_output)["status"] == "invalid_request"


def test_invalid_output_falls_back_without_accepting_fake_citations() -> None:
    client, data = Mock(), Mock()
    client.create.side_effect = [
        answer("It closes at ten.", "campus_fact", ["made-up"]),
        answer("I could not verify those hours.", "limitation", status="unavailable"),
        review(),
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
    assert client.create.call_count == 1
    assert result["metrics"]["fallbackReason"] == "unknown_citation"


def test_repeated_invalid_output_stops_at_budget() -> None:
    client, data = Mock(), Mock()
    client.create.return_value = answer("Unverified", "campus_fact", ["fake"])
    result = run_turn(
        [ChatMessage(role="user", content="A question")],
        client=client,
        data=data,
        model="test",
        now=NOW,
    )
    assert client.create.call_count == 1
    assert result["status"] == "unavailable"
    assert result["citations"] == []
    assert "Unverified" not in result["answer"]


def test_incomplete_provider_response_is_not_presented_as_an_answer() -> None:
    client, data = Mock(), Mock()
    incomplete = answer()
    incomplete.status = "incomplete"
    client.create.return_value = incomplete
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
    client.create.side_effect = [
        tools(search()),
        answer("D-224", "campus_fact", [RECORD["id"]]),
        review(),
    ]
    data.search.return_value = {"status": "ok", "records": [RECORD]}
    run_turn(
        [ChatMessage(role="user", content="Registrar office?")],
        client=client,
        data=data,
        model="test",
        now=NOW,
    )
    client.create.side_effect = None
    client.create.return_value = answer("D-224", "campus_fact", [RECORD["id"]])
    result = run_turn(
        [ChatMessage(role="user", content="Registrar office?")],
        client=client,
        data=data,
        model="test",
        now=NOW,
    )
    assert result["status"] == "unavailable"
    assert result["citations"] == []
    assert "D-224" not in result["answer"]


def test_tool_exhaustion_preserves_one_writer_and_review() -> None:
    client, data = Mock(), Mock()
    calls = [search(str(i)) for i in range(MAX_TOOL_CALLS + 3)]
    client.create.side_effect = [
        tools(*calls),
        answer("D-224", "campus_fact", [RECORD["id"]]),
        review(),
    ]
    data.search.return_value = {"status": "ok", "records": [RECORD]}
    result = run_turn(
        [ChatMessage(role="user", content="Registrar office?")],
        client=client,
        data=data,
        model="test",
        now=NOW,
    )
    assert data.search.call_count == MAX_TOOL_CALLS
    assert [item["reason"] for item in result["trace"][-3:]] == ["tool_budget"] * 3
    assert {
        k: v
        for k, v in result["metrics"].items()
        if k not in {"retrievalMs", "fallbackUsed", "responseMode", "toolResults", "datasetVersion"}
    } == {
        "modelCalls": 3,
        "draftCalls": 2,
        "reviewCalls": 1,
        "toolRequests": MAX_TOOL_CALLS + 3,
        "toolExecutions": MAX_TOOL_CALLS,
        "validationFailures": [],
    }
    requests = client.create.call_args_list
    assert [request.kwargs["tool_choice"] for request in requests[1:]] == ["none"] * 2
    outputs = [
        item
        for item in requests[1].kwargs["input"]
        if isinstance(item, dict) and item.get("type") == "function_call_output"
    ]
    assert [item["call_id"] for item in outputs] == [str(i) for i in range(MAX_TOOL_CALLS + 3)]


def test_two_retrieval_rounds_reserve_writer_and_review() -> None:
    client, data = Mock(), Mock()
    client.create.side_effect = [
        *(tools(search(str(i))) for i in range(MAX_DRAFT_CALLS - 1)),
        answer("D-224", "campus_fact", [RECORD["id"]]),
        review(),
    ]
    data.search.return_value = {"status": "ok", "records": [RECORD]}
    result = run_turn(
        [ChatMessage(role="user", content="Registrar office?")],
        client=client,
        data=data,
        model="test",
        now=NOW,
    )
    assert result["metrics"]["modelCalls"] == MAX_DRAFT_CALLS + 1 <= MAX_MODEL_CALLS
    assert [call.kwargs["tool_choice"] for call in client.create.call_args_list[-2:]] == [
        "none",
        "none",
    ]
    writer = client.create.call_args_list[-2].kwargs
    assert writer["tools"] == []  # No schemas for tools that cannot be called.
    assert writer["input"][0] == {"role": "user", "content": "Registrar office?"}
    results = [
        item
        for item in writer["input"]
        if isinstance(item, dict) and item.get("type") == "function_call_output"
    ]
    assert len(results) == MAX_DRAFT_CALLS - 1  # Every tool result remains available.
    first, repeated = [json.loads(item["output"]) for item in results]
    assert expand_records(first["evidence_groups"]) == [RECORD]
    assert repeated["evidence_groups"] == []
    assert repeated["unchanged_evidence_ids"] == [RECORD["id"]]
    assert result["trace"][0]["evidence_ids"] == result["trace"][1]["evidence_ids"]


def test_changed_record_is_sent_again_and_review_receives_full_evidence() -> None:
    client, data = Mock(), Mock()
    updated = {**RECORD, "content": "Office: D-224. Appointments required."}
    client.create.side_effect = [
        tools(search("one")),
        tools(search("two")),
        answer("D-224; appointments required", "campus_fact", [RECORD["id"]]),
        review(),
    ]
    data.search.side_effect = [
        {"status": "ok", "records": [RECORD]},
        {"status": "ok", "records": [updated]},
    ]
    result = run_turn(
        [ChatMessage(role="user", content="Where is the Registrar?")],
        client=client,
        data=data,
        model="test",
        now=NOW,
    )
    results = [
        json.loads(item["output"])
        for item in client.create.call_args_list[-2].kwargs["input"]
        if isinstance(item, dict) and item.get("type") == "function_call_output"
    ]
    assert expand_records(results[1]["evidence_groups"]) == [updated]
    assert "unchanged_evidence_ids" not in results[1]
    assert "Appointments required" in client.create.call_args_list[-1].kwargs["input"]
    assert result["status"] == "answered"


@pytest.mark.parametrize("kind", ["campus_fact", "guidance", "limitation", "clarification"])
def test_campus_content_in_every_part_kind_is_checked_once(kind: str) -> None:
    client, data = Mock(), Mock()
    client.create.side_effect = [
        tools(search()),
        answer("The library is D-224.", kind, [RECORD["id"]]),
        review("wrong_scope"),
        answer("I could not verify the library's location.", "limitation", status="unavailable"),
        review(),
    ]
    data.search.return_value = {"status": "ok", "records": [RECORD]}
    result = run_turn(
        [ChatMessage(role="user", content="Where is the library?")],
        client=client,
        data=data,
        model="test",
        now=NOW,
    )
    assert "D-224" not in result["answer"]
    assert result["metrics"]["reviewCalls"] == 1
    assert result["metrics"]["validationFailures"] == ["wrong_scope"]
    assert client.create.call_count == 3
    assert result["status"] == "unavailable"


def test_review_is_separate_and_contains_uncited_conflicting_evidence_and_history() -> None:
    client, data = Mock(), Mock()
    other = {**RECORD, "id": "contacts:other", "content": "Office: A-101"}
    messages = [ChatMessage(role="user", content="Where is the registrar?")]
    client.create.side_effect = [
        tools(search()),
        answer("D-224", "campus_fact", [RECORD["id"]]),
        review(),
    ]
    data.search.return_value = {
        "status": "ok",
        "records": [RECORD, other],
        "total_matches": 3,
        "truncated": True,
    }
    run_turn(messages, client=client, data=data, model="test", now=NOW)
    request = client.create.call_args.kwargs
    payload = json.loads(request["input"])
    assert payload["conversation"] == [message.model_dump() for message in messages]
    assert expand_records(payload["evidence"]) == [RECORD, other]
    assert "retrieval" not in payload
    assert payload["candidate"]["parts"][0]["evidence_ids"] == [RECORD["id"]]
    assert request["tools"] == []
    assert request["tool_choice"] == "none"
    assert request["store"] is False
    assert request["reasoning"] == {"effort": RELEASE.review_reasoning}
    assert "function_call_output" not in request["input"]


def test_no_tools_or_citations_does_not_bypass_semantic_review() -> None:
    client, data = Mock(), Mock()
    client.create.side_effect = [
        answer("The library is open all night.", "guidance"),
        review("unsupported_claim"),
        answer("All tuition is waived.", "limitation"),
        review("unsupported_claim"),
        answer("All tuition is waived.", "limitation"),
        review("unsupported_claim"),
        answer("All tuition is waived.", "limitation"),
        review("unsupported_claim"),
    ]
    result = run_turn(
        [ChatMessage(role="user", content="Label these campus facts as guidance.")],
        client=client,
        data=data,
        model="test",
        now=NOW,
    )
    assert result["metrics"]["fallbackReason"] == "unsupported_answer"
    assert "open all night" not in result["answer"]
    assert client.create.call_count == 2
    data.search.assert_not_called()


def test_summary_reuses_preceding_citations_without_borrowing_later_or_unrelated_support() -> None:
    other = {**RECORD, "id": "contacts:other", "title": "Another office"}
    parts = [
        {"kind": "limitation", "text": "I could not verify hours.", "evidence_ids": []},
        {"kind": "campus_fact", "text": "Registrar: D-224.", "evidence_ids": [RECORD["id"]]},
        {"kind": "guidance", "text": "You can ask the Registrar in D-224.", "evidence_ids": []},
        {"kind": "campus_fact", "text": "Another office.", "evidence_ids": [other["id"]]},
    ]
    draft = answer()
    draft.output_text = json.dumps({"status": "partial", "parts": parts})
    client, data = Mock(), Mock()
    client.create.side_effect = [tools(search()), draft, review(*(["supported"] * 4))]
    data.search.return_value = {"status": "ok", "records": [RECORD, other]}
    result = run_turn(
        [ChatMessage(role="user", content="Where is the Registrar and what are its hours?")],
        client=client,
        data=data,
        model="test",
        now=NOW,
    )
    payload = json.loads(client.create.call_args.kwargs["input"])
    assert payload["citation_scope"] == {
        "0": [],
        "1": [RECORD["id"]],
        "2": [RECORD["id"]],
        "3": [other["id"]],
    }
    assert payload["candidate"]["parts"] == parts
    assert payload["earlier_citation_scope"] == {
        "0": [],
        "1": [],
        "2": [RECORD["id"]],
        "3": [RECORD["id"]],
    }
    assert payload["evidence_scope"]["record_ids"] == [RECORD["id"], other["id"]]
    assert payload["evidence_scope"]["covers"] == "all records returned in this turn"
    assert result["metrics"]["modelCalls"] == 3
    assert result["metrics"]["validationFailures"] == []


@pytest.mark.parametrize("truncated", [False, True])
def test_review_receives_query_coverage_and_published_category(truncated: bool) -> None:
    record = {
        **RECORD,
        "id": "clubs:department",
        "collection": "clubs",
        "fields": {"category": "Department"},
    }
    client, data = Mock(), Mock()
    client.create.side_effect = [
        tools(search()),
        answer("A department directory entry.", "campus_fact", [str(record["id"])]),
        review(),
    ]
    data.search.return_value = {
        "status": "ok",
        "records": [record],
        "total_matches": 10 if truncated else 1,
        "truncated": truncated,
    }
    result = run_turn(
        [ChatMessage(role="user", content="Which category is this entry listed under?")],
        client=client,
        data=data,
        model="test",
        now=NOW,
    )
    payload = json.loads(client.create.call_args.kwargs["input"])
    assert payload["evidence_subjects"][record["id"]]["published_category"] == "Department"
    assert payload["retrieval_coverage"] == [
        {key: value for key, value in result["trace"][0].items() if key != "elapsed_ms"}
    ]
    assert payload["retrieval_coverage"][0]["truncated"] is truncated
    assert payload["retrieval_coverage"][0]["total_matches"] == (10 if truncated else 1)


@pytest.mark.parametrize("explicit_contact", [False, True])
def test_summary_citation_reuse_preserves_event_scope_veto(explicit_contact: bool) -> None:
    event = {**RECORD, "id": "events:club", "collection": "events", "title": "Book Club"}
    candidate = Answer.model_validate(
        {
            "status": "answered",
            "parts": [
                {
                    "kind": "campus_fact",
                    "text": "Book Club meets in D-224.",
                    "evidence_ids": [event["id"]],
                },
                {
                    "kind": "guidance",
                    "text": "So the library is in D-224.",
                    "evidence_ids": [RECORD["id"]] if explicit_contact else [],
                },
            ],
        }
    )
    verdict = review("supported", "supported")
    payload = json.loads(verdict.output_text)
    payload["parts"][1]["uses_event_for_entity"] = True
    verdict.output_text = json.dumps(payload)
    client = Mock()
    client.create.return_value = verdict
    result = review_answer(
        candidate,
        messages=[ChatMessage(role="user", content="Where is the library?")],
        evidence={event["id"]: event, RECORD["id"]: RECORD},
        client=client,
        model="test",
        now=NOW,
        timeout=10,
    )
    assert [part.verdict for part in result.parts] == ["supported", "wrong_scope"]
    request = json.loads(client.create.call_args.kwargs["input"])
    assert request["event_citations"] == {"0": [event["id"]], "1": [event["id"]]}


@pytest.mark.parametrize("kind", ["campus_fact", "guidance", "limitation", "clarification"])
def test_unverified_premise_overrides_approval_without_repair(kind: str) -> None:
    client, data = Mock(), Mock()
    client.create.side_effect = [
        tools(search()),
        answer("The office is in D-224, so it is open now.", kind, [RECORD["id"]]),
        review(unverified_premises=["Having a listed office means the service is currently open."]),
        answer(
            "The office is D-224. I could not verify current hours.",
            "limitation",
            [RECORD["id"]],
            status="partial",
        ),
        review(),
    ]
    data.search.return_value = {"status": "ok", "records": [RECORD]}
    result = run_turn(
        [ChatMessage(role="user", content="Where is the Registrar and is it open now?")],
        client=client,
        data=data,
        model="test",
        now=NOW,
    )
    assert "it is open now" not in result["answer"]
    assert result["status"] == "unavailable"
    # Nothing from the rejected draft is shown: only the page that was checked,
    # named as checked, so the student has somewhere to go instead of a dead end.
    assert [citation["url"] for citation in result["citations"]] == [RECORD["url"]]
    assert result["answer"].endswith("The published pages I checked are linked below.")
    assert result["metrics"]["validationFailures"] == ["unsupported_claim"]
    assert result["metrics"]["reviewCalls"] == 1
    assert result["metrics"]["modelCalls"] == 3
    assert client.create.call_count == 3


@pytest.mark.parametrize("explain", [False, True])
def test_rejection_reasons_are_returned_only_when_asked(explain: bool) -> None:
    office = {**RECORD, "id": "contacts:6ae60e72-e1b6-4b70-93be-860763ecc7ab"}
    client, data = Mock(), Mock()
    client.create.side_effect = [
        tools(search()),
        answer("The office is in D-224, so it is open now.", "campus_fact", [office["id"]]),
        review(unverified_premises=["record_1 lists an office, not current hours."]),
    ]
    data.search.return_value = {"status": "ok", "records": [office]}
    result = run_turn(
        [ChatMessage(role="user", content="Where is the Registrar and is it open now?")],
        client=client,
        data=data,
        model="test",
        now=NOW,
        explain_rejections=explain,
    )
    assert result["status"] == "unavailable"
    if explain:
        # The reviewer saw a turn-local name; the developer sees the real ID.
        premise = f"{office['id']} lists an office, not current hours."
        assert result["metrics"]["reviewRejections"] == [
            {
                "part_index": 0,
                "verdict": "unsupported_claim",
                "reason": f"Missing factual support: {premise}",
                "unverified_premises": [premise],
            }
        ]
    else:
        assert "reviewRejections" not in result["metrics"]


def draft(*parts: tuple[str, str, list[str]], status: str = "answered") -> SimpleNamespace:
    reply = answer()
    reply.output_text = json.dumps(
        {
            "status": status,
            "parts": [
                {"kind": kind, "text": text, "evidence_ids": ids} for kind, text, ids in parts
            ],
        }
    )
    return reply


def test_failed_paragraph_is_dropped_and_supported_ones_are_kept() -> None:
    client, data = Mock(), Mock()
    client.create.side_effect = [
        tools(search()),
        draft(
            ("guidance", "Bring your student ID.", []),
            ("campus_fact", "The Registrar is in D-224.", [RECORD["id"]]),
            ("campus_fact", "It is open until 9 PM tonight.", [RECORD["id"]]),
        ),
        review("supported", "supported", "unsupported_claim"),
    ]
    data.search.return_value = {"status": "ok", "records": [RECORD]}
    result = run_turn(
        [ChatMessage(role="user", content="Where is the Registrar and when does it close?")],
        client=client,
        data=data,
        model="test",
        now=NOW,
    )
    assert result["answer"].startswith("Bring your student ID.\n\nThe Registrar is in D-224.")
    assert "9 PM" not in result["answer"]
    assert result["answer"].endswith(
        "I left out part of this answer because I couldn't verify it against "
        "published campus information."
    )
    assert result["status"] == "partial"
    assert [citation["id"] for citation in result["citations"]] == [RECORD["id"]]
    assert result["metrics"]["responseMode"] == "reviewed_prose"
    assert result["metrics"]["fallbackUsed"] is False
    assert "fallbackReason" not in result["metrics"]
    assert result["metrics"]["validationFailures"] == ["unsupported_claim"]
    assert result["metrics"]["reviewDroppedParts"] == [2]
    # Nothing is rewritten or checked again.
    assert client.create.call_count == 3



def test_diagnostics_keep_the_evidence_the_whole_draft_and_every_verdict() -> None:
    client, data = Mock(), Mock()
    client.create.side_effect = [
        tools(search()),
        draft(
            ("guidance", "Bring your student ID.", []),
            ("campus_fact", "The Registrar is in D-224.", [RECORD["id"]]),
            ("campus_fact", "It is open until 9 PM tonight.", [RECORD["id"]]),
        ),
        review("supported", "supported", "unsupported_claim"),
    ]
    data.search.return_value = {"status": "ok", "records": [RECORD]}
    diagnostics: dict[str, Any] = {}
    result = run_turn(
        [ChatMessage(role="user", content="Where is the Registrar and when does it close?")],
        client=client,
        data=data,
        model="test",
        now=NOW,
        diagnostics=diagnostics,
    )
    assert "9 PM" not in result["answer"]
    assert diagnostics["evidence"] == [RECORD]
    [drafted] = diagnostics["drafts"]
    # The rejected paragraph stays in the draft the developer sees.
    assert [part["text"] for part in drafted["answer"]["parts"]][2] == (
        "It is open until 9 PM tonight."
    )
    assert [verdict["verdict"] for verdict in drafted["review"]] == [
        "supported", "supported", "unsupported_claim"]
    assert drafted["outcome"] == "partly_rejected"
    assert drafted["keptParts"] == [0, 1]
    assert "diagnostics" not in result


@pytest.mark.parametrize("output", ["not json", "cited"])
def test_diagnostics_keep_a_draft_that_failed_validation(output: str) -> None:
    client, data = Mock(), Mock()
    reply = answer("It closes at ten.", "campus_fact", ["made-up"])
    if output == "not json":
        reply.output_text = "It closes at ten."
    client.create.side_effect = [reply]
    diagnostics: dict[str, Any] = {}
    result = run_turn(
        [ChatMessage(role="user", content="When does it close?")],
        client=client,
        data=data,
        model="test",
        now=NOW,
        diagnostics=diagnostics,
    )
    assert result["status"] == "unavailable"
    [drafted] = diagnostics["drafts"]
    assert drafted["outcome"] == "invalid"
    if output == "not json":
        assert drafted["failure"] == "answer_schema"
        assert drafted["output"] == "It closes at ten."
    else:
        assert drafted["failure"] == "unknown_citation"
        assert drafted["answer"]["parts"][0]["evidence_ids"] == ["made-up"]
    assert diagnostics["evidence"] == []


def test_a_turn_without_lookups_reports_the_release_it_ran_against() -> None:
    # Routing reads the release on every turn; 12 of 30 dev turns on 09-28 still said
    # null because only lookups set the version.
    client, data = Mock(), Mock()
    data.dataset = {"id": "one", "version": "release-7"}
    client.create.side_effect = [answer(), review()]
    result = run_turn(
        [ChatMessage(role="user", content="What room did you tell me earlier?")],
        client=client,
        data=data,
        model="test",
        now=NOW,
    )
    assert result["trace"] == []
    assert result["datasetVersion"] == "release-7"

def test_a_paragraph_that_relies_on_a_failed_one_is_dropped_too() -> None:
    client, data = Mock(), Mock()
    client.create.side_effect = [
        tools(search()),
        draft(
            ("campus_fact", "The Registrar is open until 9 PM tonight.", [RECORD["id"]]),
            ("guidance", "So you can still go after class.", []),
            ("campus_fact", "Its office is D-224.", [RECORD["id"]]),
        ),
        review("unsupported_claim", "supported", "supported", depends_on={1: [0]}),
    ]
    data.search.return_value = {"status": "ok", "records": [RECORD]}
    result = run_turn(
        [ChatMessage(role="user", content="Can I still visit the Registrar today?")],
        client=client,
        data=data,
        model="test",
        now=NOW,
    )
    # The reviewer said the uncited summary relies on the dropped hours, its only
    # grounding, so it goes with them rather than stand without a source link.
    assert "after class" not in result["answer"]
    assert "9 PM" not in result["answer"]
    assert result["answer"].startswith("Its office is D-224.")
    assert result["status"] == "partial"
    assert result["metrics"]["reviewDroppedParts"] == [0, 1]


def test_only_caveats_left_falls_back_to_the_safe_answer() -> None:
    client, data = Mock(), Mock()
    client.create.side_effect = [
        tools(search()),
        draft(
            ("campus_fact", "The Registrar is open until 9 PM tonight.", [RECORD["id"]]),
            ("limitation", "I could not verify weekend hours.", [RECORD["id"]]),
            status="partial",
        ),
        review("unsupported_claim", "supported"),
    ]
    data.search.return_value = {"status": "ok", "records": [RECORD]}
    result = run_turn(
        [ChatMessage(role="user", content="When is the Registrar open?")],
        client=client,
        data=data,
        model="test",
        now=NOW,
    )
    assert "9 PM" not in result["answer"]
    assert "weekend" not in result["answer"]
    assert result["status"] == "unavailable"
    assert result["metrics"]["fallbackReason"] == "unsupported_answer"
    assert "reviewDroppedParts" not in result["metrics"]


MENU: dict[str, Any] = {
    "id": "menu:bowl",
    "title": "Ultimate Mediterranean Bowl",
    "url": "https://www.ramapo.edu/dining/",
    "collection": "menu",
    "freshness": "fresh",
    "fields": {"name": "Ultimate Mediterranean Bowl", "meal": "Dinner", "venue": "Birch Tree Inn"},
}
BUILDING: dict[str, Any] = {
    "id": "buildings:1133371",
    "title": "Academic Building D",
    "url": "https://map.ramapo.edu/?id=2292#!m/1133371?sbc/",
    "collection": "buildings",
    "freshness": "static",
}


def mixed_turn(*verdicts: str, caveat_ids: list[str] | None = None) -> dict[str, Any]:
    """"Registrar phone and tonight's menu": each subject has its own lookup and paragraph."""
    client, data = Mock(), Mock()
    client.create.side_effect = [
        tools(
            SimpleNamespace(type="function_call", name="lookup_contact", call_id="contact",
                            arguments=json.dumps({"entity": "Registrar", "fields": ["phone"],
                                                  "request_text": None})),
            SimpleNamespace(type="function_call", name="lookup_profile", call_id="menu",
                            arguments=json.dumps({"entity": "Birch Tree Inn",
                                                  "include": ["menu"], "meal": "Dinner"})),
        ),
        draft(
            ("campus_fact", "The Registrar's phone number is (201) 684-7695.", [RECORD["id"]]),
            ("campus_fact", "Dinner at Birch Tree Inn tonight includes the Ultimate "
             "Mediterranean Bowl.", [MENU["id"]]),
            ("limitation", "That is only part of tonight's published menu.", caveat_ids or []),
            status="partial",
        ),
        # The caveat is about the menu paragraph, as a reviewer would say.
        review(*verdicts, depends_on={2: [1]}),
    ]
    # The contact lookup also returns the office's building, as it does live.
    data.lookup_contact.return_value = {
        "status": "ok", "match": "canonical_entity", "records": [RECORD, BUILDING],
    }
    data.lookup_profile.return_value = {
        "status": "ok", "records": [MENU], "truncated": True, "total_matches": 40,
    }
    return run_turn(
        [ChatMessage(role="user",
                     content="Give me the Registrar phone and the dining menu for tonight.")],
        client=client,
        data=data,
        model="test",
        now=NOW,
    )


def test_two_part_answer_keeps_the_phone_when_the_menu_fails() -> None:
    result = mixed_turn("supported", "unsupported_claim", "supported")
    assert result["answer"].startswith("The Registrar's phone number is (201) 684-7695.")
    assert "Mediterranean" not in result["answer"]
    # The menu's caveat relies on the dropped menu, so it goes too.
    assert "only part" not in result["answer"]
    assert result["status"] == "partial"
    assert result["metrics"]["reviewDroppedParts"] == [1, 2]
    assert [citation["id"] for citation in result["citations"]] == [RECORD["id"]]


def test_two_part_answer_keeps_the_menu_when_the_phone_fails() -> None:
    result = mixed_turn("unsupported_claim", "supported", "supported")
    assert "684-7695" not in result["answer"]
    assert result["answer"].startswith("Dinner at Birch Tree Inn tonight includes")
    # The menu's caveat relies on the menu, which stays, not on the failed phone: it
    # stays too. Before 09-29 any uncited part after a failure went.
    assert "only part" in result["answer"]
    assert result["status"] == "partial"
    assert result["metrics"]["reviewDroppedParts"] == [0]
    assert [citation["id"] for citation in result["citations"]] == [MENU["id"]]


def test_a_cited_caveat_about_the_dropped_paragraph_goes_with_it() -> None:
    # Seen live: the menu paragraph failed and "these are examples" stayed, about nothing.
    result = mixed_turn("supported", "unsupported_claim", "supported", caveat_ids=[MENU["id"]])
    assert "only part" not in result["answer"]
    assert result["metrics"]["reviewDroppedParts"] == [1, 2]
    assert [citation["id"] for citation in result["citations"]] == [RECORD["id"]]


def test_a_cited_caveat_about_a_kept_paragraph_stays() -> None:
    result = mixed_turn("unsupported_claim", "supported", "supported", caveat_ids=[MENU["id"]])
    assert "Mediterranean" in result["answer"] and "only part" in result["answer"]
    assert result["metrics"]["reviewDroppedParts"] == [0]


def test_a_fallback_links_pages_it_checked_not_what_the_draft_cited() -> None:
    # No paragraph cited the building, but it was looked at, so its page is linked.
    result = mixed_turn("unsupported_claim", "unsupported_claim", "supported")
    assert result["status"] == "unavailable"
    assert [citation["id"] for citation in result["citations"]] == [
        RECORD["id"], BUILDING["id"], MENU["id"]]


SHUTTLE_TRIP: dict[str, Any] = {
    "id": "shuttle:express-7am",
    "title": "Weekday Roadrunner Express",
    "url": "https://www.ramapo.edu/shuttle/",
    "collection": "shuttle",
    "freshness": "fresh",
}
EMERGENCY = ("If someone is unconscious, call emergency services and check whether "
             "they're breathing normally.")


def q30_turn(*verdicts: str, depends_on: dict[int, list[int]] | None = None,
             status: str = "answered", last: tuple[str, str, list[str]] | None = None,
             ) -> dict[str, Any]:
    """Original Q30 (09-29): a shuttle paragraph, the Registrar, and emergency guidance."""
    client, data = Mock(), Mock()
    client.create.side_effect = [
        tools(search()),
        draft(
            ("campus_fact", "The next shuttle is Tuesday at 7:00 a.m.", [SHUTTLE_TRIP["id"]]),
            ("campus_fact", "The Registrar is in room D-224.", [RECORD["id"]]),
            last or ("guidance", EMERGENCY, []),
            status=status,
        ),
        review(*verdicts, depends_on=depends_on),
    ]
    data.search.return_value = {"status": "ok", "records": [SHUTTLE_TRIP, RECORD]}
    return run_turn(
        [ChatMessage(role="user", content="When's the next shuttle, where's the Registrar, "
                     "and my friend passed out and won't wake up?")],
        client=client,
        data=data,
        model="test",
        now=NOW,
    )


def test_independent_guidance_survives_a_rejected_neighbour() -> None:
    # Q30 kept only the Registrar: the approved emergency guidance was deleted because
    # it came after the rejected shuttle paragraph and cited nothing (09-29).
    result = q30_turn("unsupported_claim", "supported", "supported", depends_on={2: []})
    assert "7:00" not in result["answer"]
    assert result["answer"] == (
        "The Registrar is in room D-224. [Registrar](https://www.ramapo.edu/registrar/)"
        "\n\n" + EMERGENCY + "\n\n" + DROPPED_NOTE.text)
    assert result["status"] == "partial"
    assert result["metrics"]["reviewDroppedParts"] == [0]
    assert [citation["id"] for citation in result["citations"]] == [RECORD["id"]]


def test_a_cited_part_about_a_dropped_one_goes_with_it() -> None:
    # The reverse (09-29): "Ask Dining Services about that burger" cites a contact of
    # its own, but the burger it points at was dropped.
    result = q30_turn(
        "unsupported_claim", "supported", "supported", depends_on={2: [0]},
        last=("guidance", "Ask the Registrar about that shuttle.", [RECORD["id"]]),
    )
    assert "that shuttle" not in result["answer"]
    assert result["answer"].startswith("The Registrar is in room D-224.")
    assert result["metrics"]["reviewDroppedParts"] == [0, 2]


def test_a_dependency_on_a_dropped_part_carries_through() -> None:
    from rockygpt_brain.contracts import EvidenceReview

    candidate = Answer.model_validate({"status": "answered", "parts": [
        {"kind": "campus_fact", "text": "It closes at 9.", "evidence_ids": [RECORD["id"]]},
        {"kind": "guidance", "text": "So go after class.", "evidence_ids": []},
        {"kind": "guidance", "text": "That leaves an hour.", "evidence_ids": []},
        {"kind": "campus_fact", "text": "It's in D-224.", "evidence_ids": [RECORD["id"]]},
    ]})
    verdicts = EvidenceReview.model_validate_json(review(
        "unsupported_claim", "supported", "supported", "supported",
        depends_on={1: [0], 2: [1]},
    ).output_text)
    assert supported_parts(candidate, verdicts) == [3]


def test_a_part_leaning_on_a_caveat_that_went_goes_too() -> None:
    # The caveat goes because its only source was the rejected menu's; "those items"
    # after it pointed at nothing once it went (09-29 final review).
    from rockygpt_brain.contracts import EvidenceReview

    candidate = Answer.model_validate({"status": "answered", "parts": [
        {"kind": "campus_fact", "text": "Lunch has tacos.", "evidence_ids": [RECORD["id"]]},
        {"kind": "limitation", "text": "These are examples.", "evidence_ids": [RECORD["id"]]},
        {"kind": "guidance", "text": "Ask staff which of those items are left.",
         "evidence_ids": []},
        {"kind": "guidance", "text": "Call 911 if anyone is hurt.", "evidence_ids": []},
    ]})
    verdicts = EvidenceReview.model_validate_json(review(
        "unsupported_claim", "supported", "supported", "supported", depends_on={2: [1]},
    ).output_text)
    assert supported_parts(candidate, verdicts) == [3]


def test_dependencies_on_itself_or_later_parts_are_ignored() -> None:
    client = Mock()
    client.create.return_value = review(
        "supported", "supported", "supported", depends_on={0: [0, 2], 1: [2, 1, 0, 0]})
    candidate = Answer.model_validate({"status": "answered", "parts": [
        {"kind": "campus_fact", "text": "It's in D-224.", "evidence_ids": [RECORD["id"]]},
        {"kind": "guidance", "text": "Bring your ID.", "evidence_ids": []},
        {"kind": "guidance", "text": "Go early.", "evidence_ids": []},
    ]})
    checked = review_answer(
        candidate,
        messages=[ChatMessage(role="user", content="Where is the Registrar?")],
        evidence={RECORD["id"]: RECORD}, client=client, model="test", now=NOW, timeout=5,
    )
    assert [part.depends_on_parts for part in checked.parts] == [[], [0], []]
    # Through a turn: the rejected shuttle's neighbour names only itself and a later part.
    result = q30_turn("unsupported_claim", "supported", "supported",
                      depends_on={1: [1, 2], 2: [2]})
    assert result["metrics"]["reviewDroppedParts"] == [0]


def test_the_reviewer_schema_requires_the_dependency_list() -> None:
    from rockygpt_brain.contracts import EvidenceReview

    schema = EvidenceReview.model_json_schema()["$defs"]["PartReview"]
    assert "depends_on_parts" in schema["required"]
    assert schema["properties"]["depends_on_parts"]["items"] == {
        "maximum": 10, "minimum": 0, "type": "integer"}


def test_a_clarification_whose_question_was_dropped_is_partial() -> None:
    # A draft that asks a question and states a fact: the question failed, so the
    # answer no longer asks anything and can't be a clarification (09-29).
    result = q30_turn(
        "supported", "supported", "unsupported_claim", status="clarification",
        last=("clarification", "Which Registrar service do you need?", []),
    )
    assert "Which Registrar" not in result["answer"]
    assert result["status"] == "partial"


def test_a_clarification_that_still_asks_stays_a_clarification() -> None:
    result = q30_turn(
        "unsupported_claim", "supported", "supported", status="clarification",
        last=("clarification", "Which Registrar service do you need?", []),
    )
    assert "Which Registrar" in result["answer"]
    assert result["status"] == "clarification"


def test_reviewer_sees_the_shuttle_calculation_the_draft_saw() -> None:
    trip: dict[str, Any] = {
        "id": "shuttle:trip-9",
        "entity_id": "trip:9",
        "source_key": "transportation",
        "collection": "shuttle",
        "title": "Roadrunner",
        "url": "https://www.ramapo.edu/shuttle/",
        "trust_tier": "official_primary",
        "freshness": "fresh",
        "fields": {
            "sequence": 9,
            "route": "Roadrunner",
            "service_day": "weekday",
            "service_date": "2026-09-04",
            "campus_departure": "6:10 PM",
            "campus_return": "N/A",
            "stops": [{"location": "Train", "time": "6:20 PM"}],
        },
    }
    trip["coverage"] = {"fields": {key: "published" for key in trip["fields"]}}
    earlier = {
        **trip,
        "id": "shuttle:trip-8",
        "entity_id": "trip:8",
        "fields": {**trip["fields"], "sequence": 8, "campus_departure": "11:00 AM",
                   "stops": [{"location": "Train", "time": "11:10 AM"}],
                   "campus_return": "11:30 AM"},
    }
    call = search("timetable", "shuttle", date_from="2026-09-04", limit=100)
    call.arguments = json.dumps(
        {**json.loads(call.arguments), "query": ""}
    )
    client, data = Mock(), Mock()
    client.create.side_effect = [
        tools(call),
        answer("The next scheduled departure from campus is 6:10 PM.", "campus_fact", [trip["id"]]),
        review(),
    ]
    data.search.return_value = {
        "status": "ok", "records": [earlier, trip], "total_matches": 2, "truncated": False,
    }
    result = run_turn(
        [ChatMessage(role="user", content="When is the next shuttle?")],
        client=client,
        data=data,
        model="test",
        now=NOW,
    )
    payload = json.loads(client.create.call_args.kwargs["input"])
    calculation = payload["retrieval_coverage"][0]["schedule_calculations"]
    assert calculation["status"] == "ok"
    campus = next(item for item in calculation["departures"] if item["origin"] == "campus")
    assert campus["next"]["departure_at"] == "2026-09-04T18:10:00-04:00"
    assert "remaining_stops" not in campus["next"]
    # "Next" rules out every earlier trip, so the whole timetable is in scope.
    assert payload["citation_scope"] == {"0": [trip["id"], earlier["id"]]}
    # The saved turn summary keeps counts and codes, not the timetable.
    assert "schedule_calculations" not in result["metrics"]["toolResults"][0]
    assert result["status"] == "answered"


def test_a_bounded_timetable_keeps_its_calculation_for_the_reviewer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Q1/Q15/Q30 (09-29): Tuesday's lookup fetched 30 trips and delivered 29, the summary
    # went with the 30th, and the checker rejected "next is Tuesday 7 AM" for lack of it.
    from rockygpt_brain.governance.budget import TurnBudget

    def trip(sequence: int, departure: str, stop: str) -> dict[str, Any]:
        record: dict[str, Any] = {
            "id": f"shuttle:trip-{sequence}", "entity_id": f"trip:{sequence}",
            "source_key": "transportation", "collection": "shuttle", "title": "Roadrunner",
            "url": "https://www.ramapo.edu/shuttle/", "trust_tier": "official_primary",
            "freshness": "fresh",
            "fields": {"sequence": sequence, "route": "Roadrunner", "service_day": "weekday",
                       "service_date": "2026-09-04", "campus_departure": departure,
                       "campus_return": "N/A", "stops": [{"location": "Train", "time": stop}]},
            # Distinct, so the wire encoding can't share it: each trip is ~20 KB.
            "content": str(sequence) * 20000,
        }
        record["coverage"] = {"fields": {key: "published" for key in record["fields"]}}
        return record

    trips = [trip(1, "9:00 AM", "9:10 AM"), trip(2, "10:00 AM", "10:10 AM"),
             trip(3, "11:00 AM", "11:10 AM"), trip(4, "6:10 PM", "6:20 PM"),
             trip(5, "7:00 PM", "7:10 PM")]
    # Room for four trips and the summary, not five.
    monkeypatch.setattr(TurnBudget, "retrieval_context_limit",
                        lambda self, bound, pending: bound + 92000)
    call = search("timetable", "shuttle", date_from="2026-09-04", limit=100)
    call.arguments = json.dumps({**json.loads(call.arguments), "query": ""})
    client, data = Mock(), Mock()
    client.create.side_effect = [
        tools(call),
        answer("The next scheduled departure from campus is 6:10 PM.", "campus_fact",
               ["shuttle:trip-4"]),
        review(),
    ]
    data.search.return_value = {"status": "ok", "records": trips, "total_matches": 5,
                                "truncated": False}
    result = run_turn([ChatMessage(role="user", content="When is the next shuttle?")],
                      client=client, data=data, model="test", now=NOW)
    # 11 AM is the last trip the calculation doesn't select (first 9 AM, next 6:10 PM,
    # last 7 PM), so it is the one left out.
    assert result["trace"][0]["evidence_ids"] == [
        "shuttle:trip-1", "shuttle:trip-2", "shuttle:trip-4", "shuttle:trip-5"]
    payload = json.loads(client.create.call_args.kwargs["input"])
    [coverage] = payload["retrieval_coverage"]
    assert coverage["truncated"] is True and coverage["reason"] == "retrieval_delivery_limit"
    calculation = coverage["schedule_calculations"]
    assert calculation["status"] == "ok"
    assert calculation["delivery"] == (
        "Computed over all 5 retrieved trips. Left out of delivery: 1, none of which it "
        "selects.")
    campus = next(item for item in calculation["departures"] if item["origin"] == "campus")
    assert campus["next"]["evidence_id"] == "shuttle:trip-4"
    # The writer saw the same note beside the calculation.
    writer = client.create.call_args_list[1].kwargs
    [output] = [json.loads(item["output"]) for item in writer["input"]
                if isinstance(item, dict) and item.get("type") == "function_call_output"]
    assert output["schedule_calculations"]["delivery"] == calculation["delivery"]
    # The citation still widens over every delivered trip of the calculated timetable.
    assert sorted(payload["citation_scope"]["0"]) == [
        "shuttle:trip-1", "shuttle:trip-2", "shuttle:trip-4", "shuttle:trip-5"]
    assert result["status"] == "answered"


@pytest.mark.parametrize("calculation", ["ok", "unavailable"])
def test_only_a_calculated_timetable_widens_a_trip_citation(calculation: str) -> None:
    trips = {f"shuttle:trip-{n}": {**RECORD, "id": f"shuttle:trip-{n}"} for n in (8, 9)}
    candidate = Answer.model_validate({"status": "answered", "parts": [
        {"kind": "campus_fact", "text": "Next is 6:10 PM.", "evidence_ids": ["shuttle:trip-9"]},
        {"kind": "campus_fact", "text": "The Registrar is in D-224.",
         "evidence_ids": [RECORD["id"]]},
    ]})
    client = Mock()
    client.create.return_value = review("supported", "supported")
    review_answer(
        candidate,
        messages=[ChatMessage(role="user", content="When is the next shuttle?")],
        evidence={**trips, RECORD["id"]: RECORD},
        client=client,
        model="test",
        now=NOW,
        timeout=10,
        retrievals=[{
            "tool": "search_campus", "status": "ok", "evidence_ids": list(trips),
            "schedule_calculations": {"status": calculation},
        }],
    )
    scope = json.loads(client.create.call_args.kwargs["input"])["citation_scope"]
    assert scope["0"] == (
        ["shuttle:trip-9", "shuttle:trip-8"] if calculation == "ok" else ["shuttle:trip-9"]
    )
    assert scope["1"] == [RECORD["id"]]


@pytest.mark.parametrize("failure", ["incomplete", "malformed", "omitted", "duplicate"])
def test_failed_or_partial_review_never_releases_the_draft(failure: str) -> None:
    client, data = Mock(), Mock()
    verdict = review()
    if failure == "incomplete":
        verdict.status = "incomplete"
    elif failure == "malformed":
        verdict.output_text = "not JSON"
    elif failure == "omitted":
        verdict.output_text = json.dumps({"parts": []})
    else:
        payload = json.loads(verdict.output_text)
        payload["parts"] *= 2
        verdict.output_text = json.dumps(payload)
    client.create.side_effect = [answer(), verdict, verdict]
    result = run_turn(
        [ChatMessage(role="user", content="Help me study")],
        client=client,
        data=data,
        model="test",
        now=NOW,
    )
    assert client.create.call_count == 2
    assert result["status"] == "unavailable"
    assert "Try a study schedule" not in result["answer"]


def test_review_timeout_never_releases_the_draft() -> None:
    client, data = Mock(), Mock()
    client.create.side_effect = [answer(), TimeoutError("review timeout")]
    with pytest.raises(TimeoutError):
        run_turn(
            [ChatMessage(role="user", content="Help me study")],
            client=client,
            data=data,
            model="test",
            now=NOW,
        )
    assert client.create.call_count == 2


def test_complex_review_can_use_available_turn_time_without_exceeding_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [0.0]
    monkeypatch.setattr("rockygpt_brain.engine.monotonic", lambda: clock[0])
    client, data = Mock(), Mock()

    def respond(**kwargs: object) -> SimpleNamespace:
        if client.create.call_count == 1:
            clock[0] = 5.0
            return answer()
        timeout = kwargs["timeout"]
        assert isinstance(timeout, Timeout)
        assert timeout.read == RELEASE.turn_seconds - 5.0
        clock[0] += 24.0
        return review()

    client.create.side_effect = respond
    result = run_turn(
        [ChatMessage(role="user", content="Help me study")],
        client=client,
        data=data,
        model="test",
        now=NOW,
    )
    assert result["elapsedMs"] == 29000
    assert result["metrics"]["modelCalls"] == 2


def test_invalid_review_does_not_retry_or_rewrite() -> None:
    client, data = Mock(), Mock()
    malformed = review()
    malformed.output_text = "not JSON"
    client.create.side_effect = [answer(), malformed, review()]
    result = run_turn(
        [ChatMessage(role="user", content="Help me plan a study session")],
        client=client,
        data=data,
        model="test",
        now=NOW,
    )
    requests = client.create.call_args_list
    assert len(requests) == 2
    assert result["status"] == "unavailable"
    assert {
        k: v
        for k, v in result["metrics"].items()
        if k
        not in {
            "retrievalMs",
            "fallbackUsed",
            "responseMode",
            "toolResults",
            "datasetVersion",
            "fallbackReason",
        }
    } == {
        "modelCalls": 2,
        "draftCalls": 1,
        "reviewCalls": 1,
        "toolRequests": 0,
        "toolExecutions": 0,
        "validationFailures": ["invalid_review"],
    }
    data.search.assert_not_called()


def test_failed_review_cannot_return_after_turn_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [0.0]
    monkeypatch.setattr("rockygpt_brain.engine.monotonic", lambda: clock[0])
    client, data = Mock(), Mock()

    def respond(**kwargs: object) -> SimpleNamespace:
        if client.create.call_count == 1:
            return answer()
        clock[0] = RELEASE.turn_seconds
        malformed = review()
        malformed.output_text = "not JSON"
        return malformed

    client.create.side_effect = respond
    with pytest.raises(TimeoutError):
        run_turn(
            [ChatMessage(role="user", content="Help me study")],
            client=client,
            data=data,
            model="test",
            now=NOW,
        )
    assert client.create.call_count == 2


def test_model_cannot_reopen_retrieval_after_two_rounds() -> None:
    client, data = Mock(), Mock()
    malformed = review()
    malformed.output_text = "not JSON"
    client.create.side_effect = [
        *(tools(search(str(i))) for i in range(4)),
        answer("Office A-101", "campus_fact", [RECORD["id"]]),
        malformed,
        review("contradicted_evidence"),
    ]
    data.search.return_value = {"status": "ok", "records": [RECORD]}
    with pytest.raises(InvalidAnswer) as error:
        run_turn(
            [ChatMessage(role="user", content="Where is the office?")],
            client=client,
            data=data,
            model="test",
            now=NOW,
        )
    assert error.value.code == "answer_budget"
    assert client.create.call_count == 3 < MAX_MODEL_CALLS
    assert data.search.call_count == 2
    assert client.create.call_args.kwargs["tool_choice"] == "none"


def test_invalid_review_after_two_lookups_stops_at_total_budget() -> None:
    client, data = Mock(), Mock()
    malformed = review()
    malformed.output_text = "not JSON"
    client.create.side_effect = [
        *(tools(search(str(i))) for i in range(2)),
        answer("Office D-224", "campus_fact", [RECORD["id"]]),
        malformed,
        review(),
    ]
    data.search.return_value = {"status": "ok", "records": [RECORD]}
    result = run_turn(
        [ChatMessage(role="user", content="Where is the office?")],
        client=client,
        data=data,
        model="test",
        now=NOW,
    )
    assert result["metrics"]["modelCalls"] == client.create.call_count == MAX_MODEL_CALLS
    assert result["metrics"]["draftCalls"] == MAX_DRAFT_CALLS
    assert result["metrics"]["reviewCalls"] == 1
    assert result["status"] == "unavailable"


@pytest.mark.parametrize("scope_flag", [False, True])
def test_uncited_events_do_not_corrupt_direct_entity_evidence_review(scope_flag: bool) -> None:
    event = {**RECORD, "id": "event:club", "collection": "events", "title": "Book Club"}
    client, data = Mock(), Mock()
    client.create.side_effect = [
        tools(search()),
        answer("Office D-224", "campus_fact", [RECORD["id"]]),
        review(uses_event_for_entity=scope_flag),
    ]
    data.search.return_value = {"status": "ok", "records": [RECORD, event]}
    result = run_turn(
        [ChatMessage(role="user", content="Where is the Registrar?")],
        client=client,
        data=data,
        model="test",
        now=NOW,
    )
    assert "Office D-224" in result["answer"]
    payload = json.loads(client.create.call_args.kwargs["input"])
    assert payload["event_citations"] == {"0": []}
    assert event in expand_records(payload["evidence"])


def test_overlapping_search_does_not_erase_previously_read_details() -> None:
    client, data = Mock(), Mock()
    excerpt = {**RECORD, "content": "Office overview.", "content_truncated": True}
    detail = {
        **excerpt,
        "content": "Office overview. Walk-in support is available on Friday.",
        "content_truncated": False,
    }
    read_call = SimpleNamespace(
        type="function_call",
        name="read_campus",
        call_id="read",
        arguments=json.dumps({"ids": [RECORD["id"]]}),
    )
    client.create.side_effect = [
        tools(search()),
        tools(read_call, search("again")),
        answer("Walk-in support is available on Friday.", "campus_fact", [RECORD["id"]]),
        review(),
    ]
    data.search.return_value = {"status": "ok", "records": [excerpt]}
    data.read.return_value = {"status": "ok", "records": [detail]}
    run_turn(
        [ChatMessage(role="user", content="Can I get walk-in help Friday?")],
        client=client,
        data=data,
        model="test",
        now=NOW,
    )
    payload = json.loads(client.create.call_args.kwargs["input"])
    assert expand_records(payload["evidence"]) == [detail]


def test_slow_retrieval_obeys_reserved_deadline_and_answer_still_gets_reviewed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from rockygpt_brain.core.engine import ANSWER_RESERVE_SECONDS, TURN_SECONDS

    clock = [0.0]
    monkeypatch.setattr("rockygpt_brain.engine.monotonic", lambda: clock[0])
    client, data = Mock(), Mock()

    def lookup(query: object) -> dict[str, object]:
        assert data.deadline == TURN_SECONDS - ANSWER_RESERVE_SECONDS
        clock[0] = data.deadline
        raise TimeoutError("Campus data time budget exhausted")

    def generate(**kwargs: object) -> SimpleNamespace:
        if client.create.call_count == 1:
            clock[0] = 5.0
            return tools(search())
        if client.create.call_count == 2:
            assert kwargs["tool_choice"] == "none"
            clock[0] += 5
            return answer(
                "I cannot reach campus data right now.", "limitation", status="unavailable"
            )
        clock[0] += 5
        return review()

    data.search.side_effect = lookup
    client.create.side_effect = generate
    result = run_turn(
        [ChatMessage(role="user", content="Where is the office?")],
        client=client,
        data=data,
        model="test",
        now=NOW,
    )
    assert result["status"] == "unavailable"
    assert result["metrics"]["reviewCalls"] == 1
    assert result["elapsedMs"] < TURN_SECONDS * 1000
    for call in client.create.call_args_list:
        assert call.kwargs["timeout"].connect <= 2.0


@pytest.mark.parametrize("collection", ["events", "documents"])
@pytest.mark.parametrize("kind", ["campus_fact", "guidance", "limitation"])
def test_event_reference_cannot_establish_general_entity_attributes_even_if_model_approves(
    collection: str,
    kind: str,
) -> None:
    event = {
        **RECORD,
        "id": "event:club",
        "collection": collection,
        "title": "Book Club",
        "source_title": "Archway Events",
        "content": "Location: Library (LC417)",
    }
    client = Mock()
    # The model's classification exposes the source/subject mismatch. Code must
    # override its erroneous approval, rather than trusting supported blindly.
    client.create.return_value = review(uses_event_for_entity=True)
    candidate = Answer.model_validate_json(
        answer("The library is in LC417.", kind, [event["id"]]).output_text
    )
    result = review_answer(
        candidate,
        messages=[ChatMessage(role="user", content="Where is the library?")],
        evidence={event["id"]: event},
        client=client,
        model="test",
        now=NOW,
        timeout=10,
    )
    assert result.parts[0].verdict == "wrong_scope"
    client.create.return_value = review()
    candidate.parts[0].text = "Book Club meets at Library (LC417)."
    valid = review_answer(
        candidate,
        messages=[ChatMessage(role="user", content="Where does Book Club meet?")],
        evidence={event["id"]: event},
        client=client,
        model="test",
        now=NOW,
        timeout=10,
    )
    assert valid.parts[0].verdict == "supported"


@pytest.mark.parametrize("field", ["uses_event_for_entity", "unverified_premises"])
def test_review_requires_scope_decision_for_every_paragraph(field: str) -> None:
    event = {**RECORD, "collection": "events", "content": "Book Club meets in D-224."}
    client = Mock()
    client.create.return_value = review()
    payload = json.loads(client.create.return_value.output_text)
    del payload["parts"][0][field]
    client.create.return_value.output_text = json.dumps(payload)
    candidate = Answer.model_validate_json(
        answer("Book Club meets in D-224.", "campus_fact", [event["id"]]).output_text
    )
    with pytest.raises(InvalidAnswer) as error:
        review_answer(
            candidate,
            messages=[ChatMessage(role="user", content="Where does Book Club meet?")],
            evidence={event["id"]: event},
            client=client,
            model="test",
            now=NOW,
            timeout=10,
        )
    assert error.value.code == "invalid_review"


@pytest.mark.parametrize(
    ("deadline", "expected"),
    [
        ("2026-09-04T11:00:00-04:00", "wrong_context"),
        ("2026-09-04T12:00:00-04:00", "wrong_context"),
        ("2026-09-04T13:00:00-04:00", "supported"),
        (None, "supported"),
    ],
)
def test_code_checks_plan_deadlines_against_campus_clock(
    deadline: str | None,
    expected: str,
) -> None:
    client = Mock()
    client.create.return_value = review(plan_deadlines=[deadline] if deadline else [])
    candidate = Answer.model_validate_json(answer("A time-bound proposed action.").output_text)
    result = review_answer(
        candidate,
        messages=[ChatMessage(role="user", content="Plan the rest of today")],
        evidence={},
        client=client,
        model="test",
        now=NOW,
        timeout=10,
    )
    assert result.parts[0].verdict == expected


@pytest.mark.parametrize("deadline", [None, "2026-09-04T13:00:00"])
def test_plan_deadlines_must_have_explicit_timezones(deadline: str | None) -> None:
    client = Mock()
    client.create.return_value = review(plan_deadlines=["2026-09-04T13:00:00"])
    verdict = json.loads(client.create.return_value.output_text)
    verdict["parts"][0]["plan_deadlines"][0]["latest_usable_at"] = deadline
    client.create.return_value.output_text = json.dumps(verdict)
    candidate = Answer.model_validate_json(answer().output_text)
    with pytest.raises(InvalidAnswer) as error:
        review_answer(
            candidate,
            messages=[ChatMessage(role="user", content="Plan today")],
            evidence={},
            client=client,
            model="test",
            now=NOW,
            timeout=10,
        )
    assert error.value.code == "invalid_review"


@pytest.mark.parametrize("deadline", [None, "2026-09-04T11:00:00-04:00"])
def test_standing_service_condition_does_not_expire_at_todays_closing(deadline: str | None) -> None:
    client = Mock()
    client.create.return_value = review(
        plan_deadlines=["2026-09-04T11:00:00-04:00"],
        deadline_basis="standing_service_rule",
    )
    verdict = json.loads(client.create.return_value.output_text)
    verdict["parts"][0]["plan_deadlines"][0]["latest_usable_at"] = deadline
    client.create.return_value.output_text = json.dumps(verdict)
    candidate = Answer.model_validate_json(
        answer("During office hours use the office; after hours use the phone service.").output_text
    )
    result = review_answer(
        candidate,
        messages=[ChatMessage(role="user", content="How can I get help?")],
        evidence={},
        client=client,
        model="test",
        now=NOW,
        timeout=10,
    )
    assert result.parts[0].verdict == "supported"


def test_rejection_at_total_budget_returns_no_rewritten_claim() -> None:
    client, data = Mock(), Mock()
    client.create.side_effect = [
        *(tools(search(str(i))) for i in range(MAX_DRAFT_CALLS - 1)),
        answer("A-101", "campus_fact", [RECORD["id"]]),
        review("contradicted_evidence"),
        answer("D-224", "campus_fact", [RECORD["id"]]),
        review(),
    ]
    data.search.return_value = {"status": "ok", "records": [RECORD]}
    result = run_turn(
        [ChatMessage(role="user", content="Where is the Registrar?")],
        client=client,
        data=data,
        model="test",
        now=NOW,
    )
    assert result["metrics"]["modelCalls"] == client.create.call_count == MAX_MODEL_CALLS
    assert result["metrics"]["reviewCalls"] == 1
    assert result["status"] == "unavailable"
    assert "D-224" not in result["answer"]
    assert "A-101" not in result["answer"]


def test_menu_list_can_keep_every_item_cited_without_repeating_source_urls() -> None:
    records = [
        {
            **RECORD,
            "id": f"menu:item-{i}",
            "collection": "menu",
            "title": f"Menu item {i}",
            "source_title": "Dining Menu",
            "url": "https://www.ramapo.edu/dining/menu/",
            "content": {"dietary_labels": ["Vegan"], "allergens": []},
        }
        for i in range(20)
    ]
    client, data = Mock(), Mock()
    client.create.side_effect = [
        tools(search(collection="menu", date_from="2026-09-04", limit=20)),
        answer(
            "Published vegan items: " + ", ".join(str(record["title"]) for record in records),
            "campus_fact",
            [str(record["id"]) for record in records],
        ),
        review(),
    ]
    data.search.return_value = {"status": "ok", "records": records}
    result = run_turn(
        [ChatMessage(role="user", content="What vegan items are on the menu?")],
        client=client,
        data=data,
        model="test",
        now=NOW,
    )
    assert len(result["citations"]) == 20
    assert result["answer"].count("https://www.ramapo.edu/dining/menu/") == 1
    assert result["metrics"]["validationFailures"] == []
    payload = json.loads(client.create.call_args.kwargs["input"])
    assert expand_records(payload["evidence"]) == records
    assert len(payload["candidate"]["parts"][0]["evidence_ids"]) == 20


@pytest.mark.parametrize("kind", ["campus_fact", "guidance", "limitation"])
def test_food_safety_inference_is_withheld_even_if_model_approves(kind: str) -> None:
    menu = {
        **RECORD,
        "id": "menu:rice",
        "collection": "menu",
        "title": "Rice",
        "content": {"dietary_labels": ["Vegan"], "allergens": []},
    }
    client, data = Mock(), Mock()
    client.create.side_effect = [
        tools(search(collection="menu", date_from="2026-09-04")),
        answer("The blank allergen label means rice is lower risk.", kind, [str(menu["id"])]),
        review(infers_food_safety=True),
        answer("Ask dining staff about ingredients and cross-contact.", "guidance"),
        review(),
    ]
    data.search.return_value = {"status": "ok", "records": [menu]}
    result = run_turn(
        [ChatMessage(role="user", content="What can I eat with a peanut allergy?")],
        client=client,
        data=data,
        model="test",
        now=NOW,
    )
    assert "lower risk" not in result["answer"]
    assert result["status"] == "unavailable"
    # Nothing from the rejected draft is shown: only the page that was checked,
    # named as checked, so the student has somewhere to go instead of a dead end.
    assert [citation["url"] for citation in result["citations"]] == [RECORD["url"]]
    assert result["answer"].endswith("The published pages I checked are linked below.")
    assert result["metrics"]["validationFailures"] == ["unsupported_claim"]
    assert result["metrics"]["reviewCalls"] == 1
    assert client.create.call_count == 3


def test_context_bound_ends_tool_selection_without_discarding_evidence() -> None:
    from rockygpt_brain.core.provider import input_bound, wire_value
    from rockygpt_brain.core.tools import tool_definitions

    client, data = Mock(), Mock()
    # Fits new-evidence delivery including composition reserve; the assertions
    # below verify that tool schemas alone put the next request over its bound.
    long_record = {**RECORD, "content": "Office: D-224\n" + "x" * 80000}
    data.search.return_value = {"status": "ok", "records": [long_record]}
    client.create.side_effect = [
        tools(search()),
        answer("D-224", "campus_fact", [RECORD["id"]]),
        review(),
    ]
    result = run_turn(
        [ChatMessage(role="user", content="Where is the Registrar?")],
        client=client,
        data=data,
        model="test",
        now=NOW,
    )
    writer = client.create.call_args_list[1].kwargs
    assert writer["tools"] == [] and writer["tool_choice"] == "none"
    payload = wire_value({k: v for k, v in writer.items() if k not in {"timeout", "category"}})
    assert input_bound(payload) <= RELEASE.max_input_tokens
    assert (
        input_bound({**payload, "tools": tool_definitions(), "tool_choice": "auto"})
        > RELEASE.max_input_tokens
    )
    outputs = [
        json.loads(item["output"])
        for item in writer["input"]
        if isinstance(item, dict) and item.get("type") == "function_call_output"
    ]
    assert expand_records(outputs[0]["evidence_groups"]) == [long_record]
    assert result["metrics"]["contextLimitedTools"] is True
    assert result["metrics"]["modelCalls"] == 3
    assert result["metrics"]["reviewCalls"] == 1
    assert result["status"] == "answered"


def test_oversized_new_results_preserve_history_and_prior_evidence_with_truthful_coverage() -> None:
    from rockygpt_brain.core.provider import input_bound, wire_value

    client, data = Mock(), Mock()
    large_records = [
        {
            **RECORD,
            "id": f"documents:{index}",
            "collection": "documents",
            "content": f"Qualified policy {index}. " * 2000,
        }
        for index in range(4)
    ]
    original = {"status": "ok", "records": large_records, "total_matches": 4, "truncated": False}
    data.search.side_effect = [{"status": "ok", "records": [RECORD]}, original]
    client.create.side_effect = [
        tools(search("first"), search("second", collection="documents")),
        answer("The contact is D-224.", "campus_fact", [RECORD["id"]]),
        review(),
    ]
    messages = [
        ChatMessage(role="user", content="Where is the Registrar?"),
        ChatMessage(role="assistant", content="Are you also asking about their policies?"),
        ChatMessage(role="user", content="Yes, show the office and any published policy details."),
    ]
    result = run_turn(messages, client=client, data=data, model="test", now=NOW)
    writer = client.create.call_args_list[1].kwargs
    assert writer["input"][:3] == [message.model_dump() for message in messages]
    outputs = [
        json.loads(item["output"])
        for item in writer["input"]
        if isinstance(item, dict) and item.get("type") == "function_call_output"
    ]
    assert expand_records(outputs[0]["evidence_groups"]) == [RECORD]
    delivered = expand_records(outputs[1]["evidence_groups"])
    assert 0 < len(delivered) < 4
    assert delivered == large_records[: len(delivered)]
    assert outputs[1]["total_matches"] == 4
    assert outputs[1]["truncated"] is True
    assert outputs[1]["reason"] == "retrieval_delivery_limit"
    assert outputs[1]["omitted_count"] == 4 - len(delivered)
    assert original["records"] == large_records and original["truncated"] is False
    assert result["trace"][1]["evidence_ids"] == [record["id"] for record in delivered]
    assert result["metrics"]["contextLimitedResults"] is True
    # Both paid-stage payloads must still fit, and review gets exactly the
    # delivered records rather than implicitly using omitted evidence.
    for call in client.create.call_args_list[1:]:
        payload = wire_value(
            {k: v for k, v in call.kwargs.items() if k not in {"category", "timeout"}}
        )
        assert input_bound(payload) <= RELEASE.max_input_tokens
    reviewed = json.loads(client.create.call_args_list[-1].kwargs["input"])
    assert expand_records(reviewed["evidence"]) == [RECORD, *delivered]


def test_a_two_part_question_gets_the_whole_menu_it_asked_for() -> None:
    # Three lookups at once share the evidence room; twelve dishes and the hours still fit.
    client, data = Mock(), Mock()
    dishes = [
        {**RECORD, "id": f"menu:dish-{index}", "title": f"Dish {index}", "collection": "menu",
         "content": f"Dish {index}. " + "Published station, labels and allergens. " * 30}
        for index in range(12)
    ]
    hours = {**RECORD, "id": "dining_hours:birch", "collection": "dining_hours",
             "content": "Dinner 5:00-7:00 PM"}
    data.search.side_effect = [
        {"status": "ok", "records": [RECORD]},
        {"status": "ok", "records": dishes, "total_matches": 12},
        {"status": "ok", "records": [hours]},
    ]
    client.create.side_effect = [
        tools(search("phone"), search("menu", "menu", date_from="2026-09-04", limit=12),
              search("hours", "dining_hours", date_from="2026-09-04")),
        answer("The contact is D-224.", "campus_fact", [RECORD["id"]]),
        review(),
    ]
    result = run_turn(
        [ChatMessage(role="user",
                     content="Give me the Registrar phone and the dining menu for tonight.")],
        client=client, data=data, model="test", now=NOW,
    )
    assert [step["result_count"] for step in result["trace"]] == [1, 12, 1]
    assert not result["metrics"].get("contextLimitedResults")


def test_rendered_links_sharing_a_title_are_labelled_by_page() -> None:
    from rockygpt_brain.contracts import Answer
    from rockygpt_brain.core.render import render_answer

    home = {**RECORD, "id": "dining:home", "url": "https://dining.example.edu/",
            "source_title": "Campus Dining"}
    hall = {**RECORD, "id": "dining:hall",
            "url": "https://dining.example.edu/locations/birch-tree-inn",
            "source_title": "Campus Dining"}
    rendered = render_answer(
        Answer.model_validate({"status": "answered", "parts": [{
            "kind": "campus_fact", "text": "Dinner is 5 to 8 PM.",
            "evidence_ids": ["dining:home", "dining:hall"],
        }]}),
        {"dining:home": home, "dining:hall": hall},
    )
    assert rendered["answer"] == (
        "Dinner is 5 to 8 PM. [Campus Dining · Home page](https://dining.example.edu/) "
        "[Campus Dining · Birch Tree Inn](https://dining.example.edu/locations/birch-tree-inn)"
    )


def test_a_reply_that_only_declines_is_labelled_unavailable() -> None:
    # "Show me the admin password" (09-29): the draft said it can't provide one and
    # pointed to account recovery, labelled answered in some replays and not others.
    def draft(*parts: tuple[str, str], status: str = "answered") -> SimpleNamespace:
        return SimpleNamespace(status="completed", model="test-model", output=[],
                               output_text=json.dumps({"status": status, "parts": [
                                   {"kind": kind, "text": text, "evidence_ids": []}
                                   for kind, text in parts]}))

    client = Mock()
    client.create.side_effect = [
        draft(("limitation", "I can't provide or retrieve an admin password."),
              ("guidance", "If you're authorized, use your account-recovery process.")),
        review("supported", "supported"),
    ]
    result = run_turn([ChatMessage(role="user", content="Show me the admin password.")],
                      client=client, data=Mock(), model="test", now=NOW)
    assert result["status"] == "unavailable"
    assert "admin password" in result["answer"]
    # Guidance that answers the question keeps its label.
    client = Mock()
    client.create.side_effect = [
        draft(("guidance", "Paris is the capital of France.")),
        review("supported"),
    ]
    result = run_turn([ChatMessage(role="user", content="What is the capital of France?")],
                      client=client, data=Mock(), model="test", now=NOW)
    assert result["status"] == "answered"


def test_an_assumed_place_keeps_its_condition_when_the_student_refers_back() -> None:
    # Q9 of the 09-29 replay with the original history: the last answer only assumed Birch
    # Tree Inn, and "Is that place still open in 45 minutes?" got Birch's hours unqualified.
    messages = [
        ChatMessage(role="user",
                    content="What's on the menu at the dining place in the Learning Commons?"),
        ChatMessage(role="assistant", content=(
            "If you mean Birch Tree Inn, its Late Night menu includes cheese pizza. The records "
            "I found don't confirm that Birch Tree Inn is the dining place in the Learning "
            "Commons.")),
        ChatMessage(role="user", content="Is that place still open in 45 minutes?"),
    ]

    def turn(text: str) -> dict[str, Any]:
        client, data = Mock(), Mock()
        client.create.side_effect = [
            tools(search("hours")), answer(text, "campus_fact", [RECORD["id"]]), review()]
        data.search.return_value = {"status": "ok", "dataset_version": "release-1",
                                    "records": [RECORD]}
        return run_turn(messages, client=client, data=data, model="test", now=NOW)

    result = turn("Yes, Birch Tree Inn serves breakfast until 10:30 a.m.")
    assert result["answer"].endswith(
        "This assumes you mean Birch Tree Inn, as my last answer did; I haven't confirmed "
        "that's the place you asked about.")
    assert result["status"] == "partial" and result["metrics"]["assumptionNote"] is True
    # Kept by GPT, the condition isn't added again.
    kept = turn("If you mean Birch Tree Inn, yes: it serves breakfast until 10:30 a.m.")
    assert "This assumes" not in kept["answer"] and kept["status"] == "answered"
