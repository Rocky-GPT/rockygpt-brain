"""Declared capabilities. Contract section 11.

A domain is executable only through a declaration here. Anything undeclared is
`absent { no_capability }` — never a widened query and never a nearby endpoint.
Adding a domain means adding a declaration, not adding a branch.

Three declarations carry most of the weight:

* `relations` maps a semantic relation to how it resolves. Membership is the
  support test; a relation absent from the map is not supported.
* `ordering` names the total order extremal relations are computed against.
  Without one, `earliest` and `latest` are `no_capability` rather than a guess
  at what "last" could mean for these records.
* `evidence_floor` is the support test for retrieval-backed domains. Undeclared
  means the domain cannot report success at all, for the same reason an
  undeclared relation cannot run: an undeclared guarantee is not a guarantee.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from rockygpt_brain.core.interpretation import Domain, OrderSemantic, Relation
from rockygpt_brain.core.outcomes import AbsenceCause

#: TimeReference kinds a capability can accept, by `kind` discriminator.
TimeKind = Literal["now", "named", "offset", "absolute"]


@dataclass(frozen=True, slots=True)
class Ordering:
    """One total order: the field carrying it, and how to read that field.

    Domain meaning lives here rather than in the model or in the transport. A
    capability may declare several — a schedule can be ordered by when a trip
    leaves or by when it arrives — and the request names which one by meaning.
    """

    field: str
    kind: Literal["time", "date", "name", "number"]


@dataclass(frozen=True, slots=True)
class RelationPlan:
    """How one relation is resolved.

    `transport` carries the parameters that reach DATA. `resolves_cardinality`
    says whether the transport itself returns the selected record; when it does
    not, the Worker computes the selection from a complete result set.
    """

    transport: dict[str, str] = field(default_factory=dict)
    resolves_cardinality: bool = False


@dataclass(frozen=True, slots=True)
class EntityRole:
    """A role a mention may fill, and how its resolution failure is reported.

    `reported` means the transport distinguishes "this name matched nothing" from
    "this name matched but no record qualifies". `unreported` means it does not,
    and the Worker must then take the conservative reading: an empty result for a
    request carrying a mention is `entity_unknown`, because claiming the entity
    does not exist would be an assertion the data never made.
    """

    parameter: str
    resolution: Literal["reported", "unreported"]


@dataclass(frozen=True, slots=True)
class Capability:
    domain: Domain
    path: str
    method: Literal["GET", "POST"]
    relations: dict[Relation, RelationPlan]
    entity_roles: dict[str, EntityRole] = field(default_factory=dict)
    #: semantic constraint key -> transport parameter.
    constraints: dict[str, str] = field(default_factory=dict)
    time_references: frozenset[TimeKind] = frozenset()
    #: semantic -> the field that carries it. Empty means no extremum exists.
    orderings: dict[OrderSemantic, Ordering] = field(default_factory=dict)
    #: Used when a request names no order. Must be a declared semantic.
    default_ordering: OrderSemantic | None = None
    measurement_relations: frozenset[Relation] = frozenset()
    #: transport-reported reason -> contract cause.
    absence_map: dict[str, AbsenceCause] = field(default_factory=dict)
    default_absence: AbsenceCause = AbsenceCause.NO_QUALIFYING_RECORDS
    #: Relevance floor. Undeclared means this domain cannot report success.
    evidence_floor: float | None = None
    #: Whether the transport takes an `at` instant.
    accepts_instant: bool = True
    #: Transport parameter carrying a weekday, filled from resolved time only.
    #: The Listener never emits a weekday; contract section 3.1.
    day_parameter: str | None = None
    #: Whether the transport can express a multi-day window.
    accepts_range: bool = False
    #: Largest page the transport will return, requested when the Worker must
    #: see a complete set to compute a relation over it.
    max_limit: int | None = None


_SEARCH_ABSENCE: dict[str, AbsenceCause] = {
    "dataset_empty": AbsenceCause.NO_QUALIFYING_RECORDS,
    "entity_no_match": AbsenceCause.ENTITY_UNKNOWN,
    "no_remaining": AbsenceCause.NO_QUALIFYING_RECORDS,
    "not_current": AbsenceCause.NO_QUALIFYING_RECORDS,
}

_LISTING_RELATIONS: dict[Relation, RelationPlan] = {
    Relation.ALL: RelationPlan(),
    Relation.DESCRIBE: RelationPlan(),
    Relation.EXISTS: RelationPlan(),
}


def _listing(path: str, **kwargs: object) -> Capability:
    """A domain DATA exposes as an unordered listing.

    No ordering is declared, so extremal relations are unsupported rather than
    approximated by position in the response.
    """

    return Capability(
        path=path,
        method="GET",
        relations=dict(_LISTING_RELATIONS),
        measurement_relations=frozenset({Relation.EXISTS}),
        absence_map=dict(_SEARCH_ABSENCE),
        **kwargs,  # type: ignore[arg-type]
    )


CAPABILITIES: dict[Domain, Capability] = {
    Domain.CAMPUS_HOURS: _listing(
        "/v1/search/campus-hours",
        domain=Domain.CAMPUS_HOURS,
        entity_roles={"subject": EntityRole("q", "unreported")},
        day_parameter="day",
        time_references=frozenset({"now", "named", "offset", "absolute"}),
    ),
    Domain.DINING_HOURS: _listing(
        "/v1/search/dining-hours",
        domain=Domain.DINING_HOURS,
        entity_roles={"subject": EntityRole("q", "unreported")},
        day_parameter="day",
        time_references=frozenset({"now", "named", "offset", "absolute"}),
    ),
    Domain.MENU: _listing(
        "/v1/search/menu",
        domain=Domain.MENU,
        entity_roles={"subject": EntityRole("q", "unreported")},
        constraints={"meal": "meal"},
        time_references=frozenset({"now", "named", "offset", "absolute"}),
    ),
    Domain.EVENTS: _listing(
        "/v1/search/events",
        domain=Domain.EVENTS,
        entity_roles={"subject": EntityRole("q", "unreported")},
        time_references=frozenset({"now", "named", "offset", "absolute"}),
    ),
    Domain.ACADEMIC_DATES: _listing(
        "/v1/search/academic-dates",
        domain=Domain.ACADEMIC_DATES,
        entity_roles={"subject": EntityRole("q", "unreported")},
        time_references=frozenset({"now", "named", "offset", "absolute"}),
    ),
    Domain.CONTACTS: _listing(
        "/v1/search/contacts",
        domain=Domain.CONTACTS,
        entity_roles={"subject": EntityRole("q", "unreported")},
    ),
    Domain.CLUBS: _listing(
        "/v1/search/clubs",
        domain=Domain.CLUBS,
        entity_roles={"subject": EntityRole("q", "unreported")},
    ),
    Domain.PROGRAMS: _listing(
        "/v1/search/programs",
        domain=Domain.PROGRAMS,
        entity_roles={"subject": EntityRole("q", "unreported")},
    ),
    Domain.MAP: Capability(
        domain=Domain.MAP,
        path="/v1/map",
        method="GET",
        relations=dict(_LISTING_RELATIONS),
        entity_roles={"subject": EntityRole("q", "unreported")},
        measurement_relations=frozenset({Relation.EXISTS}),
        absence_map=dict(_SEARCH_ABSENCE),
        accepts_instant=False,
    ),
    # Shuttle declares a total order, so every extremal relation is available.
    # `first`, `next` and `current` are resolved by the transport, which returns
    # the selected record itself. `latest` has no transport selector, so the
    # Worker computes it from a complete result set — the same generic path any
    # domain declaring an ordering would take.
    Domain.SHUTTLE: Capability(
        domain=Domain.SHUTTLE,
        path="/v2/capabilities/shuttle/query",
        method="POST",
        relations={
            Relation.EARLIEST: RelationPlan(
                {"selection": "first", "timeScope": "full_day"}, resolves_cardinality=True
            ),
            Relation.NEXT: RelationPlan(
                {"selection": "next", "timeScope": "remaining"}, resolves_cardinality=True
            ),
            Relation.CURRENT: RelationPlan(
                {"selection": "current", "timeScope": "at_time"}, resolves_cardinality=True
            ),
            Relation.LATEST: RelationPlan({"selection": "all"}),
            Relation.ALL: RelationPlan({"selection": "all"}),
        },
        entity_roles={
            "route": EntityRole("route", "reported"),
            "origin": EntityRole("origin", "reported"),
            "destination": EntityRole("destination", "reported"),
        },
        time_references=frozenset({"now", "named", "offset", "absolute"}),
        # Departure time is what this transport reports reliably today. Arrival
        # ordering is a second entry here when DATA exposes the field, not a
        # different code path.
        orderings={OrderSemantic.DEPARTURE_TIME: Ordering("matchedOrigin.time", "time")},
        default_ordering=OrderSemantic.DEPARTURE_TIME,
        max_limit=100,
        absence_map={
            "dataset_empty": AbsenceCause.NO_QUALIFYING_RECORDS,
            "entity_no_match": AbsenceCause.ENTITY_UNKNOWN,
            "no_remaining": AbsenceCause.NO_QUALIFYING_RECORDS,
            "not_current": AbsenceCause.NO_QUALIFYING_RECORDS,
        },
    ),
    Domain.DOCUMENTS: Capability(
        domain=Domain.DOCUMENTS,
        path="/v2/retrieve",
        method="POST",
        relations={Relation.DESCRIBE: RelationPlan()},
        default_absence=AbsenceCause.NO_SUPPORTING_EVIDENCE,
        # Deliberately unset. DATA reports retrieval success from result count
        # alone, so an ungated lane would let quantity stand in for support.
        # Until a floor is measured, this domain reports
        # `absent { no_supporting_evidence }` rather than manufacturing success.
        evidence_floor=None,
        accepts_instant=False,
    ),
}


def can_report_success(capability: Capability) -> bool:
    """Whether a retrieval-backed domain has a declared support test.

    Domains that do not rank evidence are unaffected; a domain that does, and
    has no floor, cannot distinguish a supporting document from a returned one.
    """

    if capability.domain is not Domain.DOCUMENTS:
        return True
    return capability.evidence_floor is not None


def capability_guide() -> str:
    """The executable surface, rendered for the Listener.

    Generated from the registry so the guide cannot describe a relation the
    Worker will not run.
    """

    lines: list[str] = []
    for domain, capability in CAPABILITIES.items():
        relations = ", ".join(sorted(r.value for r in capability.relations))
        roles = ", ".join(sorted(capability.entity_roles)) or "none"
        keys = ", ".join(sorted(capability.constraints)) or "none"
        order = ", ".join(sorted(o.value for o in capability.orderings)) or "none"
        lines.append(
            f"- {domain.value}: relations [{relations}]; reference roles [{roles}]; "
            f"constraints [{keys}]; ordering [{order}]"
        )
    return "\n".join(lines)
