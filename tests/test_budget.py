"""Admission boundaries with a controlled clock; no paid calls."""

from unittest.mock import Mock

import pytest

from rockygpt_brain.config import RELEASE
from rockygpt_brain.core.provider import PaidGateway
from rockygpt_brain.governance.accounting import PaidCallError
from rockygpt_brain.governance.budget import TurnBudget
from test_provider import NOW, arguments, response


def test_two_tool_rounds_leave_writer_and_one_review() -> None:
    budget = TurnBudget(clock=lambda: 0)
    for _ in range(2):
        budget.model_timeout("draft")
        budget.note_model("draft")
        assert budget.begin_retrieval()
        for _ in range(4):
            assert budget.admit_tool()
    assert not budget.can_retrieve
    assert not budget.begin_retrieval()
    assert not budget.admit_tool()
    budget.model_timeout("draft")
    budget.note_model("draft")
    budget.model_timeout("review")
    budget.note_model("review")
    for category in ("draft", "review"):
        with pytest.raises(PaidCallError, match="model_call_limit"):
            budget.model_timeout(category)


def test_rejected_review_has_no_recheck_or_rewrite_capacity() -> None:
    budget = TurnBudget(clock=lambda: 0)
    budget.note_model("review")
    for category in ("review", "draft"):
        with pytest.raises(PaidCallError, match="model_call_limit"):
            budget.model_timeout(category)


def test_clock_reserves_writer_and_review_time() -> None:
    clock = [0.0]
    budget = TurnBudget(clock=lambda: clock[0])
    clock[0] = budget.retrieval_deadline
    assert not budget.begin_retrieval()
    assert not budget.admit_tool()
    assert budget.model_timeout("draft") == (
        RELEASE.answer_reserve_seconds - RELEASE.review_reserve_seconds
    )
    clock[0] = RELEASE.turn_seconds - RELEASE.review_reserve_seconds
    with pytest.raises(TimeoutError):
        budget.model_timeout("draft")
    assert budget.model_timeout("review") == RELEASE.review_reserve_seconds
    clock[0] = RELEASE.turn_seconds
    with pytest.raises(TimeoutError):
        budget.model_timeout("review")


def test_context_cost_and_verification_reserve_are_checked_before_payment() -> None:
    provider, ledger = Mock(), Mock()
    gateway = PaidGateway(
        provider,
        ledger,
        "bounded",
        clock=lambda: NOW,
        release=RELEASE.model_copy(update={"max_turn_cost_nusd": 1}),
    )
    with pytest.raises(PaidCallError, match="turn_cost_limit"):
        gateway.create(category="draft", **arguments())
    ledger.reserve.assert_not_called()
    provider.create.assert_not_called()
    with pytest.raises(PaidCallError, match="context_limit"):
        gateway.budget.admit_cost("draft", RELEASE.max_input_tokens + 1, 0)


def test_cost_uses_settled_usage_and_retains_uncertain_reservations() -> None:
    provider, ledger = Mock(), Mock()
    provider.create.return_value = response()
    gateway = PaidGateway(provider, ledger, "costs", clock=lambda: NOW)
    gateway.create(category="draft", **arguments())
    first = gateway.usage.calls[0]
    assert first["reservedNusd"] > first["costNusd"]
    bound = ledger.reserve.call_args.args[4]["input_token_bound"]
    reserve = gateway.budget.admit_cost("draft", bound, first["costNusd"])
    review_reserve = (
        RELEASE.max_input_tokens * RELEASE.price.input_nusd
        + RELEASE.review_output_tokens * RELEASE.price.output_nusd
    )
    # At the exact ceiling a settled call fits, but the same unresolved call
    # remains charged at its full reservation and must fail closed.
    narrow = RELEASE.model_copy(
        update={
            "max_turn_cost_nusd": first["costNusd"] + reserve + review_reserve,
        }
    )
    gateway.budget = TurnBudget(narrow)
    gateway.create(category="draft", **arguments())
    first["settled"] = False
    first.pop("costNusd")
    with pytest.raises(PaidCallError, match="turn_cost_limit"):
        gateway.create(category="draft", **arguments())
    assert provider.create.call_count == 2


def test_gateway_deadline_applies_without_controller() -> None:
    provider, ledger = Mock(), Mock()
    clock = [0.0]
    gateway = PaidGateway(provider, ledger, "expired", clock=lambda: NOW)
    gateway.budget = TurnBudget(clock=lambda: clock[0])
    clock[0] = RELEASE.turn_seconds
    with pytest.raises(TimeoutError):
        gateway.create(category="draft", **arguments())
    ledger.reserve.assert_not_called()
    provider.create.assert_not_called()
