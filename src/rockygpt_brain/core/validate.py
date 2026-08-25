"""A plan is checked here before anything runs.

The model writes the plan; this decides whether Rocky will act on it. A plan is
kept only when the lane has what it needs and every field it names is one the
capability allows. Time words are resolved here too, against the campus clock,
because dates are deterministic and so belong in Python.

Two deliberate asymmetries:

Fields belonging to another lane are dropped rather than rejected. A sound RAG
plan that also carries a stray capability is still a sound RAG plan.

An unknown field is rejected outright. It is the one signal that the model
invented something, and guessing what it meant is how a taxonomy starts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from rockygpt_brain.core.capabilities import CAPABILITIES
from rockygpt_brain.core.plan import Filter, Lane, Plan

_DAYS = {"today": 0, "tomorrow": 1, "yesterday": -1}


@dataclass(frozen=True, slots=True)
class Rejected:
    """Why a plan will not be run."""

    reason: str


def check(plan: Plan, now: datetime) -> Plan | Rejected:
    """Return the plan Rocky will run, or why it will run nothing."""
    if plan.lane is Lane.CODE:
        return _check_code(plan, now)
    if plan.lane is Lane.RAG:
        if not plan.topic:
            return Rejected("a RAG plan needs a topic")
        return Plan(lane=Lane.RAG, topic=plan.topic)
    if plan.lane is Lane.MEMORY:
        if not plan.query:
            return Rejected("a MEMORY plan needs a query")
        return Plan(lane=Lane.MEMORY, query=plan.query)
    if plan.lane is Lane.GENERAL:
        # Absent means stable. Searching the web is the exceptional path, so a
        # planner that says nothing about freshness does not trigger one.
        if plan.freshness != "current":
            return Plan(lane=Lane.GENERAL, freshness="stable")
        if not plan.query:
            return Rejected("a current answer needs a query to look up")
        return Plan(lane=Lane.GENERAL, freshness="current", query=plan.query)
    return Plan(lane=plan.lane)


def _check_code(plan: Plan, now: datetime) -> Plan | Rejected:
    capability = CAPABILITIES.get(plan.capability or "")
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

    # A capability says what to look in; the operation says what to do with what
    # is found. A plan with one and not the other is half-written, and running
    # it means guessing the missing half.
    if not operation.stated:
        return Rejected(f"a {plan.capability} plan needs an operation")

    return Plan(
        lane=Lane.CODE,
        capability=plan.capability,
        filters=[Filter(field=f.field, value=resolve(f.value, now)) for f in plan.filters],
        operation=operation,
    )


def resolve(value: str, now: datetime) -> str:
    """A time word becomes a date or an instant. Anything else is left alone."""
    word = value.strip().casefold()
    if word == "now":
        return now.isoformat()
    if word in _DAYS:
        return (now + timedelta(days=_DAYS[word])).date().isoformat()
    return value
