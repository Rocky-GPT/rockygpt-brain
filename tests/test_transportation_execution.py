"""Focused deterministic tests for the completed shuttle capability."""

from datetime import UTC, date, datetime
from typing import cast

import pytest

from rockygpt_brain.transportation import (
    RelativeDay,
    ServiceDay,
    ServiceDayTemplate,
    ShuttleComparisonRequest,
    ShuttleQuery,
    ShuttleQueryRequest,
    UpcomingDay,
)
from rockygpt_brain.transportation_execution import (
    CAMPUS_TIME_ZONE,
    SCHEDULE_NOTICE,
    TrustedShuttleData,
    TrustedSourceData,
    TrustedTripData,
    answer_transportation,
    execute_transportation,
    route_mentions_match_trusted_data,
)


def trip(
    trip_id: str,
    service_day: str,
    sequence: int,
    departure: str,
    arrival: str,
    stops: tuple[tuple[str, str], ...] = (),
    route: str = "Test Campus Shuttle",
) -> TrustedTripData:
    assert service_day in {"weekday", "saturday", "sunday"}
    return TrustedTripData(
        trip_id=trip_id,
        source_record_key=f"record-{trip_id}",
        route=route,
        service_day=cast(ServiceDay, service_day),
        sequence=sequence,
        departure=departure,
        arrival=arrival,
        stops=stops,
        valid_from=None,
        valid_until=None,
        content_hash=f"hash-{trip_id}",
        source_id="official-source",
    )


@pytest.fixture
def trusted_data() -> TrustedShuttleData:
    return TrustedShuttleData(
        dataset_version="test-version",
        dataset_activated_at=datetime(2026, 8, 30, 12, tzinfo=UTC),
        source_commit_sha="abc123",
        sources=(
            TrustedSourceData(
                source_id="official-source",
                title="Official Transportation Services",
                url="https://example.edu/transportation",
                trust_tier="official_primary",
                freshness_sla_hours=24,
                collected_at=datetime(2026, 8, 30, 11, tzinfo=UTC),
            ),
        ),
        trips=(
            trip(
                "weekday-1",
                "weekday",
                1,
                "10:00 AM",
                "10:30 AM",
                (("Train Station", "10:20 AM"),),
            ),
            trip(
                "weekday-2",
                "weekday",
                2,
                "10:30 AM",
                "11:00 AM",
                (("Train Station", "10:50 AM"),),
            ),
            trip("weekday-3", "weekday", 3, "11:00 AM", "End of Service"),
            trip("saturday-1", "saturday", 1, "9:00 AM", "10:00 AM", route="Saturday Shuttle"),
            trip("saturday-2", "saturday", 2, "1:00 PM", "2:00 PM", route="Saturday Shuttle"),
            trip("sunday-1", "sunday", 1, "11:00 AM", "12:00 PM", route="Sunday Shuttle"),
        ),
    )


def next_request(count: int = 1, destination: str | None = None) -> ShuttleQueryRequest:
    return ShuttleQueryRequest(
        kind="query",
        answer_kind="trips",
        query=ShuttleQuery(
            day=UpcomingDay(kind="upcoming"),
            selection="next",
            count=count,
            destination_mention=destination,
        ),
        show="both",
    )


@pytest.mark.parametrize(
    ("evaluated_at", "expected_trip", "minutes_until"),
    [
        (datetime(2026, 8, 31, 9, 59, tzinfo=CAMPUS_TIME_ZONE), "weekday-1", 1),
        (datetime(2026, 8, 31, 10, 0, tzinfo=CAMPUS_TIME_ZONE), "weekday-1", 0),
        (datetime(2026, 8, 31, 10, 1, tzinfo=CAMPUS_TIME_ZONE), "weekday-2", 29),
        (datetime(2026, 8, 31, 23, 0, tzinfo=CAMPUS_TIME_ZONE), "weekday-1", 660),
    ],
)
def test_next_shuttle_boundaries_use_the_injected_clock(
    trusted_data: TrustedShuttleData,
    evaluated_at: datetime,
    expected_trip: str,
    minutes_until: int,
) -> None:
    result = execute_transportation(next_request(), evaluated_at=evaluated_at, data=trusted_data)

    record = result.query_results[0].records[0]
    assert result.outcome == "success"
    assert record.trip_id == expected_trip
    assert record.minutes_until == minutes_until


def test_next_three_preserves_deterministic_order_and_provenance(
    trusted_data: TrustedShuttleData,
) -> None:
    result = execute_transportation(
        next_request(3),
        evaluated_at=datetime(2026, 8, 31, 9, 0, tzinfo=CAMPUS_TIME_ZONE),
        data=trusted_data,
    )

    records = [record for query in result.query_results for record in query.records]
    assert [record.trip_id for record in records] == [
        "weekday-1",
        "weekday-2",
        "weekday-3",
    ]
    assert result.provenance is not None
    assert result.provenance.dataset_version == "test-version"
    assert result.provenance.sources[0].trust_tier == "official_primary"


def test_cross_day_next_results_report_complete_candidate_metadata(
    trusted_data: TrustedShuttleData,
) -> None:
    result = execute_transportation(
        next_request(3),
        evaluated_at=datetime(2026, 8, 31, 10, 45, tzinfo=CAMPUS_TIME_ZONE),
        data=trusted_data,
    )

    assert len(result.query_results) == 2
    today, tomorrow = result.query_results
    assert [record.trip_id for record in today.records] == ["weekday-3"]
    assert today.matched_count == 1
    assert today.truncated is False
    assert [record.trip_id for record in tomorrow.records] == ["weekday-1", "weekday-2"]
    assert tomorrow.matched_count == 3
    assert tomorrow.truncated is True


def test_unparseable_trusted_time_is_preserved_without_inventing_midnight(
    trusted_data: TrustedShuttleData,
) -> None:
    result = execute_transportation(
        next_request(3),
        evaluated_at=datetime(2026, 8, 31, 9, 0, tzinfo=CAMPUS_TIME_ZONE),
        data=trusted_data,
    )
    final_trip = result.query_results[0].records[-1]

    assert final_trip.arrival.label == "End of Service"
    assert final_trip.arrival.at is None
    assert "12:00 AM" not in answer_transportation(result)


def test_destination_uses_only_encoded_stop_occurrences(
    trusted_data: TrustedShuttleData,
) -> None:
    station = execute_transportation(
        next_request(destination="train station"),
        evaluated_at=datetime(2026, 8, 31, 9, 0, tzinfo=CAMPUS_TIME_ZONE),
        data=trusted_data,
    )
    missing = execute_transportation(
        next_request(destination="Ridgewood"),
        evaluated_at=datetime(2026, 8, 31, 9, 0, tzinfo=CAMPUS_TIME_ZONE),
        data=trusted_data,
    )

    assert station.query_results[0].records[0].matched_destination is not None
    assert station.query_results[0].records[0].matched_destination.location == "Train Station"
    assert missing.outcome == "no_match"
    assert not missing.query_results[0].records


def test_tomorrow_schedule_and_weekend_comparison_are_bounded(
    trusted_data: TrustedShuttleData,
) -> None:
    now = datetime(2026, 8, 31, 9, 0, tzinfo=CAMPUS_TIME_ZONE)
    tomorrow = ShuttleQueryRequest(
        kind="query",
        answer_kind="trips",
        query=ShuttleQuery(
            day=RelativeDay(kind="relative", days_from_today=1),
            selection="all",
        ),
        show="both",
    )
    comparison = ShuttleComparisonRequest(
        kind="comparison",
        queries=(
            ShuttleQuery(
                day=ServiceDayTemplate(kind="service_day", service_day="saturday"),
                selection="all",
            ),
            ShuttleQuery(
                day=ServiceDayTemplate(kind="service_day", service_day="sunday"),
                selection="all",
            ),
        ),
        show="both",
    )

    tomorrow_result = execute_transportation(tomorrow, evaluated_at=now, data=trusted_data)
    comparison_result = execute_transportation(comparison, evaluated_at=now, data=trusted_data)

    assert tomorrow_result.query_results[0].resolved_day.service_date == date(2026, 9, 1)
    assert len(tomorrow_result.query_results[0].records) == 3
    assert comparison_result.comparison is not None
    assert comparison_result.comparison.left.trip_count == 2
    assert comparison_result.comparison.right.trip_count == 1
    assert comparison_result.query_results[0].resolved_day.service_date == date(2026, 9, 5)
    assert comparison_result.query_results[1].resolved_day.service_date == date(2026, 9, 6)


def test_grounded_answer_is_explicitly_scheduled_not_live(
    trusted_data: TrustedShuttleData,
) -> None:
    result = execute_transportation(
        next_request(),
        evaluated_at=datetime(2026, 8, 31, 9, 59, tzinfo=CAMPUS_TIME_ZONE),
        data=trusted_data,
    )
    answer = answer_transportation(result)

    assert "scheduled" in answer.casefold()
    assert SCHEDULE_NOTICE in answer
    assert "Step 5B" not in answer


def test_relative_time_answer_uses_the_deterministic_countdown(
    trusted_data: TrustedShuttleData,
) -> None:
    request = next_request().model_copy(update={"show": "relative"})
    result = execute_transportation(
        request,
        evaluated_at=datetime(2026, 8, 31, 9, 15, tzinfo=CAMPUS_TIME_ZONE),
        data=trusted_data,
    )

    answer = answer_transportation(result)

    assert result.query_results[0].records[0].minutes_until == 45
    assert "**45 minutes**" in answer
    assert "right now" not in answer.casefold()


def test_full_schedule_does_not_publish_meaningless_relative_waits(
    trusted_data: TrustedShuttleData,
) -> None:
    request = ShuttleQueryRequest(
        kind="query",
        answer_kind="trips",
        query=ShuttleQuery(
            day=RelativeDay(kind="relative", days_from_today=1),
            selection="all",
        ),
        show="both",
    )

    result = execute_transportation(
        request,
        evaluated_at=datetime(2026, 8, 31, 9, 0, tzinfo=CAMPUS_TIME_ZONE),
        data=trusted_data,
    )

    assert all(record.minutes_until is None for record in result.query_results[0].records)


def test_last_shuttle_uses_the_final_bounded_trip(
    trusted_data: TrustedShuttleData,
) -> None:
    request = ShuttleQueryRequest(
        kind="query",
        answer_kind="trips",
        query=ShuttleQuery(
            day=RelativeDay(kind="relative", days_from_today=0),
            selection="last",
            count=1,
        ),
        show="departure",
    )

    result = execute_transportation(
        request,
        evaluated_at=datetime(2026, 8, 31, 22, 0, tzinfo=CAMPUS_TIME_ZONE),
        data=trusted_data,
    )

    assert result.outcome == "success"
    assert result.query_results[0].records[0].trip_id == "weekday-3"
    assert result.query_results[0].records[0].minutes_until is None
    assert "11:00 AM" in answer_transportation(result)


def test_generic_transport_word_is_not_a_trusted_route_identity(
    trusted_data: TrustedShuttleData,
) -> None:
    generic = next_request().model_copy(
        update={
            "query": next_request().query.model_copy(
                update={"route_mention": "shuttle"}
            )
        }
    )
    canonical = next_request().model_copy(
        update={
            "query": next_request().query.model_copy(
                update={"route_mention": "Test Campus Shuttle"}
            )
        }
    )

    assert route_mentions_match_trusted_data(generic, trusted_data) is False
    assert route_mentions_match_trusted_data(canonical, trusted_data) is True
