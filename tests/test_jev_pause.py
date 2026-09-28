"""The Brain stops waiting on Jev for a minute once it stops answering."""

import asyncio
import json
from collections.abc import Callable
from typing import Any
from unittest.mock import Mock

import httpx
import pytest

from rockygpt_brain.config import RELEASE
from rockygpt_brain.core.provider import (
    JEV_PAUSES,
    JevPause,
    JevProvider,
    ModelResponse,
    PaidGateway,
    Usage,
)
from rockygpt_brain.core.routing import route_request, routing_payload
from rockygpt_brain.governance.accounting import PaidCallError
from test_routing import ENTITY, NOW, answers_for, data_mock, messages


def test_three_misses_in_a_row_pause_jev_for_a_minute() -> None:
    now = [0.0]
    pause = JevPause(clock=lambda: now[0])
    for answered in (False, False, True, False, False):
        pause.record(answered)
    assert not pause.paused()  # An answer in between starts the count again.
    pause.record(answered=False)
    assert pause.paused()
    now[0] = 59.9
    assert pause.paused()
    now[0] = 60.0
    assert not pause.paused()
    # The first call after the pause tries Jev; one more miss pauses it again.
    pause.record(answered=False)
    assert pause.paused()
    now[0] = 120.0
    pause.record(answered=True)
    pause.record(answered=False)
    assert not pause.paused()


def jev_answering(monkeypatch: pytest.MonkeyPatch,
                  handler: Callable[[httpx.Request], httpx.Response]) -> None:
    async def respond(request: httpx.Request) -> httpx.Response:
        return handler(request)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs))


def ask_jev(timeout: float = 2) -> None:
    JevProvider("secret").create(timeout=timeout, model=RELEASE.routing.model,
                                 state="hello", questions={})


@pytest.mark.parametrize("status,counts", [(529, True), (520, True), (429, True),
                                           (422, False), (401, False)])
def test_server_errors_and_rate_limits_count_as_misses(
    status: int, counts: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    jev_answering(monkeypatch, lambda request: httpx.Response(status, json={}))
    for _ in range(3):
        with pytest.raises(httpx.HTTPStatusError):
            ask_jev()
    assert JEV_PAUSES["typesafe"].paused() is counts


def test_only_a_fair_wait_that_runs_out_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200, json={})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(slow), **kwargs))
    monkeypatch.setattr("rockygpt_brain.core.provider.JEV_FAIR_WAIT", 0.05)
    # A turn with little time left gave Jev too short a wait to judge it.
    for _ in range(3):
        with pytest.raises(TimeoutError):
            ask_jev(timeout=0.02)
    assert not JEV_PAUSES["typesafe"].paused()
    for _ in range(3):
        with pytest.raises(TimeoutError):
            ask_jev(timeout=0.06)
    assert JEV_PAUSES["typesafe"].paused()


def test_an_answer_ends_the_run_of_misses(monkeypatch: pytest.MonkeyPatch) -> None:
    replies = iter([529, 529, 200, 529, 529])

    def handler(request: httpx.Request) -> httpx.Response:
        status = next(replies)
        body: dict[str, Any] = {"answers": {}, "usage": {"input_tokens": 5, "output_tokens": 1},
                                "model": RELEASE.routing.model}
        return httpx.Response(status, json=body)

    jev_answering(monkeypatch, handler)
    for _ in range(5):
        try:
            ask_jev()
        except httpx.HTTPStatusError:
            pass
    assert not JEV_PAUSES["typesafe"].paused()


def test_a_paused_jev_is_neither_called_nor_charged() -> None:
    for _ in range(3):
        JEV_PAUSES["typesafe"].record(answered=False)
    jev, ledger = Mock(), Mock()
    jev.name = "typesafe"
    gateway = PaidGateway(Mock(), ledger, "paused", routing_provider=jev, clock=lambda: NOW)
    payload, _ = routing_payload(messages(), [ENTITY], NOW)
    with pytest.raises(PaidCallError, match="routing_paused"):
        gateway.route(payload, timeout=2)
    jev.create.assert_not_called()
    ledger.reserve.assert_not_called()
    assert gateway.usage.report()["routingCalls"] == 0
    # The other provider is judged on its own.
    jev.name = "openrouter"
    jev.create.return_value = ModelResponse("", RELEASE.routing.model, "completed",
                                            json.dumps(answers_for(payload)), [],
                                            Usage(100, 0, 1, 0))
    gateway.route(payload, timeout=2)
    jev.create.assert_called_once()


def test_a_paused_jev_sends_the_turn_to_gpt_at_once() -> None:
    for _ in range(3):
        JEV_PAUSES["typesafe"].record(answered=False)
    jev = Mock()
    jev.name = "typesafe"
    gateway = PaidGateway(Mock(), Mock(), "paused", routing_provider=jev, clock=lambda: NOW)
    decision = route_request(messages(), data=data_mock(), client=gateway, now=NOW, timeout=2)
    assert decision.reason == "routing_paused"
    assert decision.calls == 0 and decision.tool is None
    assert decision.elapsed_ms < 100
