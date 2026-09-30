"""One turn, step by step. Each milestone adds its step here, in the plan's order.

A turn yields progress events as it goes and its result last, so a streaming app sees
each step (and the safety help) the moment it happens.
"""

from collections.abc import Iterator
from time import monotonic
from typing import Any, Literal, NamedTuple

from rockygpt_brain.campus import CampusReader, CampusUnavailable, Timetable
from rockygpt_brain.context import Context
from rockygpt_brain.contract import (
    ChatReply,
    ErrorCode,
    FailureReply,
    ProgressEvent,
    SafetyBlock,
    Subject,
)
from rockygpt_brain.decisions import ROUTES, Decisions, Handler, ask_jev, handler, readings
from rockygpt_brain.failures import failure
from rockygpt_brain.jev import Jev, JevError
from rockygpt_brain.safety import (
    ACCOUNT_LIMIT,
    LIMITS,
    OTHER_LIMIT,
    UNCLEAR,
    Danger,
    safety_block,
    said_danger,
)
from rockygpt_brain.shuttle_answer import ShuttleAnswer, answer
from rockygpt_brain.shuttle_ask import Asking, Dispatch, dispatch, shuttle_questions
from rockygpt_brain.spending import SpendingError
from rockygpt_brain.work import Work

NOT_YET = "RockyGPT's new Brain can't answer the rest of your question yet."

# What a Jev failure tells the student. Without Jev's readings there is no plan, so the
# turn fails on purpose, with the emergency help, and never hands the question to a model.
JEV_FAILURES: dict[str, ErrorCode] = {
    "routing_timeout": "model_timeout",
    "routing_unavailable": "model_unreachable",
    "routing_rate_limited": "busy",
    "routing_overloaded": "busy",
    "routing_provider_error": "model_provider_error",
    # A rejected key is a setup problem, and trying again can't fix it.
    "routing_auth_failed": "model_not_configured",
    # A body Typesafe refuses as invalid is a bug in the Brain, in the turn log.
    "routing_request_rejected": "internal_error",
    "routing_usage_unknown": "model_provider_error",
    "routing_model_changed": "model_provider_error",
    "routing_invalid_response": "invalid_model_output",
    # Too long for Jev: trying again can't help, a shorter chat can.
    "routing_context_limit": "context_limit",
    # An expired price is a setup problem, not a busy provider.
    "routing_price_unavailable": "model_not_configured",
}


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


def camel(name: str) -> str:
    first, *rest = name.split("_")
    return first + "".join(word.title() for word in rest)


def decided(decisions: Decisions, chosen: Handler) -> dict[str, Any]:
    """Jev's picks, what code read from them, and the route with where it goes, the picks
    that led there (`handlerPath`, the last one settled it) and those Jev put under 0.90."""
    picks = {camel(name): getattr(decisions, name) for name in decisions.sureness}
    return {**picks, "handler": chosen.name, "goesTo": ROUTES[chosen.name],
            "handlerPath": [camel(name) for name in chosen.path],
            "lowConfidence": {camel(name): sureness
                              for name, sureness in chosen.low_confidence.items()}}


class Said(NamedTuple):
    """Words code wrote for a route that needs no model, and how the turn reports them."""

    text: str
    status: Literal["unavailable", "clarification"]
    mode: str


def said_by_code(chosen: Handler | None, decisions: Decisions | None) -> Said | None:
    """The routes built so far that end in words code wrote. Safety help is separate: it
    goes first and needs no route. Every other route waits for its milestone."""
    if chosen is None or decisions is None:
        return None
    if chosen.name == "account_action":
        # All of it needs their account, or Jev's `needs` pick says why RockyGPT can't.
        text = ACCOUNT_LIMIT if decisions.own_account else LIMITS.get(decisions.needs, OTHER_LIMIT)
        return Said(text, "unavailable", "access_limit")
    if chosen.name == "ambiguous":
        return Said(UNCLEAR, "clarification", "clarification")
    return None


def stopped(error: SpendingError) -> ErrorCode:
    if error.code in {"budget_exhausted", "accounting_paused"}:
        return error.code  # type: ignore[return-value]
    if error.code == "accounting_bound_exceeded":
        return "accounting_paused"
    return "accounting_unavailable"


def read_campus(campus: CampusReader | None, jev: Jev | None, said: Danger | None,
                context: Context, metrics: dict[str, Any]) -> tuple[Timetable | None,
                                                                    Asking | None]:
    """The shuttle timetable and the shuttle questions for this turn. The stop list in the
    questions is written from the timetable, so it is read before Jev is asked; a copy read in
    the last few minutes is reused. Nothing is read when it could not be used: no campus
    setting, no Jev, or a danger phrase, which needs neither. When the read fails the
    questions still go, without the stop list, so a shuttle question can end as a data outage
    and not as "not ready"."""
    if campus is None:
        metrics["campus"] = {"skipped": "campus_not_configured"}
    elif jev is None:
        metrics["campus"] = {"skipped": "routing_unavailable"}
    elif said:
        metrics["campus"] = {"skipped": "danger_phrase"}
    else:
        table = None
        started = monotonic()
        try:
            table = campus.timetable()
            metrics["campus"] = {"datasetVersion": table.dataset_version,
                                 "collectedAt": table.collected_at.isoformat()}
        except CampusUnavailable as error:
            metrics["campus"] = {"skipped": error.code}
        metrics["campus"]["readMs"] = round((monotonic() - started) * 1000)
        return table, shuttle_questions(context.now, table)
    return None, None


def run_turn(context: Context, request_id: str, jev: Jev | None, work: Work,
             campus: CampusReader | None = None) -> Iterator[ProgressEvent | TurnResult]:
    # Safety: the danger phrases need no model and no money, so their help goes out first.
    said = said_danger(context.question)
    shown = safety_block(said) if said else None
    yield ProgressEvent(stage="understanding", safety=shown)

    # Jev: one call reads what the question asks, and the shuttle questions with it.
    metrics: dict[str, Any] = {"routingCalls": 0, "dangerPhrase": said}
    table, asking = read_campus(campus, jev, said, context, metrics)
    decisions: Decisions | None = None
    chosen: Handler | None = None
    shuttle: Dispatch | None = None
    stop: SpendingError | None = None
    failed: ErrorCode | None = None
    if jev is None:
        metrics["jev"] = {"skipped": "routing_unavailable"}
    else:
        try:
            decisions, asked = ask_jev(jev, context, request_id, work.jev_call,
                                       asking.questions if asking else None)
            chosen = handler(decisions, said)
            if asking is not None:
                shuttle = dispatch(asked.answers, asking, decisions, chosen, context)
                metrics["shuttle"] = {"refused": shuttle.refused, "picks": shuttle.picks}
            metrics["routingCalls"] = 1
            metrics["jev"] = {"answers": readings(asked.answers),
                              "decided": decided(decisions, chosen),
                              "costNusd": asked.cost_nusd,
                              "inputTokens": asked.input_tokens, "elapsedMs": asked.elapsed_ms}
        except JevError as error:
            metrics["routingCalls"] = int(error.sent)
            metrics["jev"] = {"skipped": error.code,
                              **({"httpStatus": error.status} if error.status else {}),
                              **({"elapsedMs": error.elapsed_ms}
                                 if error.elapsed_ms is not None else {})}
            failed = JEV_FAILURES.get(error.code, "model_provider_error")
        except SpendingError as error:
            # Refused before the call, or its accounting failed after: all paid work stops.
            stop = error
            metrics["jev"] = {"skipped": error.code}

    # Which route should take it. Until their milestones, the turn still ends below with
    # safety help, code-written words (account limit, "can't do", "unclear") or "not ready".
    said_it = said_by_code(chosen, decisions)
    metrics["handler"] = chosen.name if chosen else "danger" if said else None
    danger = worst(said, decisions.danger if decisions else None)
    if danger is not None and danger != said:
        shown = safety_block(danger)
        yield ProgressEvent(stage="understanding", safety=shown)

    # The shuttle timetable answers a plan Jev's picks made, in code, with no model.
    timetable_answer: ShuttleAnswer | None = None
    if shuttle is not None and shuttle.plan is not None and table is not None:
        yield ProgressEvent(stage="retrieving", subjects=[
            Subject(topic="shuttle", date_from=shuttle.plan.day.isoformat())])
        timetable_answer = answer(shuttle.plan, table, context.now)
        metrics["shuttle"].update(kind=timetable_answer.kind, stale=timetable_answer.stale)

    # Final assembly.
    if shown is not None:
        metrics["responseMode"] = "safety_net"
        work.decided(written={"by": "code", "mode": "safety_net"})
        # After the safety help, the account limit when all of it needs their account. The
        # other can't-do lines and the unclear question wait: the route here is danger.
        rest = ACCOUNT_LIMIT if decisions and decisions.own_account else NOT_YET
        yield TurnResult(200, ChatReply(
            answer=f"{shown.answer}\n\n{rest}",
            status="partial",
            citations=shown.citations,
            requestId=request_id,
        ), shown, metrics)
    elif stop is not None:
        code = stopped(stop)
        metrics["responseMode"] = code
        yield TurnResult(*failure(code, request_id, reset_at=stop.reset_at), None, metrics)
    elif failed is not None:
        # No readings, so no plan. A retryable failure, never a guess from a model.
        metrics["responseMode"] = failed
        yield TurnResult(*failure(failed, request_id), None, metrics)
    elif said_it is not None:
        # An account action or a request too unclear to read: code says what RockyGPT
        # can't reach, or asks, with no model writing it.
        metrics["responseMode"] = said_it.mode
        work.decided(written={"by": "code", "mode": said_it.mode})
        yield TurnResult(200, ChatReply(answer=said_it.text, status=said_it.status,
                                        citations=[], requestId=request_id), None, metrics)
    elif timetable_answer is not None and table is not None:
        metrics["responseMode"] = "shuttle_timetable"
        work.decided(written={"by": "code", "mode": "shuttle_timetable"})
        yield TurnResult(200, ChatReply(
            answer=timetable_answer.text, status="answered",
            citations=list(timetable_answer.citations), requestId=request_id,
            datasetVersion=table.dataset_version), None, metrics)
    elif shuttle is not None and shuttle.refused == "data_unavailable":
        # A shuttle question Jev read plainly, and no timetable to answer it from.
        metrics["responseMode"] = "data_unavailable"
        yield TurnResult(*failure("data_unavailable", request_id), None, metrics)
    else:
        metrics["responseMode"] = "not_ready"
        yield TurnResult(*failure("not_ready", request_id), None, metrics)
