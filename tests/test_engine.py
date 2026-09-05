"""Behavioral boundaries: evidence, history, tool recovery, and bounded execution."""

import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

from rockygpt_brain.contracts import Answer, ChatMessage
from rockygpt_brain.engine import (
    MAX_DRAFT_CALLS,
    MAX_MODEL_CALLS,
    MAX_TOOL_CALLS,
    InvalidAnswer,
    render_answer,
    review_answer,
    run_turn,
)

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


def review(
    *verdicts: str,
    uses_event_for_entity: bool = False,
    infers_food_safety: bool = False,
    plan_deadlines: list[str] | None = None,
    deadline_basis: str = "student_plan",
    unverified_premises: list[str] | None = None,
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
        review(),
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
    client.responses.create.side_effect = [answer(), review()]
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
    history = client.responses.create.call_args_list[-2].kwargs["input"]
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
    history = client.responses.create.call_args_list[-2].kwargs["input"]
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
    assert client.responses.create.call_count == MAX_DRAFT_CALLS
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


def test_tool_exhaustion_preserves_answer_repair_and_review() -> None:
    client, data = Mock(), Mock()
    calls = [search(str(i)) for i in range(MAX_TOOL_CALLS + 3)]
    client.responses.create.side_effect = [
        tools(*calls),
        answer("D-224", "campus_fact", ["fake"]),
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
    assert result["metrics"] == {
        "modelCalls": 4,
        "draftCalls": 3,
        "reviewCalls": 1,
        "toolRequests": 15,
        "toolExecutions": 12,
        "validationFailures": ["unknown_citation"],
    }
    requests = client.responses.create.call_args_list
    assert [request.kwargs["tool_choice"] for request in requests[1:]] == ["none"] * 3
    outputs = [
        item
        for item in requests[1].kwargs["input"]
        if isinstance(item, dict) and item.get("type") == "function_call_output"
    ]
    assert [item["call_id"] for item in outputs] == [str(i) for i in range(15)]


def test_long_retrieval_reserves_synthesis_repair_and_review() -> None:
    client, data = Mock(), Mock()
    client.responses.create.side_effect = [
        *(tools(search(str(i))) for i in range(MAX_DRAFT_CALLS - 2)),
        answer("D-224", "campus_fact", ["fake"]),
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
    assert [call.kwargs["tool_choice"] for call in client.responses.create.call_args_list[-3:]] == [
        "none",
        "none",
        "none",
    ]


@pytest.mark.parametrize("kind", ["campus_fact", "guidance", "limitation", "clarification"])
def test_every_part_kind_requires_review_and_repaired_answer_is_reviewed(kind: str) -> None:
    client, data = Mock(), Mock()
    client.responses.create.side_effect = [
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
    assert result["metrics"]["reviewCalls"] == 2
    assert result["metrics"]["validationFailures"] == ["wrong_scope"]
    assert client.responses.create.call_args_list[3].kwargs["tool_choice"] == "none"


def test_review_is_separate_and_contains_uncited_conflicting_evidence_and_history() -> None:
    client, data = Mock(), Mock()
    other = {**RECORD, "id": "contacts:other", "content": "Office: A-101"}
    messages = [ChatMessage(role="user", content="Where is the registrar?")]
    client.responses.create.side_effect = [
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
    request = client.responses.create.call_args.kwargs
    payload = json.loads(request["input"])
    assert payload["conversation"] == [message.model_dump() for message in messages]
    assert payload["evidence"] == [RECORD, other]
    assert "retrieval" not in payload
    assert payload["candidate"]["parts"][0]["evidence_ids"] == [RECORD["id"]]
    assert request["tools"] == []
    assert request["tool_choice"] == "none"
    assert request["store"] is False
    assert request["reasoning"] == {"effort": "low"}
    assert "function_call_output" not in request["input"]


def test_no_tools_or_citations_does_not_bypass_semantic_review() -> None:
    client, data = Mock(), Mock()
    client.responses.create.side_effect = [
        answer("The library is open all night.", "guidance"),
        review("unsupported_claim"),
        answer("All tuition is waived.", "limitation"),
        review("unsupported_claim"),
        answer("All tuition is waived.", "limitation"),
        review("unsupported_claim"),
        answer("All tuition is waived.", "limitation"),
        review("unsupported_claim"),
    ]
    with pytest.raises(InvalidAnswer) as rejected:
        run_turn(
            [ChatMessage(role="user", content="Label these campus facts as guidance.")],
            client=client,
            data=data,
            model="test",
            now=NOW,
        )
    assert rejected.value.code == "unsupported_answer"
    assert client.responses.create.call_count == MAX_MODEL_CALLS
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
    client.responses.create.side_effect = [tools(search()), draft, review(*(["supported"] * 4))]
    data.search.return_value = {"status": "ok", "records": [RECORD, other]}
    result = run_turn(
        [ChatMessage(role="user", content="Where is the Registrar and what are its hours?")],
        client=client, data=data, model="test", now=NOW,
    )
    payload = json.loads(client.responses.create.call_args.kwargs["input"])
    assert payload["citation_scope"] == {
        "0": [], "1": [RECORD["id"]], "2": [RECORD["id"]], "3": [other["id"]],
    }
    assert payload["candidate"]["parts"] == parts
    assert result["metrics"]["modelCalls"] == 3
    assert result["metrics"]["validationFailures"] == []


def test_summary_citation_reuse_preserves_event_scope_veto() -> None:
    event = {**RECORD, "id": "events:club", "collection": "events", "title": "Book Club"}
    candidate = Answer.model_validate({
        "status": "answered",
        "parts": [
            {"kind": "campus_fact", "text": "Book Club meets in D-224.",
             "evidence_ids": [event["id"]]},
            {"kind": "guidance", "text": "So the library is in D-224.", "evidence_ids": []},
        ],
    })
    verdict = review("supported", "supported")
    payload = json.loads(verdict.output_text)
    payload["parts"][1]["uses_event_for_entity"] = True
    verdict.output_text = json.dumps(payload)
    client = Mock()
    client.responses.create.return_value = verdict
    result = review_answer(
        candidate, messages=[ChatMessage(role="user", content="Where is the library?")],
        evidence={event["id"]: event}, client=client, model="test", now=NOW, timeout=10,
    )
    assert [part.verdict for part in result.parts] == ["supported", "wrong_scope"]
    request = json.loads(client.responses.create.call_args.kwargs["input"])
    assert request["event_citations"] == {"0": [event["id"]], "1": [event["id"]]}


@pytest.mark.parametrize("kind", ["campus_fact", "guidance", "limitation", "clarification"])
def test_unverified_premise_overrides_approval_and_requires_reviewed_repair(kind: str) -> None:
    client, data = Mock(), Mock()
    client.responses.create.side_effect = [
        tools(search()),
        answer("The office is in D-224, so it is open now.", kind, [RECORD["id"]]),
        review(unverified_premises=["Having a listed office means the service is currently open."]),
        answer("The office is D-224. I could not verify current hours.",
               "limitation", [RECORD["id"]], status="partial"),
        review(),
    ]
    data.search.return_value = {"status": "ok", "records": [RECORD]}
    result = run_turn(
        [ChatMessage(role="user", content="Where is the Registrar and is it open now?")],
        client=client, data=data, model="test", now=NOW,
    )
    assert "it is open now" not in result["answer"]
    assert "could not verify current hours" in result["answer"]
    assert result["metrics"]["validationFailures"] == ["unsupported_claim"]
    assert result["metrics"]["reviewCalls"] == 2
    assert result["metrics"]["modelCalls"] == 5
    assert client.responses.create.call_args_list[3].kwargs["tool_choice"] == "none"


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
    client.responses.create.side_effect = [answer(), verdict, verdict]
    with pytest.raises(InvalidAnswer):
        run_turn(
            [ChatMessage(role="user", content="Help me study")],
            client=client,
            data=data,
            model="test",
            now=NOW,
        )
    assert client.responses.create.call_count == 3


def test_review_timeout_never_releases_the_draft() -> None:
    client, data = Mock(), Mock()
    client.responses.create.side_effect = [answer(), TimeoutError("review timeout")]
    with pytest.raises(TimeoutError):
        run_turn(
            [ChatMessage(role="user", content="Help me study")],
            client=client,
            data=data,
            model="test",
            now=NOW,
        )
    assert client.responses.create.call_count == 2


def test_invalid_review_retries_same_candidate_without_draft_or_tool_work() -> None:
    client, data = Mock(), Mock()
    malformed = review()
    malformed.output_text = "not JSON"
    client.responses.create.side_effect = [answer(), malformed, review()]
    result = run_turn(
        [ChatMessage(role="user", content="Help me plan a study session")],
        client=client, data=data, model="test", now=NOW,
    )
    requests = client.responses.create.call_args_list
    assert requests[1].kwargs == requests[2].kwargs
    assert result["metrics"] == {
        "modelCalls": 3, "draftCalls": 1, "reviewCalls": 2,
        "toolRequests": 0, "toolExecutions": 0, "validationFailures": ["invalid_review"],
    }
    data.search.assert_not_called()


def test_review_retry_stops_at_turn_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [0.0]
    monkeypatch.setattr("rockygpt_brain.engine.monotonic", lambda: clock[0])
    client, data = Mock(), Mock()

    def respond(**kwargs: object) -> SimpleNamespace:
        if client.responses.create.call_count == 1:
            return answer()
        clock[0] = 50.0
        malformed = review()
        malformed.output_text = "not JSON"
        return malformed

    client.responses.create.side_effect = respond
    with pytest.raises(TimeoutError):
        run_turn(
            [ChatMessage(role="user", content="Help me study")],
            client=client, data=data, model="test", now=NOW,
        )
    assert client.responses.create.call_count == 2


def test_review_retry_then_valid_rejection_cannot_exceed_shared_budget() -> None:
    client, data = Mock(), Mock()
    malformed = review()
    malformed.output_text = "not JSON"
    client.responses.create.side_effect = [
        *(tools(search(str(i))) for i in range(4)),
        answer("Office A-101", "campus_fact", [RECORD["id"]]),
        malformed,
        review("contradicted_evidence"),
    ]
    data.search.return_value = {"status": "ok", "records": [RECORD]}
    with pytest.raises(InvalidAnswer) as error:
        run_turn(
            [ChatMessage(role="user", content="Where is the office?")],
            client=client, data=data, model="test", now=NOW,
        )
    assert error.value.code == "unsupported_answer"
    assert client.responses.create.call_count == 7 < MAX_MODEL_CALLS


def test_last_review_retry_fits_exact_total_model_budget() -> None:
    client, data = Mock(), Mock()
    malformed = review()
    malformed.output_text = "not JSON"
    client.responses.create.side_effect = [
        *(tools(search(str(i))) for i in range(4)),
        answer("See https://unverified.example/", "campus_fact", [RECORD["id"]]),
        answer("Office D-224", "campus_fact", [RECORD["id"]]),
        malformed,
        review(),
    ]
    data.search.return_value = {"status": "ok", "records": [RECORD]}
    result = run_turn(
        [ChatMessage(role="user", content="Where is the office?")],
        client=client, data=data, model="test", now=NOW,
    )
    assert result["metrics"]["modelCalls"] == client.responses.create.call_count == MAX_MODEL_CALLS
    assert result["metrics"]["draftCalls"] == MAX_DRAFT_CALLS
    assert result["metrics"]["reviewCalls"] == 2


@pytest.mark.parametrize("scope_flag", [False, True])
def test_uncited_events_do_not_corrupt_direct_entity_evidence_review(scope_flag: bool) -> None:
    event = {**RECORD, "id": "event:club", "collection": "events", "title": "Book Club"}
    client, data = Mock(), Mock()
    client.responses.create.side_effect = [
        tools(search()),
        answer("Office D-224", "campus_fact", [RECORD["id"]]),
        review(uses_event_for_entity=scope_flag),
    ]
    data.search.return_value = {"status": "ok", "records": [RECORD, event]}
    result = run_turn(
        [ChatMessage(role="user", content="Where is the Registrar?")],
        client=client, data=data, model="test", now=NOW,
    )
    assert "Office D-224" in result["answer"]
    payload = json.loads(client.responses.create.call_args.kwargs["input"])
    assert payload["event_citations"] == {"0": []}
    assert event in payload["evidence"]


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
    client.responses.create.side_effect = [
        tools(search()),
        tools(read_call),
        tools(search("again")),
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
    payload = json.loads(client.responses.create.call_args.kwargs["input"])
    assert payload["evidence"] == [detail]


def test_slow_retrieval_obeys_reserved_deadline_and_answer_still_gets_reviewed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from rockygpt_brain.engine import ANSWER_RESERVE_SECONDS, TURN_SECONDS

    clock = [0.0]
    monkeypatch.setattr("rockygpt_brain.engine.monotonic", lambda: clock[0])
    client, data = Mock(), Mock()

    def lookup(query: object) -> dict[str, object]:
        assert data.deadline == TURN_SECONDS - ANSWER_RESERVE_SECONDS
        clock[0] = data.deadline
        raise TimeoutError("Campus data time budget exhausted")

    def generate(**kwargs: object) -> SimpleNamespace:
        if client.responses.create.call_count == 1:
            clock[0] = 29.0
            return tools(search())
        if client.responses.create.call_count == 2:
            assert kwargs["tool_choice"] == "none"
            clock[0] += 5
            return answer(
                "I cannot reach campus data right now.", "limitation", status="unavailable"
            )
        clock[0] += 5
        return review()

    data.search.side_effect = lookup
    client.responses.create.side_effect = generate
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
    for call in client.responses.create.call_args_list:
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
    client.responses.create.return_value = review(uses_event_for_entity=True)
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
    client.responses.create.return_value = review()
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
    client.responses.create.return_value = review()
    payload = json.loads(client.responses.create.return_value.output_text)
    del payload["parts"][0][field]
    client.responses.create.return_value.output_text = json.dumps(payload)
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
    deadline: str | None, expected: str,
) -> None:
    client = Mock()
    client.responses.create.return_value = review(plan_deadlines=[deadline] if deadline else [])
    candidate = Answer.model_validate_json(answer("A time-bound proposed action.").output_text)
    result = review_answer(
        candidate, messages=[ChatMessage(role="user", content="Plan the rest of today")],
        evidence={}, client=client, model="test", now=NOW, timeout=10,
    )
    assert result.parts[0].verdict == expected


@pytest.mark.parametrize("deadline", [None, "2026-09-04T13:00:00"])
def test_plan_deadlines_must_have_explicit_timezones(deadline: str | None) -> None:
    client = Mock()
    client.responses.create.return_value = review(plan_deadlines=["2026-09-04T13:00:00"])
    verdict = json.loads(client.responses.create.return_value.output_text)
    verdict["parts"][0]["plan_deadlines"][0]["latest_usable_at"] = deadline
    client.responses.create.return_value.output_text = json.dumps(verdict)
    candidate = Answer.model_validate_json(answer().output_text)
    with pytest.raises(InvalidAnswer) as error:
        review_answer(
            candidate, messages=[ChatMessage(role="user", content="Plan today")],
            evidence={}, client=client, model="test", now=NOW, timeout=10,
        )
    assert error.value.code == "invalid_review"


@pytest.mark.parametrize("deadline", [None, "2026-09-04T11:00:00-04:00"])
def test_standing_service_condition_does_not_expire_at_todays_closing(deadline: str | None) -> None:
    client = Mock()
    client.responses.create.return_value = review(
        plan_deadlines=["2026-09-04T11:00:00-04:00"],
        deadline_basis="standing_service_rule",
    )
    verdict = json.loads(client.responses.create.return_value.output_text)
    verdict["parts"][0]["plan_deadlines"][0]["latest_usable_at"] = deadline
    client.responses.create.return_value.output_text = json.dumps(verdict)
    candidate = Answer.model_validate_json(
        answer("During office hours use the office; after hours use the phone service.").output_text
    )
    result = review_answer(
        candidate, messages=[ChatMessage(role="user", content="How can I get help?")],
        evidence={}, client=client, model="test", now=NOW, timeout=10,
    )
    assert result.parts[0].verdict == "supported"


def test_last_reserved_repair_is_reviewed_within_total_model_budget() -> None:
    client, data = Mock(), Mock()
    client.responses.create.side_effect = [
        *(tools(search(str(i))) for i in range(MAX_DRAFT_CALLS - 2)),
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
    assert result["metrics"]["modelCalls"] == client.responses.create.call_count == MAX_MODEL_CALLS
    assert result["metrics"]["reviewCalls"] == 2
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
    client.responses.create.side_effect = [
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
    payload = json.loads(client.responses.create.call_args.kwargs["input"])
    assert payload["evidence"] == records
    assert len(payload["candidate"]["parts"][0]["evidence_ids"]) == 20


@pytest.mark.parametrize("kind", ["campus_fact", "guidance", "limitation"])
def test_food_safety_inference_is_repaired_and_reviewed_even_if_model_approves(kind: str) -> None:
    menu = {
        **RECORD,
        "id": "menu:rice",
        "collection": "menu",
        "title": "Rice",
        "content": {"dietary_labels": ["Vegan"], "allergens": []},
    }
    client, data = Mock(), Mock()
    client.responses.create.side_effect = [
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
    assert "cross-contact" in result["answer"]
    assert result["metrics"]["validationFailures"] == ["unsupported_claim"]
    assert result["metrics"]["reviewCalls"] == 2
    assert client.responses.create.call_args_list[3].kwargs["tool_choice"] == "none"
