"""Billing invariants are verified with injected transports; no provider calls."""

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from openai import APIStatusError, APITimeoutError

from rockygpt_brain.provider import Gateway, GatewayError, NotSentError, TurnBudget
from rockygpt_brain.settings import Prices, ProviderSettings
from rockygpt_brain.spending import Reservation, SpendingError

NOW = datetime(2026, 10, 1, tzinfo=UTC)
SETTINGS = ProviderSettings(
    "development", "test-secret", "project", "unused",
    Prices("test-model", 125, 500, 10, datetime(2026, 11, 1, tzinfo=UTC)),
)


class FakeLedger:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.metadata: dict[str, Any] = {}
        self.amount = 0
        self.cost: int | None = None
        self.fail_reserve = False
        self.fail_settle = False

    async def ready(self) -> bool:
        return True

    async def reserve(
        self, request_id: str, amount_nusd: int, metadata: dict[str, Any], now: datetime,
    ) -> Reservation:
        self.events.append("reserve")
        if self.fail_reserve:
            raise SpendingError("budget_exhausted")
        self.amount, self.metadata = amount_nusd, metadata
        return Reservation("operation", amount_nusd)

    async def settle(
        self, reservation: Reservation, cost_nusd: int, usage: dict[str, int],
        response_id: str, returned_model: str, now: datetime,
    ) -> None:
        self.events.append("settle")
        if self.fail_settle:
            raise SpendingError("ledger_unavailable")
        self.cost = cost_nusd

    async def uncertain(self, reservation: Reservation, code: str) -> None:
        self.events.append("uncertain")

    async def release(self, reservation: Reservation, code: str, now: datetime) -> None:
        self.events.append("release")
        self.cost = 0

    async def pause(self) -> None:
        self.events.append("pause")


class FakeTransport:
    def __init__(self, ledger: FakeLedger, response: dict[str, Any]) -> None:
        self.ledger, self.response = ledger, response
        self.error: BaseException | None = None
        self.request: dict[str, Any] = {}

    async def send(self, request: dict[str, Any], remaining_seconds: float) -> dict[str, Any]:
        self.ledger.events.append("send")
        self.request = request
        if self.error:
            raise self.error
        return self.response

    async def close(self) -> None:
        pass


def response() -> dict[str, Any]:
    return {
        "id": "response", "model": "test-model", "status": "completed",
        "usage": {"input_tokens": 100, "output_tokens": 20,
                  "input_tokens_details": {"cached_tokens": 25}},
        "output": [{"type": "function_call", "call_id": "call", "name": "finish",
                    "arguments": '{"parts": []}'}],
    }


def setup(
    result: dict[str, Any] | None = None, settings: ProviderSettings = SETTINGS,
) -> tuple[Gateway, FakeLedger, FakeTransport, TurnBudget]:
    ledger = FakeLedger()
    transport = FakeTransport(ledger, response() if result is None else result)
    gateway = Gateway(settings, ledger=ledger, transport=transport, now=lambda: NOW,
                      monotonic=lambda: 1.0)
    return gateway, ledger, transport, TurnBudget("request", 10.0)


def call(gateway: Gateway, budget: TurnBudget) -> Any:
    return asyncio.run(gateway.complete(
        input=[{"role": "user", "content": "private user text"}],
        tools=[{"type": "function", "name": "finish"}], budget=budget,
    ))


def test_reserves_before_send_and_settles_before_returning_typed_tool_call() -> None:
    gateway, ledger, transport, budget = setup()
    result = call(gateway, budget)
    assert ledger.events == ["reserve", "send", "settle"]
    assert result.tool_calls[0].arguments == {"parts": []}
    assert result.output == response()["output"]
    assert ledger.cost == 75 * 125 + 25 * 10 + 20 * 500
    assert budget.committed_nusd == ledger.cost
    assert "private user text" not in json.dumps(ledger.metadata)
    assert "test-secret" not in json.dumps(ledger.metadata)
    assert transport.request["store"] is False
    assert transport.request["parallel_tool_calls"] is False
    assert transport.request["tool_choice"] == "required"
    assert transport.request["service_tier"] == "default"


@pytest.mark.parametrize("invalid", [None, {}, {"input_tokens": -1, "output_tokens": 0},
                                     {"input_tokens": True, "output_tokens": 0}])
def test_invalid_usage_keeps_full_reservation(invalid: Any) -> None:
    result = response()
    result["usage"] = invalid
    gateway, ledger, _, budget = setup(result)
    with pytest.raises(GatewayError, match="provider_invalid_response"):
        call(gateway, budget)
    assert ledger.events == ["reserve", "send", "uncertain"]
    assert budget.committed_nusd == ledger.amount
    assert ledger.cost is None


def test_malformed_output_is_still_paid() -> None:
    result = response()
    result["output"][0]["arguments"] = "[not-json"
    gateway, ledger, _, budget = setup(result)
    with pytest.raises(GatewayError, match="provider_invalid_response"):
        call(gateway, budget)
    assert ledger.events == ["reserve", "send", "settle"]
    assert ledger.cost is not None


@pytest.mark.parametrize("error", [TimeoutError("secret user text"),
                                   RuntimeError("secret API key")])
def test_uncertain_failures_retain_reservations_without_leaking_errors(error: Exception) -> None:
    gateway, ledger, transport, budget = setup()
    transport.error = error
    with pytest.raises(GatewayError) as caught:
        call(gateway, budget)
    expected = "model_timeout" if isinstance(error, TimeoutError) else "provider_unavailable"
    assert str(caught.value) == expected
    assert caught.value.retryable
    assert ledger.events == ["reserve", "send", "uncertain"]
    assert budget.committed_nusd == ledger.amount


def test_only_definite_unsent_failure_can_release_reservation() -> None:
    gateway, ledger, transport, budget = setup()
    transport.error = NotSentError()
    with pytest.raises(GatewayError):
        call(gateway, budget)
    assert ledger.events == ["reserve", "send", "release"]
    assert budget.committed_nusd == 0


def test_cancellation_retains_reservation() -> None:
    gateway, ledger, transport, budget = setup()
    transport.error = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        call(gateway, budget)
    assert ledger.events == ["reserve", "send"]
    assert budget.committed_nusd == ledger.amount


@pytest.mark.parametrize("condition,code", [
    ("budget", "budget_exhausted"), ("calls", "turn_limit"),
    ("money", "turn_budget_exhausted"), ("deadline", "deadline_exceeded"),
    ("context", "context_limit"), ("expired", "model_not_configured"),
])
def test_fail_closed_before_provider(condition: str, code: str) -> None:
    settings = SETTINGS
    if condition == "context":
        settings = replace(SETTINGS, max_input_bytes=1)
    if condition == "expired":
        settings = replace(SETTINGS, prices=replace(SETTINGS.prices, valid_until=NOW))
    gateway, ledger, _, budget = setup(settings=settings)
    if condition == "budget":
        ledger.fail_reserve = True
    if condition == "calls":
        budget.calls = budget.max_calls
    if condition == "money":
        budget.max_cost_nusd = 1
    if condition == "deadline":
        budget.deadline = 1.0
    with pytest.raises(GatewayError, match=code):
        call(gateway, budget)
    assert "send" not in ledger.events


def test_failed_settlement_prevents_return_and_holds_conservative_spend() -> None:
    gateway, ledger, _, budget = setup()
    ledger.fail_settle = True
    with pytest.raises(GatewayError, match="ledger_unavailable"):
        call(gateway, budget)
    assert budget.committed_nusd == ledger.amount
    assert ledger.cost is None


def test_usage_overrun_pauses_account() -> None:
    result = response()
    result["usage"]["output_tokens"] = 100000
    gateway, ledger, _, budget = setup(result)
    with pytest.raises(GatewayError, match="provider_usage_exceeded"):
        call(gateway, budget)
    assert ledger.events[-2:] == ["settle", "pause"]
    assert budget.committed_nusd > ledger.amount


def test_calls_in_one_turn_accumulate_actual_cost_and_are_bounded() -> None:
    gateway, ledger, _, budget = setup()
    budget.max_calls = 2
    call(gateway, budget)
    call(gateway, budget)
    assert ledger.cost is not None
    assert budget.committed_nusd == 2 * ledger.cost
    with pytest.raises(GatewayError, match="turn_limit"):
        call(gateway, budget)
    assert ledger.events.count("send") == 2


@pytest.mark.parametrize("arguments", ['{"x":NaN}', '{"x":1,"x":2}', '[]'])
def test_tool_arguments_require_unambiguous_finite_json(arguments: str) -> None:
    result = response()
    result["output"][0]["arguments"] = arguments
    gateway, ledger, _, budget = setup(result)
    with pytest.raises(GatewayError, match="provider_invalid_response"):
        call(gateway, budget)
    assert ledger.events[-1] == "settle"


def test_wrong_model_keeps_full_hold_and_pauses_instead_of_using_wrong_prices() -> None:
    result = response()
    result["model"] = "unpriced-model"
    gateway, ledger, _, budget = setup(result)
    with pytest.raises(GatewayError, match="provider_model_mismatch"):
        call(gateway, budget)
    assert ledger.events == ["reserve", "send", "uncertain", "pause"]
    assert budget.committed_nusd == ledger.amount
    assert ledger.cost is None


def test_deadline_expiring_during_reservation_releases_before_send() -> None:
    gateway, ledger, _, budget = setup()
    times = iter([1.0, 11.0])
    gateway._monotonic = lambda: next(times)
    with pytest.raises(GatewayError, match="deadline_exceeded"):
        call(gateway, budget)
    assert ledger.events == ["reserve", "release"]
    assert budget.committed_nusd == 0


def test_transport_pins_official_endpoint_and_disables_sdk_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from rockygpt_brain.provider import OpenAITransport

    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.invalid/untrusted")
    transport = OpenAITransport(SETTINGS)
    try:
        assert str(transport._client.base_url) == "https://api.openai.com/v1/"
        assert transport._client.max_retries == 0
        assert transport._client.project == SETTINGS.project
    finally:
        asyncio.run(transport.close())


@pytest.mark.parametrize("status,expected,retryable", [
    (400, "provider_request_rejected", False), (401, "provider_request_rejected", False),
    (403, "provider_request_rejected", False), (404, "provider_request_rejected", False),
    (408, "model_timeout", True), (429, "provider_unavailable", True),
    (500, "provider_unavailable", True),
])
def test_provider_status_errors_are_classified_without_exposing_provider_content(
    status: int, expected: str, retryable: bool, caplog: pytest.LogCaptureFixture,
) -> None:
    gateway, ledger, transport, budget = setup()
    transport.error = APIStatusError(
        "secret error message", body={"student": "private student text"},
        response=httpx.Response(status, request=httpx.Request("POST", "https://api.openai.com")),
    )
    with pytest.raises(GatewayError) as caught:
        call(gateway, budget)
    assert caught.value.code == expected
    assert caught.value.retryable is retryable
    assert f"http_status={status}" in caplog.text
    assert "exception_type=APIStatusError" in caplog.text
    assert "secret" not in caplog.text
    assert "private" not in caplog.text
    assert ledger.events == ["reserve", "send", "uncertain"]
    assert ledger.cost is None


def test_sdk_timeout_has_distinct_safe_diagnostics(caplog: pytest.LogCaptureFixture) -> None:
    gateway, ledger, transport, budget = setup()
    transport.error = APITimeoutError(request=httpx.Request("POST", "https://api.openai.com"))
    with pytest.raises(GatewayError, match="model_timeout"):
        call(gateway, budget)
    assert "exception_type=APITimeoutError" in caplog.text
    assert ledger.events == ["reserve", "send", "uncertain"]


def test_response_serialization_omits_wrapper_nulls_but_preserves_argument_nulls() -> None:
    from types import SimpleNamespace

    from openai.types.responses import Response

    from rockygpt_brain.provider import OpenAITransport

    class Responses:
        async def create(self, **kwargs: Any) -> Response:
            return Response.model_construct(
                output=[{
                    "type": "reasoning", "id": "reasoning-id", "summary": [],
                    "content": None, "status": None, "encrypted_content": "encrypted",
                }, {
                    "type": "function_call", "call_id": "call-id", "name": "finish",
                    "arguments": '{"result_id":null}', "id": None, "status": None,
                }],
            )

    transport = OpenAITransport.__new__(OpenAITransport)
    transport._client = SimpleNamespace(responses=Responses())  # type: ignore[assignment]
    result = asyncio.run(transport.send({}, 1))
    assert result["output"][0] == {"type": "reasoning", "id": "reasoning-id", "summary": [],
                                   "encrypted_content": "encrypted"}
    assert result["output"][1]["arguments"] == '{"result_id":null}'
    assert "status" not in result["output"][1]
