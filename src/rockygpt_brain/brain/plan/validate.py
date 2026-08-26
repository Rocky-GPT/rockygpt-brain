from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from rockygpt_brain.brain.plan.schema import Filter, Lane, Plan
from rockygpt_brain.capabilities.registry import capability_for

_DAYS = {"today": 0, "tomorrow": 1, "yesterday": -1}

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
        if item.field not in capability.filters:
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
        filters=[Filter(field=f.field, value=resolve(f.value, now)) for f in plan.filters],
        operation=operation,
    )


def resolve(value: str, now: datetime) -> str:
    word = value.strip().casefold()
    if word == "now":
        return now.isoformat()
    if word in _DAYS:
        return (now + timedelta(days=_DAYS[word])).date().isoformat()
    return value
