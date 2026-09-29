"""One turn, step by step. Each milestone adds its step here, in the plan's order.

A turn yields progress events as it goes and its result last, so a streaming app sees
each step (and the safety help) the moment it happens.
"""

from collections.abc import Iterator
from typing import Any, NamedTuple

from rockygpt_brain.context import Context
from rockygpt_brain.contract import ChatReply, ErrorCode, FailureReply, ProgressEvent, SafetyBlock
from rockygpt_brain.decisions import Decisions, ask_jev, handler, readings
from rockygpt_brain.failures import failure
from rockygpt_brain.jev import Jev, JevError
from rockygpt_brain.safety import ACCOUNT_LIMIT, Danger, safety_block, said_danger
from rockygpt_brain.spending import SpendingError
from rockygpt_brain.work import Work

NOT_YET = "RockyGPT's new Brain can't answer the rest of your question yet."


class TurnResult(NamedTuple):
    status: int
    body: ChatReply | FailureReply
    # The safety help the turn gave, if any.
    safety: SafetyBlock | None = None
    # For the dev UI and the turn log: codes, numbers and Jev's readings, never the
    # student's words.
    metrics: dict[str, Any] = {}


def worst(first: Danger | None, second: Danger | None) -> Danger | None:
    """Self-harm help also names 911, so it covers both."""
    kinds = {first, second}
    return "self_harm" if "self_harm" in kinds else "danger" if "danger" in kinds else None


def stopped(error: SpendingError) -> ErrorCode:
    if error.code in {"budget_exhausted", "accounting_paused"}:
        return error.code  # type: ignore[return-value]
    if error.code == "accounting_bound_exceeded":
        return "accounting_paused"
    return "accounting_unavailable"


def run_turn(context: Context, request_id: str, jev: Jev | None,
             work: Work) -> Iterator[ProgressEvent | TurnResult]:
    # Safety: the danger phrases need no model and no money, so their help goes out first.
    said = said_danger(context.question)
    shown = safety_block(said) if said else None
    yield ProgressEvent(stage="understanding", safety=shown)

    # Jev: one call reads what the question asks.
    metrics: dict[str, Any] = {"routingCalls": 0, "dangerPhrase": said}
    decisions: Decisions | None = None
    stop: SpendingError | None = None
    if jev is None:
        metrics["jev"] = {"skipped": "routing_unavailable"}
    else:
        try:
            decisions, asked = ask_jev(jev, context, request_id, work.jev_call)
            metrics["routingCalls"] = 1
            metrics["jev"] = {"answers": readings(asked.answers),
                              # What code made of the answers (decisions.py's bars).
                              "decided": {"danger": decisions.danger,
                                          "ownAccount": decisions.own_account,
                                          "needsEarlier": decisions.needs_earlier,
                                          "asks": decisions.asks,
                                          "subject": decisions.subject,
                                          "named": decisions.named,
                                          "needs": decisions.needs,
                                          "multiPart": decisions.multi_part,
                                          "reach": decisions.reach,
                                          "outcome": decisions.outcome,
                                          "route": decisions.route},
                              "costNusd": asked.cost_nusd,
                              "inputTokens": asked.input_tokens, "elapsedMs": asked.elapsed_ms}
        except JevError as error:
            metrics["routingCalls"] = int(error.sent)
            metrics["jev"] = {"skipped": error.code}
        except SpendingError as error:
            # Refused before the call, or its accounting failed after: all paid work stops.
            stop = error
            metrics["jev"] = {"skipped": error.code}

    # Which later handler should take it. Until those milestones, the turn still ends
    # below with safety help, the account limit or "not ready".
    metrics["handler"] = (handler(decisions, said) if decisions
                          else "safety" if said else None)
    danger = worst(said, decisions.danger if decisions else None)
    if danger is not None and danger != said:
        shown = safety_block(danger)
        yield ProgressEvent(stage="understanding", safety=shown)
    own_account = decisions is not None and decisions.own_account

    # Final assembly.
    if shown is not None:
        metrics["responseMode"] = "safety_net"
        work.decided(written={"by": "code", "mode": "safety_net"})
        yield TurnResult(200, ChatReply(
            answer=f"{shown.answer}\n\n{ACCOUNT_LIMIT if own_account else NOT_YET}",
            status="partial",
            citations=shown.citations,
            requestId=request_id,
        ), shown, metrics)
    elif stop is not None:
        code = stopped(stop)
        metrics["responseMode"] = code
        yield TurnResult(*failure(code, request_id, reset_at=stop.reset_at), None, metrics)
    elif own_account:
        # Code says what RockyGPT can't reach, with no model writing it.
        metrics["responseMode"] = "access_limit"
        work.decided(written={"by": "code", "mode": "access_limit"})
        yield TurnResult(200, ChatReply(answer=ACCOUNT_LIMIT, status="unavailable",
                                        citations=[], requestId=request_id), None, metrics)
    else:
        metrics["responseMode"] = "not_ready"
        yield TurnResult(*failure("not_ready", request_id), None, metrics)
