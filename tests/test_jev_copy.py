"""A Jev call that hasn't answered gets one copy, and the first answer wins."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from time import monotonic
from typing import Any
from unittest.mock import Mock

import httpx
import pytest

from rockygpt_brain.config import RELEASE
from rockygpt_brain.core.provider import JevProvider, PaidGateway
from rockygpt_brain.core.routing import routing_payload
from rockygpt_brain.governance.accounting import PaidCallError
from test_routing import ENTITY, NOW, answers_for, messages

PAYLOAD, _ = routing_payload(messages(), [ENTITY], NOW)
REAL_CLIENT = httpx.AsyncClient


def jev_serving(monkeypatch: pytest.MonkeyPatch,
                *replies: Callable[[], Awaitable[httpx.Response]]) -> list[int]:
    """Jev answers the n-th request with the n-th reply; returns the requests seen."""
    seen: list[int] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(len(seen))
        return await replies[len(seen) - 1]()

    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda **kwargs: REAL_CLIENT(transport=httpx.MockTransport(handler), **kwargs))
    return seen


def answer(after: float = 0, name: str = "jev") -> Callable[[], Awaitable[httpx.Response]]:
    async def reply() -> httpx.Response:
        await asyncio.sleep(after)
        return httpx.Response(200, json={
            "id": name, "model": RELEASE.routing.model,
            "answers": answers_for(PAYLOAD), "usage": {"input_tokens": 100, "output_tokens": 3},
        })
    return reply


def failure(status: int) -> Callable[[], Awaitable[httpx.Response]]:
    async def reply() -> httpx.Response:
        return httpx.Response(status, json={})
    return reply


def ask(copy: Callable[[], bool], timeout: float = 2, after: float = 0.05) -> Any:
    return JevProvider("secret").create_hedged(
        timeout=timeout, hedge_after=after, copy=copy, model=RELEASE.routing.model,
        state=PAYLOAD["state"], questions=PAYLOAD["questions"])


def test_a_slow_call_s_copy_answers_first(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = jev_serving(monkeypatch, answer(after=5, name="first"), answer(name="copy"))
    copies = Mock(return_value=True)
    started = monotonic()
    response, answered = ask(copies)
    assert monotonic() - started < 1
    assert answered == 1 and response.id == "copy" and len(seen) == 2
    copies.assert_called_once()


def test_a_call_that_answers_in_time_gets_no_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = jev_serving(monkeypatch, answer(name="first"))
    copies = Mock(return_value=True)
    response, answered = ask(copies)
    assert (answered, response.id, len(seen)) == (0, "first", 1)
    copies.assert_not_called()


def test_the_call_can_still_win_after_its_copy_is_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jev_serving(monkeypatch, answer(after=0.1, name="first"), answer(after=5, name="copy"))
    response, answered = ask(Mock(return_value=True))
    assert (answered, response.id) == (0, "first")


def test_no_copy_without_room_for_it(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = jev_serving(monkeypatch, answer(after=5), answer())
    # The ledger or budget refused the copy.
    with pytest.raises(TimeoutError):
        ask(Mock(return_value=False), timeout=0.6)
    assert len(seen) == 1
    # Too little time left for a copy to answer: it is never offered.
    jev_serving(monkeypatch, answer(after=5), answer())
    copies = Mock(return_value=True)
    with pytest.raises(TimeoutError):
        ask(copies, timeout=0.5, after=0.1)
    copies.assert_not_called()


def test_a_failed_call_waits_for_its_copy_and_both_failing_raises_the_first_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def late_failure() -> httpx.Response:
        await asyncio.sleep(0.2)
        return httpx.Response(529, json={})

    jev_serving(monkeypatch, late_failure, answer(after=0.3, name="copy"))
    assert ask(Mock(return_value=True))[1] == 1
    # The copy fails at once, before the call's own failure.
    jev_serving(monkeypatch, late_failure, failure(520))
    with pytest.raises(httpx.HTTPStatusError) as raised:
        ask(Mock(return_value=True))
    assert raised.value.response.status_code == 520


def gateway(ledger: Mock) -> PaidGateway:
    release = RELEASE.model_copy(update={
        "routing": RELEASE.routing.model_copy(update={"hedge_seconds": 0.05})})
    return PaidGateway(Mock(), ledger, "copy-test", release=release,
                       routing_provider=JevProvider("secret"), clock=lambda: NOW)


def test_each_copy_is_reserved_and_the_loser_is_left_uncertain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jev_serving(monkeypatch, answer(after=5, name="first"), answer(name="copy"))
    ledger = Mock()
    paid = gateway(ledger)
    assert paid.route(PAYLOAD, timeout=2) == answers_for(PAYLOAD)
    (first, _, _, held, metadata, _), (copy, _, _, copy_held, copy_metadata, _) = [
        call.args for call in ledger.reserve.call_args_list]
    assert copy_metadata == {**metadata, "copy_of": first} and copy_held == held
    settled = ledger.settle.call_args.args
    assert settled[0] == copy and settled[3] == "copy"
    ledger.uncertain.assert_called_once()
    assert ledger.uncertain.call_args.args[:2] == (first, "routing_copy_cancelled")
    report = paid.usage.report()
    assert report["routingCalls"] == 2 and report["costNusd"] == 4200
    assert report["unsettledNusd"] == held
    # One routing call for the turn's budget, however many copies it took.
    with pytest.raises(PaidCallError, match="model_call_limit"):
        paid.route(PAYLOAD, timeout=2)


def test_a_call_and_copy_that_never_answer_are_both_uncertain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jev_serving(monkeypatch, answer(after=5), answer(after=5))
    ledger = Mock()
    with pytest.raises(PaidCallError, match="routing_provider_error"):
        gateway(ledger).route(PAYLOAD, timeout=0.7)
    assert ledger.reserve.call_count == 2
    assert sorted(call.args[1] for call in ledger.uncertain.call_args_list) == [
        "routing_provider_error", "routing_provider_error"]
    assert {call.args[0] for call in ledger.uncertain.call_args_list} == {
        call.args[0] for call in ledger.reserve.call_args_list}


def test_a_refused_copy_reservation_leaves_the_call_waiting_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jev_serving(monkeypatch, answer(after=0.2, name="first"), answer(name="copy"))
    ledger = Mock()
    ledger.reserve.side_effect = [None, PaidCallError("budget_exhausted")]
    paid = gateway(ledger)
    assert paid.route(PAYLOAD, timeout=2) == answers_for(PAYLOAD)
    assert ledger.settle.call_args.args[3] == "first"
    ledger.uncertain.assert_not_called()
    assert paid.usage.report()["routingCalls"] == 1


def test_the_one_copy_is_counted_once_in_the_turn_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jev_serving(monkeypatch, answer(after=5, name="first"), answer(name="copy"))
    ledger = Mock()
    paid = gateway(ledger)
    paid.route(PAYLOAD, timeout=2)
    paid.finish({"status": "answered"})
    summary = ledger.record_turn.call_args.args[1]
    assert summary["routingCalls"] == 2 and summary["usageComplete"] is False
    assert json.dumps(summary)  # Stays plain data.
