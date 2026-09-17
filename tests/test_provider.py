"""Paid boundaries exercised with fake responses, never API credits."""

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

from rockygpt_brain.accounting import PaidCallError
from rockygpt_brain.config import RELEASE, ConfigurationError, configuration_hash, load_deployment
from rockygpt_brain.provider import (
    ModelResponse,
    OpenAIProvider,
    OutputItem,
    PaidGateway,
    Usage,
    input_bound,
    normalize_usage,
)

NOW = datetime(2026, 9, 11, 12, tzinfo=ZoneInfo("America/New_York"))


def arguments() -> dict[str, Any]:
    return {
        "model": RELEASE.model,
        "input": [{"role": "user", "content": "Hello"}],
        "instructions": "Answer",
        "tools": [],
        "text": {},
        "max_output_tokens": RELEASE.draft_output_tokens,
        "store": False,
        "timeout": 2.0,
    }


DEFAULT_USAGE = Usage(100, 20, 40, 30)


def response(usage: Usage | None = DEFAULT_USAGE) -> ModelResponse:
    return ModelResponse("response-1", "gpt-5.4-2026-03-05", "completed", "answer", [], usage)


def test_reservation_precedes_execution_and_settlement_releases_only_unused_amount() -> None:
    provider, ledger = Mock(), Mock()
    order = []
    ledger.reserve.side_effect = lambda *args: order.append("reserve")

    def execute(**kwargs: Any) -> ModelResponse:
        order.append("execute")
        return response()

    provider.create.side_effect = execute
    ledger.settle.side_effect = lambda *args: order.append("settle")
    gateway = PaidGateway(provider, ledger, "turn", clock=lambda: NOW)
    assert gateway.create(category="draft", **arguments()).id == "response-1"
    assert order == ["reserve", "execute", "settle"]
    reservation = ledger.reserve.call_args.args
    assert reservation[3] > ledger.settle.call_args.args[1] > 0
    assert reservation[4]["configuration_hash"] == configuration_hash()
    assert "Hello" not in json.dumps(reservation[4])
    assert provider.create.call_args.kwargs["reasoning"] == {"effort": RELEASE.draft_reasoning}
    assert provider.create.call_args.kwargs["truncation"] == "disabled"
    assert gateway.usage.report()["usageComplete"] is True
    assert gateway.usage.report()["reasoningTokens"] == 30


def test_cached_and_reasoning_tokens_are_not_double_charged() -> None:
    assert Usage(100, 20, 40, 30).cost(RELEASE.price) == 805000
    assert asdict(Usage(0, 0, 0, 0)) == {
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
    }


@pytest.mark.parametrize(
    "tokens", [(-1, 0, 0, 0), (1, 2, 0, 0), (1, 0, 2, 3), (True, 0, 0, 0), (1.2, 0, 0, 0)]
)
def test_invalid_usage_is_never_free(tokens: tuple[int, int, int, int]) -> None:
    with pytest.raises(ValueError):
        Usage(*tokens)
    assert normalize_usage(None) is None


@pytest.mark.parametrize(
    "code",
    [
        "budget_exhausted",
        "accounting_unavailable",
        "operation_already_admitted",
        "accounting_paused",
    ],
)
def test_failed_admission_never_calls_provider(code: str) -> None:
    provider, ledger = Mock(), Mock()
    ledger.reserve.side_effect = PaidCallError(code)
    gateway = PaidGateway(provider, ledger, "turn", clock=lambda: NOW)
    with pytest.raises(PaidCallError, match=code):
        gateway.create(category="draft", **arguments())
    provider.create.assert_not_called()
    assert gateway.usage.report()["modelCalls"] == 0


@pytest.mark.parametrize("result", [None, TimeoutError("secret"), RuntimeError("secret")])
def test_missing_usage_and_ambiguous_failures_keep_reservation(
    result: BaseException | None,
) -> None:
    provider, ledger = Mock(), Mock()
    if result is None:
        provider.create.return_value = response(None)
    else:
        provider.create.side_effect = result
    gateway = PaidGateway(provider, ledger, "turn", clock=lambda: NOW)
    with pytest.raises(PaidCallError) as failure:
        gateway.create(category="draft", **arguments())
    assert "secret" not in str(failure.value)
    ledger.settle.assert_not_called()
    ledger.uncertain.assert_called_once()
    assert gateway.usage.report()["unsettledNusd"] > 0
    assert gateway.usage.report()["usageComplete"] is False


def test_settlement_outage_preserves_original_hold() -> None:
    provider, ledger = Mock(), Mock()
    provider.create.return_value = response()
    ledger.settle.side_effect = PaidCallError("accounting_unavailable")
    ledger.uncertain.side_effect = PaidCallError("accounting_unavailable")
    gateway = PaidGateway(provider, ledger, "turn", clock=lambda: NOW)
    with pytest.raises(PaidCallError, match="accounting_unavailable"):
        gateway.create(category="draft", **arguments())
    assert gateway.usage.report()["unsettledNusd"] > 0


def test_incomplete_responses_are_charged_before_engine_validation() -> None:
    provider, ledger = Mock(), Mock()
    provider.create.return_value = response()
    provider.create.return_value.status = "incomplete"
    gateway = PaidGateway(provider, ledger, "turn", clock=lambda: NOW)
    assert gateway.create(category="draft", **arguments()).status == "incomplete"
    ledger.settle.assert_called_once()


@pytest.mark.parametrize(
    "change",
    [
        {"model": "unpriced-model"},
        {"max_output_tokens": 9000},
        {"store": True},
        {"timeout": None},
        {"timeout": float("inf")},
        {"previous_response_id": "hidden"},
        {"tools": [{"type": "web_search"}]},
        {"input": [{"type": "input_image", "image_url": "hidden"}]},
        {"input": "文" * 100000},
    ],
)
def test_unbounded_or_unpriced_work_is_rejected_before_reservation(change: dict[str, Any]) -> None:
    provider, ledger = Mock(), Mock()
    gateway = PaidGateway(provider, ledger, "turn", clock=lambda: NOW)
    with pytest.raises(PaidCallError):
        gateway.create(category="draft", **{**arguments(), **change})
    provider.create.assert_not_called()
    ledger.reserve.assert_not_called()


def test_prices_expire_closed_and_all_calls_share_a_cap() -> None:
    provider, ledger = Mock(), Mock()
    provider.create.return_value = response()
    gateway = PaidGateway(provider, ledger, "turn", clock=lambda: NOW.replace(month=11))
    with pytest.raises(PaidCallError, match="price_unavailable"):
        gateway.create(category="draft", **arguments())
    gateway.clock = lambda: NOW
    for _ in range(RELEASE.max_draft_calls):
        gateway.create(category="draft", **arguments())
    gateway.create(
        category="review",
        **{**arguments(), "max_output_tokens": RELEASE.review_output_tokens},
    )
    with pytest.raises(PaidCallError, match="model_call_limit"):
        gateway.create(category="draft", **arguments())
    assert provider.create.call_count == RELEASE.max_model_calls


def test_tools_and_continuation_are_metered_and_preserved() -> None:
    provider, ledger = Mock(), Mock()
    provider.create.return_value = response()
    gateway = PaidGateway(provider, ledger, "turn", clock=lambda: NOW)
    item = OutputItem(
        {
            "type": "function_call",
            "call_id": "c1",
            "name": "read_campus",
            "arguments": "{}",
            "id": "fc1",
        }
    )
    args = arguments()
    args["input"].append(item)
    args["tools"] = [{"type": "function", "name": "read_campus", "parameters": {}}]
    gateway.create(category="draft", **args)
    assert provider.create.call_args.kwargs["input"][-1] == item.payload
    assert input_bound({"input": "文"}) > input_bound({"input": "a"})


def test_sdk_usage_is_normalized_and_output_identity_is_retained() -> None:
    client = Mock()
    raw = SimpleNamespace(
        input_tokens=100,
        output_tokens=40,
        input_tokens_details=SimpleNamespace(cached_tokens=20),
        output_tokens_details=SimpleNamespace(reasoning_tokens=30),
    )
    item = Mock()
    item.model_dump.return_value = {"type": "reasoning", "id": "r1", "summary": []}
    client.responses.create.return_value = SimpleNamespace(
        id="response-1",
        model="gpt-5.4",
        status="completed",
        output_text="Hi",
        output=[item],
        usage=raw,
    )
    result = OpenAIProvider(client).create(model="gpt-5.4")
    assert result.usage == Usage(100, 20, 40, 30)
    assert result.output[0].model_dump() == item.model_dump.return_value


def test_environment_credentials_cannot_silently_fall_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "legacy-secret")
    monkeypatch.delenv("BRAIN_ENVIRONMENT", raising=False)
    with pytest.raises(ConfigurationError):
        load_deployment()
    for key, value in {
        "BRAIN_ENVIRONMENT": "development",
        "BRAIN_OPENAI_API_KEY": "dev-secret",
        "BRAIN_OPENAI_PROJECT": "dev-project",
        "BRAIN_LEDGER_DATABASE_URL": "dev-db",
        "OPENAI_CHAT_MODEL": "gpt-5.4",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("BRAIN_EXPECTED_CONFIG_HASH", raising=False)
    deployment = load_deployment()
    assert deployment.api_key == "dev-secret"
    assert "dev-secret" not in repr(deployment)
    monkeypatch.setenv("BRAIN_EXPECTED_CONFIG_HASH", "incorrect")
    with pytest.raises(ConfigurationError):
        load_deployment()


def test_sdk_calls_exist_only_in_the_paid_adapter() -> None:
    root = Path(__file__).parents[1] / "src" / "rockygpt_brain"
    for path in root.rglob("*.py"):
        if path.name != "provider.py":
            assert "from openai" not in path.read_text()
            assert ".responses.create(" not in path.read_text()


def test_changed_provider_model_is_billed_then_blocks_further_paid_work() -> None:
    provider, ledger = Mock(), Mock()
    provider.create.return_value = response()
    provider.create.return_value.model = "gpt-5.4-new-unreviewed-snapshot"
    gateway = PaidGateway(provider, ledger, "turn", clock=lambda: NOW)
    with pytest.raises(PaidCallError, match="model_identity_changed"):
        gateway.create(category="draft", **arguments())
    ledger.settle.assert_called_once()
    ledger.pause.assert_called_once()
    assert gateway.usage.report()["costNusd"] > 0
    assert gateway.usage.report()["unsettledNusd"] == 0


@pytest.mark.parametrize(
    ("phases", "statuses", "expected"),
    [
        (["commentary", "final_answer"], ["completed", "completed"], '{"answer":1}'),
        ([None], ["completed"], '{"answer":0}'),
        (["commentary"], ["completed"], ""),
        (["commentary", "final_answer"], ["completed", "incomplete"], ""),
        (["final_answer", "final_answer"], ["completed", "completed"], ""),
        ([None, None], ["completed", "completed"], ""),
    ],
)
def test_only_one_completed_final_message_becomes_structured_answer(
    phases: list[str | None],
    statuses: list[str],
    expected: str,
) -> None:
    from openai.types.responses.response_output_message import ResponseOutputMessage

    payloads = [
        {
            "id": str(index),
            "type": "message",
            "role": "assistant",
            "phase": phase,
            "status": status,
            "content": [
                {"type": "output_text", "text": '{"answer":' + str(index) + "}", "annotations": []}
            ],
        }
        for index, (phase, status) in enumerate(zip(phases, statuses, strict=True))
    ]
    client = Mock()
    client.responses.create.return_value = SimpleNamespace(
        id="response-phases",
        model="gpt-5.4",
        status="completed",
        usage=None,
        output=[ResponseOutputMessage.model_validate(item) for item in payloads],
    )
    result = OpenAIProvider(client).create(model="gpt-5.4")
    assert result.output_text == expected
    assert [item.payload.get("phase") for item in result.output] == phases
    assert [item.payload["status"] for item in result.output] == statuses


def test_identical_completed_final_messages_are_one_candidate() -> None:
    from openai.types.responses.response_output_message import ResponseOutputMessage

    client = Mock()
    payloads = [
        {
            "id": f"message-{index}",
            "type": "message",
            "role": "assistant",
            "phase": "final_answer",
            "status": "completed",
            "content": [
                {"type": "output_text", "text": '{"answer":"candidate"}', "annotations": []}
            ],
        }
        for index in range(2)
    ]
    client.responses.create.return_value = SimpleNamespace(
        id="duplicate-final",
        model="gpt-5.4",
        status="completed",
        usage=None,
        output=[ResponseOutputMessage.model_validate(item) for item in payloads],
    )
    result = OpenAIProvider(client).create(model="gpt-5.4")
    assert result.output_text == '{"answer":"candidate"}'
    assert len(result.output) == 2  # Preserve the provider's replay items unchanged.


def test_gateway_enforces_initial_and_continuation_effort_and_records_both() -> None:
    provider, ledger = Mock(), Mock()
    provider.create.side_effect = [response(), response()]
    gateway = PaidGateway(provider, ledger, "effort-sequence", clock=lambda: NOW)
    for _ in range(2):
        gateway.create(category="draft", **{**arguments(), "reasoning": {"effort": "high"}})
    assert [call.kwargs["reasoning"] for call in provider.create.call_args_list] == [
        {"effort": RELEASE.draft_reasoning},
        {"effort": RELEASE.continuation_reasoning},
    ]
    assert [call.args[4]["reasoning_effort"] for call in ledger.reserve.call_args_list] == [
        RELEASE.draft_reasoning,
        RELEASE.continuation_reasoning,
    ]
    assert ledger.settle.call_count == 2
