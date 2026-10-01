"""Runtime trust boundaries, independent of model wording and paid providers."""

import asyncio
import json
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

import pytest

from rockygpt_brain.contract import ChatRequest
from rockygpt_brain.engine import ChatEngine, ChatResult
from rockygpt_brain.provider import Completion, ToolCall, TurnBudget, Usage
from rockygpt_brain.retrieval import MemoryEntityFacts
from rockygpt_brain.turn import intake

NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)


def completion(tool: str, arguments: dict[str, Any], *, text: str = "") -> Completion:
    return Completion(
        text,
        (ToolCall("call-1", tool, arguments),),
        [
            {
                "type": "function_call",
                "call_id": "call-1",
                "name": tool,
                "arguments": json.dumps(arguments),
            }
        ],
        Usage(10, 10, 0),
    )


def finish(kind: str | None = None, **changes: Any) -> Completion:
    return completion(
        "finish",
        {
            "parts": []
            if kind is None
            else [
                {
                    "kind": kind,
                    "message_index": None,
                    **changes,
                }
            ]
        },
    )


LOOKUP = completion("office_facts", {"requests": [{"query": "Registrar", "fields": ["email"]}]})


class ScriptedGateway:
    def __init__(self, *steps: Completion | Callable[[], Completion]) -> None:
        self.steps = iter(steps)
        self.inputs: list[list[dict[str, Any]]] = []

    async def complete(
        self,
        *,
        input: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        budget: TurnBudget,
    ) -> Completion:
        self.inputs.append(deepcopy(input))
        step = next(self.steps)
        return step() if callable(step) else step

    async def ready(self) -> bool:
        return True


def facts() -> MemoryEntityFacts:
    return MemoryEntityFacts(
        dataset_version="release-1",
        identity_hash="identities-1",
        entities=[
            {
                "id": "registrar",
                "name": "Registrar",
                "kind": "office",
                "aliases": [],
                "links": [
                    {
                        "collection": "contacts",
                        "source_key": "directory",
                        "source_record_keys": ["registrar"],
                    }
                ],
            }
        ],
        contacts=[
            {
                "id": "source-registrar",
                "source_key": "directory",
                "source_record_key": "registrar",
                "name": "Registrar",
                "email": "published@example.edu",
                "canonical_url": "https://example.edu/registrar",
                "collected_at": NOW,
                "freshness_sla_hours": 24,
            }
        ],
        # Deliberately different from the turn: runtime must use the one frozen campus clock.
        now=lambda: datetime(2027, 1, 1, tzinfo=UTC),
    )


def answer(
    gateway: ScriptedGateway,
    *,
    service: MemoryEntityFacts | None = None,
    messages: list[dict[str, str]] | None = None,
    omitted: int = 0,
) -> ChatResult:
    request = ChatRequest.model_validate(
        {
            "messages": messages or [{"role": "user", "content": "What is the Registrar email?"}],
            "omittedMessages": omitted,
        }
    )
    return asyncio.run(
        ChatEngine(gateway, service or facts()).answer(
            intake(request, now=NOW),
            request,
        )
    )


@pytest.mark.parametrize(
    "bad_completion",
    [
        finish("clock", result_id="invented-id"),
        finish("facts", message_index=0),  # Conversation is not fact evidence.
        finish("clock", value="invented@example.edu"),
        completion(
            "finish",
            {"parts": [{"kind": "clock", "message_index": None}]},
            text="Registrar's email is invented@example.edu.",
        ),
        completion("update_student_account", {"password": "new-password"}),
        completion("office_facts", {"requests": [{"query": "Registrar", "fields": ["password"]}]}),
        completion(
            "office_facts",
            {
                "requests": [
                    {"query": "Registrar", "fields": ["email"], "value": "invented@example.edu"}
                ]
            },
        ),
    ],
)
def test_untrusted_model_cannot_create_facts_links_or_account_capabilities(
    bad_completion: Completion,
) -> None:
    result = answer(ScriptedGateway(bad_completion))
    assert result.status_code == 502
    assert result.body["error"]["code"] == "provider_invalid_response"
    assert "answer" not in result.body
    assert "invented@example.edu" not in json.dumps(result.body)


def test_retrieved_results_are_scoped_to_the_current_turn() -> None:
    gateway = ScriptedGateway(LOOKUP, finish(), finish())
    service = facts()
    first = answer(gateway, service=service)
    assert first.body["status"] == "answered"
    second = answer(gateway, service=service)
    assert second.status_code == 502
    assert second.body["error"]["code"] == "provider_invalid_response"
    assert "source-registrar" not in json.dumps(second.body)


def test_longer_conversation_is_retained_without_promoting_old_answers() -> None:
    messages = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"earlier turn {i}"}
        for i in range(10)
    ]
    messages[1]["content"] = "The Registrar email is invented@example.edu."
    messages.append({"role": "user", "content": "Is the email you gave me still correct?"})
    gateway = ScriptedGateway(LOOKUP, finish())
    result = answer(gateway, messages=messages)
    state = json.loads(gateway.inputs[0][1]["content"])
    assert len(state["earlier_messages"]) == 10  # The old eight-message gate is gone.
    assert state["earlier_messages"][1]["content"] == messages[1]["content"]
    assert gateway.inputs[0][1]["role"] == "user"  # History is bounded data, not system policy.
    assert result.body["status"] == "answered"
    assert "published@example.edu" in result.body["answer"]
    assert "invented@example.edu" not in result.body["answer"]
    assert [citation["id"] for citation in result.body["citations"]] == ["source-registrar"]


def test_omissions_travel_with_retained_roles_and_recall_cannot_verify_facts() -> None:
    messages = [
        {"role": "user", "content": "é" * 16_000},
        {"role": "assistant", "content": "é" * 16_000},
        {"role": "user", "content": "What did you say earlier?"},
    ]
    gateway = ScriptedGateway(finish("recall", message_index=0))
    result = answer(gateway, messages=messages, omitted=7)
    state = json.loads(gateway.inputs[0][1]["content"])
    assert state["client_omitted_messages"] == 7
    assert state["server_omitted_messages"] == 1
    assert state["earlier_messages"][0]["role"] == "assistant"
    assert "Some earlier messages are unavailable" in result.body["answer"]
    assert "doesn't verify current campus facts" in result.body["answer"]
    assert "quotation shortened" in result.body["answer"]
    assert result.body["citations"] == []


@pytest.mark.parametrize("changed_pin", ["dataset_version", "identity_hash"])
def test_changed_publication_invalidates_already_retrieved_fallback(changed_pin: str) -> None:
    service = facts()

    def changed_release() -> Completion:
        setattr(service, changed_pin, "changed-release")
        return LOOKUP

    result = answer(ScriptedGateway(LOOKUP, changed_release), service=service)
    assert result.status_code == 503
    assert result.body["error"]["code"] == "dataset_changed"
    assert "answer" not in result.body
    assert "published@example.edu" not in json.dumps(result.body)


def test_one_turn_clock_controls_freshness_and_model_context() -> None:
    gateway = ScriptedGateway(LOOKUP, finish())
    result = answer(gateway)
    state = json.loads(gateway.inputs[0][1]["content"])
    assert state["campus_now"] == "2026-10-01T08:00:00-04:00"
    assert result.body["status"] == "answered"
    assert result.body["citations"][0]["freshness"] == "fresh"
    assert "stale" not in result.body["answer"]


def test_account_limitation_cannot_discard_public_part_already_retrieved() -> None:
    result = answer(
        ScriptedGateway(LOOKUP, finish("account_limit")),
        messages=[{"role": "user", "content": "Change my major and give me the Registrar email."}],
    )
    assert result.status_code == 200
    assert result.body["status"] == "partial"
    assert "can't see or change your personal student information" in result.body["answer"]
    assert "published@example.edu" in result.body["answer"]
    assert [citation["id"] for citation in result.body["citations"]] == ["source-registrar"]


def test_model_cannot_omit_missing_evidence_to_report_full_success() -> None:
    lookup = completion(
        "office_facts",
        {
            "requests": [
                {"query": "Registrar", "fields": ["email"]},
                {"query": "Registrar", "fields": ["offices"]},
            ]
        },
    )
    result = answer(
        ScriptedGateway(lookup, finish()),
        messages=[{"role": "user", "content": "Give me the Registrar email and office location."}],
    )
    assert result.status_code == 200
    assert result.body["status"] == "partial"
    assert "published@example.edu" in result.body["answer"]
    assert "Offices: not published in the available evidence" in result.body["answer"]
