from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from rockygpt_brain.brain.plan.schema import Filter, Lane, Operation, Plan
from rockygpt_brain.capabilities.filters import FilterKind, FilterSpec
from rockygpt_brain.capabilities.registry import capability_for

_DAYS = {"today": 0, "tomorrow": 1, "yesterday": -1}

_STATED = re.compile(r"\s*\bas of\b.*$", re.IGNORECASE)
_CLOCK = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*(?:([AaPp])\.?[Mm]\.?)?$")


@dataclass(frozen=True, slots=True)
class Rejected:
    reason: str


def route(plan: Plan) -> Lane:
    if plan.a_capability_answers_it:
        return Lane.CODE
    return Lane.RAG if plan.specific_to_ramapo else Lane.GENERAL


def check(plan: Plan, now: datetime) -> Plan | Rejected:
    plan = plan.model_copy(update={"lane": route(plan)})
    checked = _lane(plan, now)
    if isinstance(checked, Rejected):
        return checked
    return checked.model_copy(
        update={
            "a_capability_answers_it": plan.a_capability_answers_it,
            "specific_to_ramapo": plan.specific_to_ramapo,
        }
    )


def _lane(plan: Plan, now: datetime) -> Plan | Rejected:
    if plan.safety:
        return Plan(safety=list(plan.safety), lane=plan.lane)
    if plan.lane is Lane.CODE:
        return _check_code(plan, now)
    if plan.lane is Lane.RAG:
        if not plan.topic:
            return Rejected("a RAG plan needs a topic")
        return Plan(lane=Lane.RAG, topic=plan.topic)
    if plan.lane is Lane.GENERAL:
        if plan.freshness != "current":
            return Plan(lane=Lane.GENERAL, freshness="stable")
        if not plan.query:
            return Rejected("a current answer needs a query to look up")
        return Plan(
            lane=Lane.GENERAL,
            freshness="current",
            query=plan.query,
            effective_query=anchor(plan.query, now),
        )
    return Plan(lane=plan.lane)


def anchor(query: str, now: datetime) -> str:
    return f"{_STATED.sub('', query).strip()} as of {now:%Y-%m-%d}"


def _check_code(plan: Plan, now: datetime) -> Plan | Rejected:
    capability = capability_for(plan.capability or "")
    if capability is None:
        return Rejected(f"no capability named {plan.capability!r}")

    normalized = _normalize_filters(plan, capability.filters, now)
    if isinstance(normalized, Rejected):
        return normalized

    operation = plan.operation
    if operation.order_by and operation.order_by not in capability.fields:
        return Rejected(f"{plan.capability} has no field {operation.order_by!r} to sort by")
    for name in operation.compare:
        if name not in capability.fields:
            return Rejected(f"{plan.capability} has no field {name!r} to compare")

    if not operation.stated:
        return Rejected(f"a {plan.capability} plan needs an operation")

    if operation.select and not operation.order_by:
        return Rejected(f"a {plan.capability} plan cannot select one row out of no order")

    return Plan(
        lane=Lane.CODE,
        capability=plan.capability,
        filters=normalized,
        operation=selective(operation),
    )


def selective(operation: Operation) -> Operation:
    """A limit of one asked for a quantity of one; drop it.

    `limit` is how many rows the question asked for and `select` takes the one
    row an ordering already names, so a limit of one is the single value where
    the two are indistinguishable — and it is the one the planner reaches for
    when the question merely reads as singular. "Last day to reg for class"
    planned `limit: 1`, and the answer named the Session I add/drop deadline as
    the last day to register while dropping the two later deadlines that had an
    equal claim to the question.

    Dropping rather than rejecting is the conservative direction: the rows the
    question did not distinguish between all survive, and BRAIN #3 explains the
    distinction it now has the evidence for. A plan that meant to take one
    ordered row says `select`, which is honoured, and both the asked-for and
    the honoured operation stay visible in the trace.
    """
    if operation.limit != 1:
        return operation
    return operation.model_copy(update={"limit": None})


def _normalize_filters(
    plan: Plan, specs: dict[str, FilterSpec], now: datetime
) -> list[Filter] | Rejected:
    """Validate values against their field types and return canonical scalars."""
    seen: set[str] = set()
    normalized: list[Filter] = []
    for item in plan.filters:
        if item.field in seen:
            return Rejected(f"{plan.capability} repeats filter {item.field!r}")
        seen.add(item.field)
        spec = specs.get(item.field)
        if spec is None:
            return Rejected(f"{plan.capability} cannot be filtered by {item.field!r}")
        value = _normalize_value(item.value, spec, now)
        if value is None:
            expected = spec.kind.value
            if spec.values:
                expected = f"one of {', '.join(sorted(spec.values))}"
            return Rejected(
                f"{plan.capability}.{item.field} expects {expected}, received {item.value!r}"
            )
        normalized.append(Filter(field=item.field, value=value))
    return normalized


def _normalize_value(value: str, spec: FilterSpec, now: datetime) -> str | None:
    stripped = value.strip()
    if not stripped:
        return None
    if spec.kind is FilterKind.ENUM:
        return spec.enum_value(stripped)
    if spec.kind is FilterKind.DATE:
        resolved = resolve(stripped, now)
        try:
            return date.fromisoformat(resolved).isoformat()
        except ValueError:
            return None
    if spec.kind is FilterKind.INSTANT:
        return _instant(stripped, now)
    return stripped


def _instant(value: str, now: datetime) -> str | None:
    resolved = resolve(value, now)
    try:
        parsed = datetime.fromisoformat(resolved.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
    if parsed is not None:
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=now.tzinfo)
        return parsed.isoformat()

    match = _CLOCK.fullmatch(value)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    half = match.group(3)
    if half:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if half.casefold() == "p" else 0)
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return None
    return datetime.combine(now.date(), time(hour, minute), tzinfo=now.tzinfo).isoformat()


def resolve(value: str, now: datetime) -> str:
    word = value.strip().casefold()
    if word == "now":
        return now.isoformat()
    if word in _DAYS:
        return (now + timedelta(days=_DAYS[word])).date().isoformat()
    return value
