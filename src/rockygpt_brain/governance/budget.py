"""Shared turn admission policy for the controller and the paid boundary.

The controller counts attempted work. The gateway independently admits paid work
using its actual settled costs and retained uncertain reservations. Both use this
policy; a caller cannot bypass limits by invoking the gateway directly.
"""

from collections.abc import Callable
from time import monotonic

from rockygpt_brain.config import RELEASE, Release
from rockygpt_brain.governance.accounting import Category, PaidCallError


class TurnBudget:
    def __init__(
        self, release: Release = RELEASE, *, clock: Callable[[], float] = monotonic
    ) -> None:
        self.release = release
        self.clock = clock
        self.started = clock()
        self.draft_calls = 0
        self.review_calls = 0
        self.routing_calls = 0
        self.retrieval_rounds = 0
        self.tool_calls = 0

    @property
    def remaining(self) -> float:
        return self.release.turn_seconds - (self.clock() - self.started)

    @property
    def retrieval_deadline(self) -> float:
        return self.started + self.release.turn_seconds - self.release.answer_reserve_seconds

    def model_timeout(self, category: Category) -> float:
        if category == "routing":
            if self.routing_calls or self.draft_calls or self.review_calls:
                raise PaidCallError("model_call_limit")
            available = min(self.release.routing.timeout_seconds,
                            self.remaining - self.release.answer_reserve_seconds)
            if available <= 0:
                raise TimeoutError("Insufficient routing time")
            return available
        if (
            self.draft_calls + self.review_calls >= self.release.max_model_calls
            or (
                category == "draft"
                and (self.review_calls > 0 or self.draft_calls >= self.release.max_draft_calls)
            )
            or (category == "review" and self.review_calls >= 1)
        ):
            raise PaidCallError("model_call_limit")
        reserve = self.release.review_reserve_seconds if category == "draft" else 0
        available = self.remaining - reserve
        if available <= 0:
            raise TimeoutError("Insufficient turn time for required verification")
        return available

    def note_model(self, category: Category) -> None:
        if category == "routing":
            self.routing_calls += 1
        elif category == "draft":
            self.draft_calls += 1
        else:
            self.review_calls += 1

    @property
    def can_retrieve(self) -> bool:
        return (
            self.retrieval_rounds < self.release.max_retrieval_rounds
            and self.tool_calls < self.release.max_tool_calls
            and self.draft_calls < self.release.max_draft_calls
            and self.draft_calls + self.review_calls <= self.release.max_model_calls - 2
            and self.remaining > self.release.answer_reserve_seconds
        )

    def begin_retrieval(self) -> bool:
        if not self.can_retrieve:
            return False
        self.retrieval_rounds += 1
        return True

    def admit_tool(self) -> bool:
        # A round already admitted may contain several independent operations.
        if (
            self.tool_calls >= self.release.max_tool_calls
            or self.clock() >= self.retrieval_deadline
        ):
            return False
        self.tool_calls += 1
        return True

    def admit_cost(self, category: Category, input_tokens: int, committed_nusd: int) -> int:
        if category == "routing":
            if input_tokens > 64000:
                raise PaidCallError("routing_context_limit")
            reservation = input_tokens * self.release.routing.price.input_nusd
            review_reserve = (
                self.release.max_input_tokens * self.release.price.input_nusd
                + self.release.review_output_tokens * self.release.price.output_nusd
            )
            if committed_nusd + reservation + review_reserve > self.release.max_turn_cost_nusd:
                raise PaidCallError("turn_cost_limit")
            return reservation
        if input_tokens > self.release.max_input_tokens:
            raise PaidCallError("context_limit")
        output_limit = (
            self.release.draft_output_tokens
            if category == "draft"
            else self.release.review_output_tokens
        )
        price = self.release.price
        reservation = input_tokens * price.input_nusd + output_limit * price.output_nusd
        # A draft may contain campus prose, so keep the worst admissible review
        # funded until the response proves that review is exempt. This is local
        # turn admission; the durable monthly ledger still reserves every call.
        review_reserve = (
            self.release.max_input_tokens * price.input_nusd
            + self.release.review_output_tokens * price.output_nusd
            if category == "draft"
            else 0
        )
        if committed_nusd + reservation + review_reserve > self.release.max_turn_cost_nusd:
            raise PaidCallError("turn_cost_limit")
        return reservation

    def retrieval_context_limit(self, current_bound: int, pending_tools: int) -> int:
        """Share remaining context among new results; keep room for composition.

        Admission still checks the actual next draft/review payload. This only
        limits newly delivered evidence; accepted history is never shortened.
        """
        remaining = max(
            0, self.release.max_input_tokens - current_bound - 2 * self.release.draft_output_tokens
        )
        return current_bound + remaining // max(1, pending_tools)
