"""Properties from `spec/brain-contract.md`.

These assert invariants, not answers. No question, entity, or expected answer
from any evaluation suite appears here: a test that named one would only prove
that a case had been special-cased.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from rockygpt_brain.core.capabilities import CAPABILITIES, Ordering, can_report_success
from rockygpt_brain.core.compilation import (
    EXTREMAL_RELATIONS,
    CompiledPlan,
    PostSelect,
    compile_task,
    resolve_time,
    validate_registry,
)
from rockygpt_brain.core.interpretation import (
    CARDINALITY_OF_RELATION,
    MEASUREMENT_RELATIONS,
    Anaphor,
    Cardinality,
    Domain,
    Mention,
    OrderSemantic,
    Relation,
    ShuttleTask,
    TimeNamed,
    TimeNow,
    TimeOffset,
)
from rockygpt_brain.core.outcomes import (
    AbsenceCause,
    Absent,
    CardinalityViolation,
    Clarify,
    Error,
    Success,
    Unavailable,
    Withheld,
    WithheldCause,
    precedence,
    seal,
    strongest,
)
from rockygpt_brain.core.selection import (
    EXTREMAL_DIRECTION,
    is_complete,
    select_extremal,
)

TZ = ZoneInfo("America/New_York")
NOW = datetime(2031, 3, 6, 18, 30, tzinfo=UTC)


def shuttle(relation: Relation, **overrides: object) -> ShuttleTask:
    fields: dict[str, object] = {
        "domain": Domain.SHUTTLE,
        "operation": "read",
        "access": "public",
        "relation": relation,
        "cardinality": CARDINALITY_OF_RELATION[relation],
        "route": None,
        "origin": None,
        "destination": None,
        "time": TimeNow(kind="now"),
    }
    fields.update(overrides)
    return ShuttleTask.model_validate(fields)


# --- section 11: declarations -------------------------------------------------


def test_registry_is_self_consistent() -> None:
    validate_registry()


def test_extremal_relations_require_a_declared_ordering() -> None:
    for domain, capability in CAPABILITIES.items():
        if EXTREMAL_RELATIONS & set(capability.relations):
            assert capability.orderings, f"{domain.value} orders nothing"


def test_undeclared_relations_are_not_executable() -> None:
    """A relation absent from the declaration is `no_capability`, not a guess."""

    for capability in CAPABILITIES.values():
        for relation in Relation:
            if relation in capability.relations:
                continue
            assert relation not in capability.relations


# --- section 3: the Listener emits no execution -------------------------------


def test_no_task_field_carries_a_weekday() -> None:
    """Contract 3.1: weekday names are a resolution product, never interpreted."""

    weekdays = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}
    for capability in CAPABILITIES.values():
        assert "day" not in capability.constraints
        for parameter in capability.constraints.values():
            assert parameter.lower() not in weekdays


def test_time_is_required_on_time_bound_tasks() -> None:
    """The field whose optionality produced widening cannot be omitted."""

    with pytest.raises(ValidationError):
        ShuttleTask.model_validate(
            {
                "domain": Domain.SHUTTLE,
                "operation": "read",
                "access": "public",
                "relation": Relation.NEXT,
                "cardinality": Cardinality.ONE,
                "route": None,
                "origin": None,
                "destination": None,
            }
        )


def test_cardinality_is_derivable_from_every_relation() -> None:
    assert set(CARDINALITY_OF_RELATION) == set(Relation)


def test_incoherent_cardinality_is_a_defect_not_a_question() -> None:
    task = shuttle(Relation.NEXT, cardinality=Cardinality.MANY)
    result = compile_task(task, NOW, TZ)
    assert isinstance(result, Error)


# --- section 5: compilation is total and fails closed -------------------------


def test_weekday_is_computed_in_the_campus_zone() -> None:
    """Python owns the calendar. Checked against the calendar, not a fixture."""

    for offset in range(0, 400, 17):
        moment = NOW + timedelta(days=offset)
        window = resolve_time(TimeNamed(kind="named", name="today"), moment, TZ)
        assert window is not None
        assert window.day_name == moment.astimezone(TZ).strftime("%A")
        assert window.service_date == moment.astimezone(TZ).date()


def test_named_days_shift_by_whole_days() -> None:
    base = resolve_time(TimeNamed(kind="named", name="today"), NOW, TZ)
    ahead = resolve_time(TimeNamed(kind="named", name="tomorrow"), NOW, TZ)
    behind = resolve_time(TimeNamed(kind="named", name="yesterday"), NOW, TZ)
    assert base and ahead and behind
    assert ahead.service_date - base.service_date == timedelta(days=1)
    assert base.service_date - behind.service_date == timedelta(days=1)


def test_offsets_move_the_instant_and_may_cross_a_day() -> None:
    window = resolve_time(TimeOffset(kind="offset", minutes=45), NOW, TZ)
    anchor = resolve_time(TimeNow(kind="now"), NOW, TZ)
    assert window is not None and anchor is not None
    assert window.instant - anchor.instant == timedelta(minutes=45)
    assert window.anchored


def test_multi_day_names_are_unsupported_not_narrowed() -> None:
    """A span no transport expresses fails; it is never collapsed to one day."""

    assert resolve_time(TimeNamed(kind="named", name="this_week"), NOW, TZ) is None
    result = compile_task(
        shuttle(Relation.NEXT, time=TimeNamed(kind="named", name="weekend")), NOW, TZ
    )
    assert isinstance(result, Absent)
    assert result.cause is AbsenceCause.NO_CAPABILITY


def test_extremal_relations_are_unsupported_without_a_declared_order() -> None:
    """Not a transport question: a domain with no total order has no extremum."""

    for domain, capability in CAPABILITIES.items():
        if capability.orderings:
            continue
        assert not (EXTREMAL_RELATIONS & set(capability.relations)), domain.value


def test_an_extremum_is_computed_where_the_transport_lacks_a_selector() -> None:
    """Contract 6.2: ordering is declared by the capability, computed by the Worker."""

    capability = CAPABILITIES[Domain.SHUTTLE]
    assert Relation.LATEST in capability.relations
    assert not capability.relations[Relation.LATEST].resolves_cardinality

    plan = compile_task(shuttle(Relation.LATEST), NOW, TZ)
    assert isinstance(plan, CompiledPlan)
    assert isinstance(plan.post_select, PostSelect)
    assert plan.post_select.direction == "descending"
    assert plan.post_select.ordering is capability.orderings[OrderSemantic.DEPARTURE_TIME]


def test_a_computed_extremum_asks_for_the_whole_set() -> None:
    """A relation defined over all records must fetch as many as it can."""

    plan = compile_task(shuttle(Relation.LATEST), NOW, TZ)
    assert isinstance(plan, CompiledPlan)
    assert plan.body["limit"] == CAPABILITIES[Domain.SHUTTLE].max_limit


def test_every_single_result_relation_is_resolved_before_the_writer() -> None:
    """Either the transport selects, or the Worker does. Never the Writer."""

    for domain, capability in CAPABILITIES.items():
        for relation, plan in capability.relations.items():
            if CARDINALITY_OF_RELATION[relation] is not Cardinality.ONE:
                continue
            if relation in MEASUREMENT_RELATIONS:
                continue
            resolved = plan.resolves_cardinality or (
                relation in EXTREMAL_DIRECTION and bool(capability.orderings)
            )
            assert resolved, f"{domain.value}.{relation.value} reaches the Writer unresolved"


def test_an_anaphor_asks_rather_than_guesses() -> None:
    task = shuttle(Relation.NEXT, origin=Anaphor(kind="anaphor", target="prior_subject"))
    result = compile_task(task, NOW, TZ)
    assert isinstance(result, Clarify)
    assert result.missing == ["origin"]


def test_a_mention_reaches_transport_only_through_its_declared_role() -> None:
    task = shuttle(Relation.NEXT, destination=Mention(kind="mention", text="somewhere"))
    plan = compile_task(task, NOW, TZ)
    assert isinstance(plan, CompiledPlan)
    assert plan.body["destination"] == "somewhere"
    assert "somewhere" not in plan.body.get("route", "")


def test_relation_alone_determines_the_transport_selector() -> None:
    """No model-supplied value reaches `selection`."""

    for relation, declared in CAPABILITIES[Domain.SHUTTLE].relations.items():
        plan = compile_task(shuttle(relation), NOW, TZ)
        assert isinstance(plan, CompiledPlan)
        assert plan.body["selection"] == declared.transport["selection"]


def test_scope_follows_anchoring_for_the_open_relation() -> None:
    anchored = compile_task(shuttle(Relation.ALL, time=TimeNow(kind="now")), NOW, TZ)
    whole_day = compile_task(
        shuttle(Relation.ALL, time=TimeNamed(kind="named", name="today")), NOW, TZ
    )
    assert isinstance(anchored, CompiledPlan) and isinstance(whole_day, CompiledPlan)
    assert anchored.body["timeScope"] == "remaining"
    assert whole_day.body["timeScope"] == "full_day"


def test_compilation_never_returns_an_unresolved_temporal_value() -> None:
    plan = compile_task(shuttle(Relation.NEXT), NOW, TZ)
    assert isinstance(plan, CompiledPlan)
    emitted = " ".join(str(value) for value in plan.body.values())
    for token in ("today", "tomorrow", "now", "yesterday"):
        assert token not in emitted.lower()
    date.fromisoformat(plan.body["serviceDate"])
    datetime.fromisoformat(plan.body["asOf"])


# --- section 7: absence, and the measured zero --------------------------------


def test_absence_causes_are_distinct_per_capability() -> None:
    """`entity_no_match` may never share a cause with a no-rows reason."""

    for domain, capability in CAPABILITIES.items():
        if "entity_no_match" not in capability.absence_map:
            continue
        entity_cause = capability.absence_map["entity_no_match"]
        assert entity_cause is AbsenceCause.ENTITY_UNKNOWN, domain.value
        others = {
            cause for reason, cause in capability.absence_map.items() if reason != "entity_no_match"
        }
        assert AbsenceCause.ENTITY_UNKNOWN not in others, domain.value


def test_success_with_no_records_requires_a_measurement_relation() -> None:
    for relation in Relation:
        candidate = Success(
            relation=relation,
            cardinality=CARDINALITY_OF_RELATION[relation],
            records=[],
            value=0 if relation in MEASUREMENT_RELATIONS else None,
        )
        if relation in MEASUREMENT_RELATIONS:
            assert seal(candidate).outcome == "success"
        else:
            with pytest.raises(CardinalityViolation):
                seal(candidate)


def test_a_measured_zero_is_success_not_absence() -> None:
    sealed = seal(Success(relation=Relation.COUNT, cardinality=Cardinality.ONE, value=0))
    assert isinstance(sealed, Success)
    assert sealed.value == 0


def test_cardinality_one_cannot_be_sealed_with_a_list() -> None:
    with pytest.raises(CardinalityViolation):
        seal(
            Success(
                relation=Relation.NEXT,
                cardinality=Cardinality.ONE,
                records=[{"a": 1}, {"a": 2}],
            )
        )


# --- section 6.1: composition cannot upgrade ----------------------------------


def test_composition_never_upgrades_to_success() -> None:
    weak = [
        Absent(cause=AbsenceCause.NO_SUPPORTING_EVIDENCE),
        Clarify(missing=["subject"]),
        Withheld(cause=WithheldCause.POLICY_PERSONAL, text="."),
        Unavailable(dependency="data"),
        Error(code="x"),
    ]
    good = Success(relation=Relation.ALL, cardinality=Cardinality.MANY, records=[{"a": 1}])
    for other in weak:
        assert strongest([good, other]).outcome != "success"
        assert strongest([other, good]).outcome != "success"


def test_composition_is_success_only_when_every_input_is() -> None:
    good = Success(relation=Relation.ALL, cardinality=Cardinality.MANY, records=[{"a": 1}])
    assert strongest([good, good]).outcome == "success"


def test_precedence_is_a_total_order() -> None:
    every = [
        Success(relation=Relation.ALL, cardinality=Cardinality.MANY, records=[{"a": 1}]),
        Absent(cause=AbsenceCause.NO_CAPABILITY),
        Clarify(missing=["x"]),
        Withheld(cause=WithheldCause.POLICY_WRITE, text="."),
        Unavailable(dependency="d"),
        Error(code="c"),
    ]
    ranks = [precedence(outcome) for outcome in every]
    assert ranks == sorted(ranks)
    assert len(set(ranks)) == len(ranks)


# --- section 6.2: completeness gates a computed extremum ----------------------


ORDER = Ordering(field="at.time", kind="time")


def test_completeness_must_be_asserted_positively() -> None:
    """Silence is not a guarantee, so an unreadable block reads as incomplete."""

    assert is_complete({"truncated": False, "state": "complete", "matched": 3, "returned": 3})
    assert not is_complete({"truncated": True, "state": "partial"})
    assert not is_complete({"state": "complete", "matched": 9, "returned": 3})
    assert not is_complete({})
    assert not is_complete(None)


def test_extremal_selection_reads_the_declared_field_only() -> None:
    records = [
        {"id": "b", "at": {"time": "9:40 PM"}},
        {"id": "a", "at": {"time": "7:00 AM"}},
        {"id": "c", "at": {"time": "1:15 PM"}},
    ]
    assert select_extremal(records, ORDER, "ascending")[0]["id"] == "a"
    assert select_extremal(records, ORDER, "descending")[0]["id"] == "b"


def test_extremal_selection_returns_exactly_one_record() -> None:
    records = [{"id": str(n), "at": {"time": f"{n}:00 AM"}} for n in range(1, 10)]
    for direction in ("ascending", "descending"):
        assert len(select_extremal(records, ORDER, direction)) == 1  # type: ignore[arg-type]


def test_extremal_selection_ignores_records_missing_the_ordered_field() -> None:
    records = [{"id": "x"}, {"id": "y", "at": {"time": "8:00 AM"}}]
    assert select_extremal(records, ORDER, "descending")[0]["id"] == "y"
    assert select_extremal([{"id": "x"}], ORDER, "ascending") == []


def test_every_extremal_relation_has_a_direction() -> None:
    for capability in CAPABILITIES.values():
        for relation in capability.relations:
            if relation is Relation.CURRENT or relation not in EXTREMAL_RELATIONS:
                continue
            assert relation in EXTREMAL_DIRECTION


# --- section 6.3: quantity is not support -------------------------------------


def test_a_ranking_domain_cannot_report_success_without_a_declared_floor() -> None:
    """Undeclared support test means no success, as with an undeclared relation."""

    documents = CAPABILITIES[Domain.DOCUMENTS]
    assert documents.evidence_floor is None
    assert not can_report_success(documents)
    assert documents.default_absence is AbsenceCause.NO_SUPPORTING_EVIDENCE


def test_non_ranking_domains_are_unaffected_by_the_floor_rule() -> None:
    for domain, capability in CAPABILITIES.items():
        if domain is Domain.DOCUMENTS:
            continue
        assert can_report_success(capability)
