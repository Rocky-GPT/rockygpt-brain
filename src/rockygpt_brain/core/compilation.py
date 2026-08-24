"""Compilation: Interpretation into deterministic operations. Contract section 5.

Every step fails closed. Nothing here substitutes a default, broadens a query, or
falls back to a neighbouring capability, because each of those turns an
under-specified request into a large result set that some later stage has to
choose from.

All temporal arithmetic lives in this module. No weekday, relative phrase, or
duration survives compilation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from rockygpt_brain.core.capabilities import CAPABILITIES, Capability, Ordering
from rockygpt_brain.core.interpretation import (
    CARDINALITY_OF_RELATION,
    Anaphor,
    Cardinality,
    Domain,
    Mention,
    OrderSemantic,
    Reference,
    Relation,
    Task,
    TimeAbsolute,
    TimeNamed,
    TimeNow,
    TimeOffset,
    TimeReference,
)
from rockygpt_brain.core.outcomes import AbsenceCause, Absent, Clarify, Error, Outcome
from rockygpt_brain.core.selection import EXTREMAL_DIRECTION, Direction

#: Relations that require a total order to mean anything.
EXTREMAL_RELATIONS: frozenset[Relation] = frozenset(
    {Relation.EARLIEST, Relation.LATEST, Relation.NEXT, Relation.CURRENT}
)

#: Named times that denote more than one day. No current transport expresses a
#: multi-day window, so they compile to `no_capability` rather than to a guess
#: about which day was meant.
_MULTI_DAY_NAMES = frozenset({"this_week", "next_week", "weekend"})


@dataclass(frozen=True, slots=True)
class ResolvedTime:
    """Absolute values produced here, never carried in from the Listener."""

    instant: datetime
    service_date: date
    day_name: str
    #: True when the request is anchored to a moment, False when it spans a day.
    anchored: bool

    def as_json(self) -> dict[str, Any]:
        return {
            "instant": self.instant.isoformat(),
            "serviceDate": self.service_date.isoformat(),
            "dayName": self.day_name,
            "anchored": self.anchored,
        }


@dataclass(frozen=True, slots=True)
class PostSelect:
    """Selection the Worker performs after fetching, over a complete result set.

    Present only when the transport cannot resolve the relation itself. The
    completeness requirement is not optional: an extremum over a truncated set
    is a guess, so the executor reports `incomplete_source` instead.
    """

    ordering: Ordering
    direction: Direction


@dataclass(frozen=True, slots=True)
class CompiledPlan:
    capability: Capability
    relation: Relation
    cardinality: Cardinality
    params: dict[str, str] = field(default_factory=dict)
    body: dict[str, Any] = field(default_factory=dict)
    resolved: dict[str, Any] = field(default_factory=dict)
    post_select: PostSelect | None = None


class RegistryError(Exception):
    """A capability declaration that cannot be honoured."""


def validate_registry() -> None:
    """Conformance check on the declarations themselves. Contract section 11.

    An extremal relation without a declared ordering would have to be resolved by
    position in an arbitrary response, which is the defect the ordering
    declaration exists to prevent.
    """

    for domain, capability in CAPABILITIES.items():
        extremal = EXTREMAL_RELATIONS & set(capability.relations)
        if extremal and not capability.orderings:
            names = ", ".join(sorted(r.value for r in extremal))
            raise RegistryError(f"{domain.value} declares [{names}] without an ordering")
        if capability.orderings and capability.default_ordering not in capability.orderings:
            raise RegistryError(f"{domain.value} declares orderings with no valid default")
        for relation, plan in capability.relations.items():
            if plan.resolves_cardinality or relation not in EXTREMAL_DIRECTION:
                continue
            if capability.max_limit is None:
                raise RegistryError(
                    f"{domain.value} computes {relation.value} but declares no max_limit"
                )
        if capability.day_parameter and not capability.time_references:
            raise RegistryError(f"{domain.value} declares a day parameter but accepts no time")


def resolve_time(reference: TimeReference, now: datetime, tz: ZoneInfo) -> ResolvedTime | None:
    """Turn a named or stated time into absolute values.

    Returns None when the reference denotes a span no transport can express; the
    caller reports that as `no_capability` rather than narrowing it to one day.
    """

    local = now.astimezone(tz)
    if isinstance(reference, TimeNow):
        return _resolved(local, anchored=True)
    if isinstance(reference, TimeOffset):
        return _resolved(local + timedelta(minutes=reference.minutes), anchored=True)
    if isinstance(reference, TimeAbsolute):
        moment = datetime.combine(reference.date, local.timetz())
        return _resolved(moment, anchored=False)
    if isinstance(reference, TimeNamed):
        if reference.name in _MULTI_DAY_NAMES:
            return None
        shift = {"today": 0, "tomorrow": 1, "yesterday": -1}[reference.name]
        return _resolved(local + timedelta(days=shift), anchored=False)
    raise TypeError(f"unhandled time reference {type(reference).__name__}")


def _resolved(moment: datetime, *, anchored: bool) -> ResolvedTime:
    return ResolvedTime(
        instant=moment,
        service_date=moment.date(),
        day_name=moment.strftime("%A"),
        anchored=anchored,
    )


def compile_task(task: Task, now: datetime, tz: ZoneInfo) -> CompiledPlan | Outcome:
    """Contract section 5, steps 1 through 7, in order."""

    capability = CAPABILITIES.get(Domain(task.domain))
    if capability is None:
        return Absent(cause=AbsenceCause.NO_CAPABILITY)

    if task.relation not in capability.relations:
        return Absent(cause=AbsenceCause.NO_CAPABILITY)

    derived = CARDINALITY_OF_RELATION[task.relation]
    if derived is not task.cardinality:
        # A Listener defect, not a user ambiguity: asking the reader whether they
        # meant one or many would be nonsense. Contract section 3.2.
        return Error(code="incoherent_interpretation")

    resolved: dict[str, Any] = {}
    time_reference = getattr(task, "time", None)
    if time_reference is not None:
        if not capability.time_references:
            return Absent(cause=AbsenceCause.NO_CAPABILITY)
        if time_reference.kind not in capability.time_references:
            return Absent(cause=AbsenceCause.NO_CAPABILITY)
        window = resolve_time(time_reference, now, tz)
        if window is None and not capability.accepts_range:
            return Absent(cause=AbsenceCause.NO_CAPABILITY)
        assert window is not None
        resolved["time"] = window.as_json()
    else:
        window = None

    entities: dict[str, str] = {}
    for role, entity_role in capability.entity_roles.items():
        reference: Reference | None = getattr(task, role, None)
        if reference is None:
            continue
        if isinstance(reference, Anaphor):
            # Turn state is not built yet, so an anaphor cannot be substituted.
            # Asking is the fail-closed reading; guessing a subject from prose is
            # the defect this contract removes.
            return Clarify(missing=[role], pending_request=task.model_dump(mode="json"))
        assert isinstance(reference, Mention)
        entities[entity_role.parameter] = reference.text
    if entities:
        resolved["mentions"] = dict(entities)

    constraints: dict[str, str] = {}
    for key, parameter in capability.constraints.items():
        value = getattr(task, key, None)
        if value is not None:
            constraints[parameter] = str(value)

    if task.order_by is not None and task.order_by not in capability.orderings:
        # A named order the capability does not declare is unsupported, never
        # quietly replaced by the default.
        return Absent(cause=AbsenceCause.NO_CAPABILITY)

    post = _post_select(capability, task.relation, task.order_by)
    if isinstance(post, Absent):
        return post
    if capability.method == "POST" and capability.domain is Domain.SHUTTLE:
        return _shuttle_plan(task, capability, window, entities, resolved, post)
    return _listing_plan(task, capability, window, entities, constraints, resolved, post)


def _post_select(
    capability: Capability,
    relation: Relation,
    order_by: OrderSemantic | None,
) -> PostSelect | Absent | None:
    """Selection the Worker owns, when the transport does not resolve it.

    Where the order is defined is a capability question; where the selection is
    computed is not. A declared ordering is sufficient to compute an extremum
    deterministically, provided the fetched set is complete.
    """

    plan = capability.relations[relation]
    if plan.resolves_cardinality:
        return None
    direction = EXTREMAL_DIRECTION.get(relation)
    if direction is None:
        return None
    semantic = order_by or capability.default_ordering
    ordering = capability.orderings.get(semantic) if semantic else None
    if ordering is None:
        return Absent(cause=AbsenceCause.NO_CAPABILITY)
    return PostSelect(ordering=ordering, direction=direction)


def _listing_plan(
    task: Task,
    capability: Capability,
    window: ResolvedTime | None,
    entities: dict[str, str],
    constraints: dict[str, str],
    resolved: dict[str, Any],
    post: PostSelect | None,
) -> CompiledPlan:
    params: dict[str, str] = {**entities, **constraints}
    if window is not None:
        if capability.day_parameter:
            params[capability.day_parameter] = window.day_name
        if capability.accepts_instant:
            params["at"] = window.instant.isoformat()
    if post is not None and capability.max_limit is not None:
        params["limit"] = str(capability.max_limit)
    return CompiledPlan(
        capability=capability,
        relation=task.relation,
        cardinality=task.cardinality,
        params=params,
        resolved=resolved,
        post_select=post,
    )


def _shuttle_plan(
    task: Task,
    capability: Capability,
    window: ResolvedTime | None,
    entities: dict[str, str],
    resolved: dict[str, Any],
    post: PostSelect | None,
) -> CompiledPlan:
    """Relation selects; the resolved window scopes. Neither is model-supplied."""

    assert window is not None
    transport = dict(capability.relations[task.relation].transport)
    if "timeScope" not in transport:
        # `all` is the only relation whose scope is not fixed by the relation
        # itself: anchored to a moment it means what is still catchable, named as
        # a day it means the whole day.
        transport["timeScope"] = "remaining" if window.anchored else "full_day"
    body: dict[str, Any] = {
        "asOf": window.instant.isoformat(),
        "serviceDate": window.service_date.isoformat(),
        **transport,
        **entities,
    }
    if post is not None and capability.max_limit is not None:
        # A relation computed here is defined over the whole set, so ask for as
        # much of it as the transport will return and check completeness after.
        body["limit"] = capability.max_limit
    return CompiledPlan(
        capability=capability,
        relation=task.relation,
        cardinality=task.cardinality,
        body=body,
        resolved=resolved,
        post_select=post,
    )
