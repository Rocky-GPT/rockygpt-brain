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

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from rockygpt_brain.brain.plan.schema import Filter, Lane, Plan
from rockygpt_brain.capabilities.registry import capability_for

_DAYS = {"today": 0, "tomorrow": 1, "yesterday": -1}

#: A date the planner stated in the query despite being asked not to. Told the
#: current time and asked for a search, it copies the date in about a third of
#: the time — so Python takes it back out rather than leaving two.
_STATED = re.compile(r"\s*\bas of\b.*$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Rejected:
    """Why a plan will not be run."""

    reason: str


def route(plan: Plan) -> Lane:
    """Where the answer comes from, from the two questions the planner answered.

    The cascade, and the whole of it. It stops at the first hit, which is why
    `specific_to_ramapo` is not consulted once a capability fits — the question
    was never reached, and what the planner put there is noise.
    """
    if plan.a_capability_answers_it:
        return Lane.CODE
    return Lane.RAG if plan.specific_to_ramapo else Lane.GENERAL


def check(plan: Plan, now: datetime) -> Plan | Rejected:
    """Return the plan Rocky will run, or why it will run nothing.

    Every branch below rebuilds the plan from the fields its lane uses, which
    is how a field that belongs to no lane goes missing. The two judgements
    behind the lane belong to none of them, so they are stamped back on here —
    once, where no new branch can forget.
    """
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
    """What the lane itself needs, and whether it has it."""
    # Safety first, and alone. Every branch below rebuilds the plan from
    # scratch and would drop the flag; more than that, every branch below can
    # reject, and a rejected plan ends the turn. The one turn that must never
    # end that way is this one, so it does not pass through them at all.
    if plan.safety:
        return Plan(safety=list(plan.safety), lane=plan.lane)
    if plan.lane is Lane.CODE:
        return _check_code(plan, now)
    if plan.lane is Lane.RAG:
        if not plan.topic:
            return Rejected("a RAG plan needs a topic")
        return Plan(lane=Lane.RAG, topic=plan.topic)
    if plan.lane is Lane.GENERAL:
        # Absent means stable. Searching the web is the exceptional path, so a
        # planner that says nothing about freshness does not trigger one.
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
    """The query Python searches with: what BRAIN #2 meant, plus today's date.

    The division is the point. BRAIN #2 writes the meaning of the search and
    nothing else; the date comes from the server clock, every time, with no
    condition on it. Left to the planner the date appeared about four times in
    five — the worst possible rate, frequent enough to look correct and rare
    enough that the turns it missed looked like nothing in particular.

    Unconditional on purpose. A rule that dates a query only sometimes is a
    rule that has to be right about when, and reading the model's own wording
    to decide would put the model back in charge of the thing it was unreliable
    at. Same reason Python resolves `today`: the clock is deterministic, so it
    belongs here rather than in a prompt.

    A date the planner wrote anyway is removed first. That is not the model
    deciding anything — it is this owning the date on both ends, so the query
    carries exactly one and it is the server's. Words of meaning are untouched:
    "current price of gold" is what the search is for, and stays.
    """
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
