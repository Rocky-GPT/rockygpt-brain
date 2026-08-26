from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from rockygpt_brain.brain.plan.schema import Filter, Lane, Plan
from rockygpt_brain.capabilities.registry import capability_for

_DAYS = {"today": 0, "tomorrow": 1, "yesterday": -1}
#: The time words that name the moment every lookup already runs at. A filter
#: asking for now, on a lookup that is already now, narrows nothing — which is
#: the only reason one can be dropped instead of honoured. `tomorrow` is not
#: here and must never be: dropping it would answer about today.
_ALREADY = frozenset({"now", "today"})

_STATED = re.compile(r"\s*\bas of\b.*$", re.IGNORECASE)


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

    for item in plan.filters:
        if item.field in capability.filters or _asks_for_now(item):
            continue
        return Rejected(f"{plan.capability} cannot be filtered by {item.field!r}")

    operation = plan.operation
    if operation.order_by and operation.order_by not in capability.fields:
        return Rejected(f"{plan.capability} has no field {operation.order_by!r} to sort by")
    for name in operation.compare:
        if name not in capability.fields:
            return Rejected(f"{plan.capability} has no field {name!r} to compare")

    if not operation.stated:
        return Rejected(f"a {plan.capability} plan needs an operation")

    return Plan(
        lane=Lane.CODE,
        capability=plan.capability,
        filters=dated(plan.filters, capability.temporal, now),
        operation=operation,
    )


def dated(filters: list[Filter], temporal: frozenset[str], now: datetime) -> list[Filter]:
    """Time words turned into dates, but only on fields that hold a time.

    A filter value is read against the clock where the field can hold a time,
    and where it cannot, a filter asking for *now* is dropped.

    Dropping is the only repair `validate` makes, and it is narrow on purpose.
    Every lookup is handed the clock whether or not the question mentions it,
    so `today` on a field that holds no time asks for exactly what is already
    happening: it narrows nothing, and a filter that narrows nothing can go.
    What the planner is doing there is looking for somewhere to put a word, and
    "What's on the menu today?" planned `meal: "today"` three times in three —
    `meal` holds BREAKFAST, LUNCH or DINNER. Dating it produced
    `meal: "2026-08-26"`, a filter matching nothing that reads like it should.

    `tomorrow` is never dropped, because it does narrow: dropping it would
    answer about today under tomorrow's question. It stays, matches nothing,
    and is reported as having matched nothing.
    """
    kept: list[Filter] = []
    for f in filters:
        if f.field in temporal:
            kept.append(Filter(field=f.field, value=resolve(f.value, now)))
        elif not _asks_for_now(f):
            kept.append(f)
    return kept


def _asks_for_now(item: Filter) -> bool:
    """Whether this filter asks for the moment the lookup already runs at."""
    return item.value.strip().casefold() in _ALREADY


def resolve(value: str, now: datetime) -> str:
    word = value.strip().casefold()
    if word == "now":
        return now.isoformat()
    if word in _DAYS:
        return (now + timedelta(days=_DAYS[word])).date().isoformat()
    return value
