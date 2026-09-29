"""A call forced to plan lookups gets no longer than the lookup window (core audit C7, 09-29).

The draft timeout keeps only the review's time (30 s at the start of a 45 s turn), but
lookups stop 15 s in. A forced plan back at 16 s was refused as retrieval_time and the
writer answered without it, so the turn paid for a plan it could never use.
"""

import json
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

from rockygpt_brain.campus.progress import WorkLog
from rockygpt_brain.config import RELEASE
from rockygpt_brain.contracts import ChatMessage
from rockygpt_brain.core import engine
from rockygpt_brain.core.engine import run_turn
from rockygpt_brain.core.routing import RouteDecision
from rockygpt_brain.governance.accounting import PaidCallError
from test_engine import NOW, answer, review, tools

WINDOW = RELEASE.turn_seconds - RELEASE.answer_reserve_seconds  # 15 s
DRAFT = RELEASE.turn_seconds - RELEASE.review_reserve_seconds  # 30 s


def controlled(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    clock = [0.0]
    monkeypatch.setattr(engine, "monotonic", lambda: clock[0])
    return clock


def routed(monkeypatch: pytest.MonkeyPatch, clock: list[float], decision: RouteDecision,
           took: float) -> None:
    def route(*args: Any, **kwargs: Any) -> RouteDecision:
        clock[0] += took
        return decision

    monkeypatch.setattr(engine, "route_request", route)


def turn(client: Mock, data: Mock, *, active: bool, **kwargs: Any) -> dict[str, Any]:
    return run_turn(
        [ChatMessage(role="user", content="What is the Registrar phone?")],
        client=client, data=data, model="test", now=NOW,
        routing_client=Mock() if active else None,
        routing_mode="active" if active else "off", **kwargs)


def test_a_graph_first_plan_waits_only_for_the_lookup_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = controlled(monkeypatch)
    monkeypatch.setattr(engine, "graph_first", lambda *args: True)
    client, data = Mock(), Mock()
    data.lookup_profile.return_value = {"status": "not_found", "records": []}

    def generate(**kwargs: Any) -> SimpleNamespace:
        if client.create.call_count == 1:
            # Back inside the window, so its lookup runs.
            clock[0] = WINDOW - 1
            return tools(SimpleNamespace(
                type="function_call", name="lookup_profile", call_id="lookup",
                arguments=json.dumps({"entity": "Registrar", "include": ["contact"]})))
        if client.create.call_count == 2:
            return answer("I couldn't find the Registrar's phone.", "limitation",
                          status="unavailable")
        return review()

    client.create.side_effect = generate
    turn(client, data, active=False)
    first, writer = client.create.call_args_list[:2]
    assert first.kwargs["tool_choice"] == "required"
    assert first.kwargs["timeout"].read == WINDOW < DRAFT
    assert first.kwargs["timeout"].connect == 2.0
    data.lookup_profile.assert_called_once()
    # The call after the lookups only offers tools, so it keeps the draft timeout.
    assert writer.kwargs["timeout"].read == DRAFT - (WINDOW - 1)


def test_a_plan_jev_forces_waits_only_for_what_is_left_of_the_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = controlled(monkeypatch)
    routed(monkeypatch, clock, RouteDecision(route="contact", confidence=0.9,
                                             tool="lookup_contact"), took=2)
    client, data = Mock(), Mock()
    client.create.side_effect = [answer(), review()]
    turn(client, data, active=True)
    first = client.create.call_args_list[0]
    assert first.kwargs["tool_choice"] == {"type": "function", "name": "lookup_contact"}
    assert first.kwargs["timeout"].read == WINDOW - 2


def test_a_call_that_only_offers_tools_keeps_the_draft_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = controlled(monkeypatch)
    routed(monkeypatch, clock, RouteDecision(route="general", confidence=0.96), took=2)
    client, data = Mock(), Mock()
    client.create.side_effect = [answer(), review()]
    turn(client, data, active=True)
    first = client.create.call_args_list[0]
    assert first.kwargs["tool_choice"] == "auto"
    assert first.kwargs["tools"]
    assert first.kwargs["timeout"].read == DRAFT - 2


def graph_first_at(clock: list[float], at: float) -> Callable[..., bool]:
    def picked(*args: Any) -> bool:
        clock[0] = at  # Time spent before the first call, however it went.
        return True

    return picked


@pytest.mark.parametrize("forced_by", ["graph_first", "jev"])
def test_a_closed_window_forces_no_plan(
    monkeypatch: pytest.MonkeyPatch, forced_by: str,
) -> None:
    clock = controlled(monkeypatch)
    at = WINDOW - 0.5
    if forced_by == "jev":
        routed(monkeypatch, clock, RouteDecision(route="contact", confidence=0.9,
                                                 tool="lookup_contact"), took=at)
    else:
        monkeypatch.setattr(engine, "graph_first", graph_first_at(clock, at))
    client, data = Mock(), Mock()
    client.create.side_effect = [
        answer("I can't look that up right now.", "limitation", status="unavailable"),
        review(),
    ]
    work = WorkLog(0.0)
    result = turn(client, data, active=forced_by == "jev", progress=work.watch(None),
                  diagnostics={"work": work})
    first = client.create.call_args_list[0]
    assert first.kwargs["tools"] == []
    assert first.kwargs["tool_choice"] == "none"
    # Not a plan, so the writer keeps the draft timeout.
    assert first.kwargs["timeout"].read == DRAFT - at
    [draft] = [step["draft"] for step in work.report()["steps"] if "draft" in step]
    assert draft["answerOnly"] == "retrieval_time"
    assert result["metrics"]["reviewCalls"] == 1
    data.lookup_contact.assert_not_called()
    data.lookup_profile.assert_not_called()


def slow_plan(clock: list[float], took: float) -> Callable[..., SimpleNamespace]:
    """A plan that comes back `took` seconds in, or times out at its own read timeout."""

    def plan(**kwargs: Any) -> SimpleNamespace:
        wait = kwargs["timeout"].read
        if wait < took:
            clock[0] += wait
            raise PaidCallError("model_timeout")
        clock[0] += took
        return tools(SimpleNamespace(
            type="function_call", name="lookup_profile", call_id="lookup",
            arguments=json.dumps({"entity": "Registrar", "include": ["contact"]})))

    return plan


def test_a_plan_the_window_cuts_off_still_gets_a_reviewed_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The audit's trigger: a forced plan that would take 16 s. Before the cap the turn
    # waited for it, refused its lookup and still answered; the cap must not turn that
    # into a failed turn.
    clock = controlled(monkeypatch)
    monkeypatch.setattr(engine, "graph_first", lambda *args: True)
    client, data = Mock(), Mock()
    plan = slow_plan(clock, took=16)

    def generate(**kwargs: Any) -> SimpleNamespace:
        if client.create.call_count == 1:
            return plan(**kwargs)
        if client.create.call_count == 2:
            return answer("I couldn't check the Registrar's phone right now.", "limitation",
                          status="unavailable")
        return review()

    client.create.side_effect = generate
    work = WorkLog(0.0)
    result = turn(client, data, active=False, progress=work.watch(None),
                  diagnostics={"work": work})
    first, writer, _ = client.create.call_args_list
    assert first.kwargs["timeout"].read == WINDOW
    assert writer.kwargs["tools"] == []
    assert writer.kwargs["tool_choice"] == "none"
    # The writer and review keep the time they had before the cap.
    assert writer.kwargs["timeout"].read == DRAFT - WINDOW
    assert result["status"] == "unavailable"
    assert result["metrics"]["planTimedOut"] is True
    assert result["metrics"]["draftCalls"] == 2
    assert result["metrics"]["reviewCalls"] == 1
    [draft] = [step["draft"] for step in work.report()["steps"] if "draft" in step]
    assert draft["answerOnly"] == "retrieval_time"
    data.lookup_profile.assert_not_called()


def test_a_timeout_inside_the_window_still_fails_the_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A connect timeout 2 s in is the provider failing, not the window closing.
    clock = controlled(monkeypatch)
    monkeypatch.setattr(engine, "graph_first", lambda *args: True)
    client, data = Mock(), Mock()

    def unreachable(**kwargs: Any) -> SimpleNamespace:
        clock[0] += kwargs["timeout"].connect
        raise PaidCallError("model_timeout")

    client.create.side_effect = unreachable
    with pytest.raises(PaidCallError, match="model_timeout"):
        turn(client, data, active=False)
    assert client.create.call_count == 1
