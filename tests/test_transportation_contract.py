"""Focused validation tests for the Step 5A shuttle contract."""

from datetime import UTC, date, datetime, time

import pytest
from pydantic import ValidationError

from rockygpt_brain.transportation import (
    AROUND_WINDOW_MINUTES,
    CalendarDay,
    NamedWeekday,
    RelativeDay,
    ResolvedShuttleDay,
    ServiceDayTemplate,
    ShuttleClarificationRequest,
    ShuttleComparisonFact,
    ShuttleComparisonRequest,
    ShuttleProvenance,
    ShuttleQuery,
    ShuttleQueryRequest,
    ShuttleQueryResult,
    ShuttleRequest,
    ShuttleResult,
    ShuttleScheduleSummary,
    ShuttleSource,
    ShuttleStopFact,
    ShuttleTimeConstraint,
    ShuttleTimedFact,
    ShuttleTripFact,
    UnsupportedShuttleRequest,
    UpcomingDay,
)

EVALUATED_AT = datetime(2026, 8, 31, 13, 37, tzinfo=UTC)


def next_query(*, count: int = 1, offset: int = 0) -> ShuttleQuery:
    return ShuttleQuery(
        day=UpcomingDay(kind="upcoming"),
        selection="next",
        count=count,
        offset=offset,
    )


def full_schedule(day: ServiceDayTemplate | RelativeDay) -> ShuttleQuery:
    return ShuttleQuery(day=day, selection="all")


def query_request(query: ShuttleQuery | None = None) -> ShuttleQueryRequest:
    return ShuttleQueryRequest(
        kind="query",
        answer_kind="trips",
        query=query or next_query(),
        show="both",
    )


def source() -> ShuttleSource:
    return ShuttleSource(
        source_id="source-1",
        title="Transportation Services",
        url="https://www.ramapo.edu/about/transportation-services/",
        trust_tier="official_primary",
        freshness_sla_hours=168,
        collected_at=datetime(2026, 8, 28, 21, 22, tzinfo=UTC),
    )


def provenance() -> ShuttleProvenance:
    return ShuttleProvenance(
        dataset_version="v2-20260831175938",
        dataset_activated_at=datetime(2026, 8, 31, 18, 2, tzinfo=UTC),
        source_commit_sha="287949c30a4d586da00a4346ec8f4ce4edb35dd6",
        sources=[source()],
    )


def trip() -> ShuttleTripFact:
    return ShuttleTripFact(
        trip_id="trip-1",
        source_record_key="weekday-roadrunner:0",
        route="Weekday Roadrunner Express",
        service_date=date(2026, 8, 31),
        service_day="weekday",
        departure=ShuttleTimedFact(
            label="7:00 AM", at=datetime(2026, 8, 31, 7, 0, tzinfo=UTC)
        ),
        stops=[
            ShuttleStopFact(
                location="Garden State Plaza",
                time=ShuttleTimedFact(
                    label="7:30 AM", at=datetime(2026, 8, 31, 7, 30, tzinfo=UTC)
                ),
            )
        ],
        arrival=ShuttleTimedFact(
            label="7:55 AM", at=datetime(2026, 8, 31, 7, 55, tzinfo=UTC)
        ),
        minutes_until=23,
        source_id="source-1",
        content_hash="trusted-row-hash",
    )


def query_result(*, records: list[ShuttleTripFact] | None = None) -> ShuttleQueryResult:
    selected = records if records is not None else [trip()]
    return ShuttleQueryResult(
        resolved_day=ResolvedShuttleDay(
            label="today", service_date=date(2026, 8, 31), service_day="weekday"
        ),
        records=selected,
        matched_count=len(selected),
        truncated=False,
    )


def test_next_n_request_has_only_interpretation_not_campus_facts() -> None:
    request = ShuttleRequest.model_validate(
        {
            "kind": "query",
            "answer_kind": "trips",
            "query": {
                "day": {"kind": "upcoming"},
                "selection": "next",
                "count": 3,
                "offset": 0,
                "route_mention": "the train route",
                "origin_mention": "campus",
                "destination_mention": "the station",
            },
            "show": "departure",
        }
    )

    assert isinstance(request.root, ShuttleQueryRequest)
    assert request.root.query.count == 3
    assert request.root.query.route_mention == "the train route"
    assert not hasattr(request.root.query, "route_id")
    assert not hasattr(request.root.query, "trips")


@pytest.mark.parametrize(
    "day",
    [
        RelativeDay(kind="relative", days_from_today=0),
        RelativeDay(kind="relative", days_from_today=1),
        NamedWeekday(kind="named_weekday", weekday="monday"),
        ServiceDayTemplate(kind="service_day", service_day="saturday"),
        CalendarDay(kind="calendar_date", date=date(2026, 9, 6)),
    ],
)
def test_full_schedule_supports_every_bounded_day_shape(
    day: RelativeDay | NamedWeekday | ServiceDayTemplate | CalendarDay,
) -> None:
    request = query_request(ShuttleQuery(day=day, selection="all"))
    assert request.query.day == day
    assert request.query.count is None
    assert request.query.offset == 0


def test_availability_requires_a_timed_all_trips_query() -> None:
    request = ShuttleQueryRequest(
        kind="query",
        answer_kind="availability",
        query=ShuttleQuery(
            day=RelativeDay(kind="relative", days_from_today=0),
            selection="all",
            time=ShuttleTimeConstraint(
                relation="around", clock=time(17, 0), basis="departure"
            ),
        ),
        show="departure",
    )

    assert request.query.time is not None
    assert request.query.time.clock == time(17, 0)
    assert AROUND_WINDOW_MINUTES == 15


def test_comparison_is_exactly_two_full_schedules() -> None:
    request = ShuttleComparisonRequest(
        kind="comparison",
        queries=(
            full_schedule(ServiceDayTemplate(kind="service_day", service_day="saturday")),
            full_schedule(ServiceDayTemplate(kind="service_day", service_day="sunday")),
        ),
        show="both",
    )

    assert len(request.queries) == 2
    assert [query.day.service_day for query in request.queries] == ["saturday", "sunday"]  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "payload",
    [
        {
            "kind": "query",
            "answer_kind": "trips",
            "query": {"day": {"kind": "upcoming"}, "selection": "next"},
            "show": "both",
        },
        {
            "kind": "query",
            "answer_kind": "trips",
            "query": {
                "day": {"kind": "upcoming"},
                "selection": "all",
                "count": None,
            },
            "show": "both",
        },
        {
            "kind": "query",
            "answer_kind": "availability",
            "query": {
                "day": {"kind": "relative", "days_from_today": 0},
                "selection": "all",
            },
            "show": "both",
        },
        {
            "kind": "query",
            "answer_kind": "trips",
            "query": {
                "day": {"kind": "relative", "days_from_today": 0},
                "selection": "all",
                "time": {"relation": "at", "clock": "17:00", "basis": "departure"},
            },
            "show": "both",
        },
    ],
)
def test_request_rejects_incompatible_query_shapes(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ShuttleRequest.model_validate(payload)


def test_request_rejects_unknown_fields_and_second_precision() -> None:
    with pytest.raises(ValidationError):
        ShuttleRequest.model_validate(
            {
                "kind": "query",
                "answer_kind": "availability",
                "query": {
                    "day": {"kind": "relative", "days_from_today": 0},
                    "selection": "all",
                    "time": {
                        "relation": "at",
                        "clock": "17:00:30",
                        "basis": "departure",
                    },
                    "invented_fact": "5:00 PM",
                },
                "show": "both",
            }
        )


def test_last_trip_is_bounded_and_singular() -> None:
    request = ShuttleQuery(
        day=RelativeDay(kind="relative", days_from_today=0),
        selection="last",
        count=1,
    )

    assert request.selection == "last"
    assert request.count == 1

    with pytest.raises(ValidationError):
        ShuttleQuery(
            day=UpcomingDay(kind="upcoming"),
            selection="last",
            count=1,
        )

    with pytest.raises(ValidationError):
        ShuttleQuery(
            day=RelativeDay(kind="relative", days_from_today=0),
            selection="last",
            count=2,
        )


def test_success_result_requires_trusted_records_and_provenance() -> None:
    result = ShuttleResult(
        outcome="success",
        request=query_request(),
        evaluated_at=EVALUATED_AT,
        query_results=[query_result()],
        provenance=provenance(),
    )

    assert result.query_results[0].records[0].route == "Weekday Roadrunner Express"
    assert result.provenance is not None
    assert result.provenance.sources[0].trust_tier == "official_primary"


def test_non_clock_arrival_is_preserved_without_inventing_an_instant() -> None:
    final_trip = trip().model_copy(
        update={"arrival": ShuttleTimedFact(label="End of Service", at=None)}
    )
    result = ShuttleResult(
        outcome="success",
        request=query_request(),
        evaluated_at=EVALUATED_AT,
        query_results=[query_result(records=[final_trip])],
        provenance=provenance(),
    )

    assert result.query_results[0].records[0].arrival.label == "End of Service"
    assert result.query_results[0].records[0].arrival.at is None


def test_executed_result_without_provenance_is_rejected() -> None:
    with pytest.raises(ValidationError, match="provenance"):
        ShuttleResult(
            outcome="success",
            request=query_request(),
            evaluated_at=EVALUATED_AT,
            query_results=[query_result()],
        )


def test_successful_comparison_requires_two_results_and_computed_summary() -> None:
    request = ShuttleComparisonRequest(
        kind="comparison",
        queries=(
            full_schedule(ServiceDayTemplate(kind="service_day", service_day="saturday")),
            full_schedule(ServiceDayTemplate(kind="service_day", service_day="sunday")),
        ),
        show="both",
    )
    summary = ShuttleComparisonFact(
        left=ShuttleScheduleSummary(
            label="Saturday",
            trip_count=12,
            first_departure_at=EVALUATED_AT,
            last_departure_at=EVALUATED_AT,
        ),
        right=ShuttleScheduleSummary(
            label="Sunday",
            trip_count=9,
            first_departure_at=EVALUATED_AT,
            last_departure_at=EVALUATED_AT,
        ),
        right_minus_left_trip_count=-3,
    )
    result = ShuttleResult(
        outcome="success",
        request=request,
        evaluated_at=EVALUATED_AT,
        query_results=[query_result(), query_result()],
        comparison=summary,
        provenance=provenance(),
    )

    assert result.comparison is not None
    assert result.comparison.right_minus_left_trip_count == -3


def test_unsupported_and_ambiguous_requests_are_explicit_non_answers() -> None:
    unsupported = ShuttleResult(
        outcome="unsupported",
        request=UnsupportedShuttleRequest(kind="unsupported", reason="live_status"),
        evaluated_at=EVALUATED_AT,
    )
    clarification = ShuttleResult(
        outcome="needs_clarification",
        request=ShuttleClarificationRequest(
            kind="clarification", reason="ambiguous_reference"
        ),
        evaluated_at=EVALUATED_AT,
    )
    interpretation_failure = ShuttleClarificationRequest(
        kind="clarification", reason="interpretation_failure"
    )

    assert unsupported.query_results == []
    assert unsupported.provenance is None
    assert clarification.query_results == []
    assert interpretation_failure.reason == "interpretation_failure"
