"""Deterministic transportation filtering, ordering, and calculations."""

import re
from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from rockygpt_brain.capabilities.transportation.models import (
    AROUND_WINDOW_MINUTES,
    CalendarDay,
    NamedWeekday,
    RelativeDay,
    ResolvedShuttleDay,
    ServiceDay,
    ServiceDayTemplate,
    ShuttleClarificationRequest,
    ShuttleComparisonFact,
    ShuttleComparisonRequest,
    ShuttleProvenance,
    ShuttleQuery,
    ShuttleQueryRequest,
    ShuttleQueryResult,
    ShuttleRequestValue,
    ShuttleResult,
    ShuttleScheduleSummary,
    ShuttleSource,
    ShuttleStopFact,
    ShuttleTimedFact,
    ShuttleTripFact,
    UnsupportedShuttleRequest,
    UpcomingDay,
)
from rockygpt_brain.capabilities.transportation.repository import (
    TrustedShuttleData,
    TrustedTripData,
    load_trusted_shuttle_data,
)

CAMPUS_TIME_ZONE = ZoneInfo("America/New_York")
WEEKDAYS = (
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"
)

def _resolve_day(query: ShuttleQuery, now: datetime) -> ResolvedShuttleDay:
    day = query.day
    if isinstance(day, RelativeDay):
        resolved_date = now.date() + timedelta(days=day.days_from_today)
        label = "today" if day.days_from_today == 0 else "tomorrow"
        return ResolvedShuttleDay(
            label=label,
            service_date=resolved_date,
            service_day=_service_day(resolved_date),
        )
    if isinstance(day, NamedWeekday):
        target = WEEKDAYS.index(day.weekday)
        resolved_date = now.date() + timedelta(days=(target - now.weekday()) % 7)
        return ResolvedShuttleDay(
            label=day.weekday.capitalize(),
            service_date=resolved_date,
            service_day=_service_day(resolved_date),
        )
    if isinstance(day, ServiceDayTemplate):
        resolved_date = _next_service_date(now.date(), day.service_day)
        return ResolvedShuttleDay(
            label=day.service_day.capitalize(),
            service_date=resolved_date,
            service_day=day.service_day,
        )
    assert isinstance(day, CalendarDay)
    return ResolvedShuttleDay(
        label=day.date.isoformat(),
        service_date=day.date,
        service_day=_service_day(day.date),
    )


def _service_day(value: date) -> ServiceDay:
    if value.weekday() == 5:
        return "saturday"
    if value.weekday() == 6:
        return "sunday"
    return "weekday"


def _next_service_date(start: date, service_day: ServiceDay) -> date:
    if service_day == "saturday":
        return start + timedelta(days=(5 - start.weekday()) % 7)
    if service_day == "sunday":
        return start + timedelta(days=(6 - start.weekday()) % 7)
    if start.weekday() < 5:
        return start
    return start + timedelta(days=7 - start.weekday())


def _date_label(value: date, now: datetime) -> str:
    if value == now.date():
        return "today"
    if value == now.date() + timedelta(days=1):
        return "tomorrow"
    return f"{value.strftime('%A')}, {value.strftime('%B')} {value.day}"


def route_mentions_match_trusted_data(
    request: ShuttleRequestValue,
    data: TrustedShuttleData,
) -> bool:
    """Reject model-assigned route filters that identify no trusted route."""
    route_names = {trip.route for trip in data.trips}
    for query in _request_queries(request):
        mention = query.route_mention
        if mention is not None and not any(
            _is_route_identity_match(route_name, mention) for route_name in route_names
        ):
            return False
    return True


def _best_stop(stops: list[ShuttleStopFact], mention: str) -> ShuttleStopFact | None:
    scored = [
        (_match_score(stop.location, mention), index, stop) for index, stop in enumerate(stops)
    ]
    score, _, stop = max(scored, default=(0.0, 0, None), key=lambda item: (item[0], -item[1]))
    return stop if score >= 0.5 else None


def _match_score(candidate: str, requested: str) -> float:
    candidate_text = _normalize(candidate)
    requested_text = _normalize(requested)
    if not candidate_text or not requested_text:
        return 0.0
    if candidate_text == requested_text:
        return 1.0
    if requested_text in candidate_text or candidate_text in requested_text:
        return 0.9
    requested_words = set(requested_text.split()) - {"the", "a", "an"}
    candidate_words = set(candidate_text.split())
    if not requested_words:
        return 0.0
    return len(requested_words & candidate_words) / len(requested_words)


def _is_route_identity_match(candidate: str, requested: str) -> bool:
    candidate_text = _normalize(candidate)
    requested_text = _normalize(requested)
    if candidate_text == requested_text:
        return True
    requested_words = requested_text.split()
    if len(requested_words) < 2 and not any(character.isdigit() for character in requested_text):
        return False
    return _match_score(candidate, requested) >= 0.5


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", value.casefold())).strip()


def _is_campus(mention: str) -> bool:
    words = set(_normalize(mention).split())
    return "campus" in words or words in ({"ramapo"}, {"ramapo", "college"})


def _has_mentions(query: ShuttleQuery) -> bool:
    return any((query.route_mention, query.origin_mention, query.destination_mention))


def _request_queries(request: ShuttleRequestValue) -> tuple[ShuttleQuery, ...]:
    if isinstance(request, ShuttleQueryRequest):
        return (request.query,)
    if isinstance(request, ShuttleComparisonRequest):
        return request.queries
    return ()


def _facts_for_day(
    query: ShuttleQuery,
    resolved: ResolvedShuttleDay,
    now: datetime,
    data: TrustedShuttleData,
) -> list[ShuttleTripFact]:
    assert resolved.service_date is not None
    service_date = resolved.service_date
    facts: list[ShuttleTripFact] = []
    for trip in data.trips:
        if trip.service_day != resolved.service_day:
            continue
        if trip.valid_from is not None and service_date < trip.valid_from:
            continue
        if trip.valid_until is not None and service_date > trip.valid_until:
            continue
        if query.route_mention and _match_score(trip.route, query.route_mention) < 0.5:
            continue
        fact = _trip_fact(trip, service_date, query, now)
        if fact is not None:
            facts.append(fact)
    return sorted(facts, key=_selection_time)


def _trip_fact(
    trip: TrustedTripData,
    service_date: date,
    query: ShuttleQuery,
    now: datetime,
) -> ShuttleTripFact | None:
    departure_at = _scheduled_at(service_date, trip.departure)
    if departure_at is None:
        return None
    last_at = departure_at
    stops: list[ShuttleStopFact] = []
    for location, label in trip.stops:
        stop_at = _scheduled_at(service_date, label, last_at)
        stops.append(
            ShuttleStopFact(
                location=location,
                time=ShuttleTimedFact(label=label, at=stop_at),
            )
        )
        if stop_at is not None:
            last_at = stop_at
    arrival_at = _scheduled_at(service_date, trip.arrival, last_at)

    matched_origin: ShuttleStopFact | None = None
    if query.origin_mention and not _is_campus(query.origin_mention):
        matched_origin = _best_stop(stops, query.origin_mention)
        if matched_origin is None:
            return None
    matched_destination: ShuttleStopFact | None = None
    if query.destination_mention:
        matched_destination = _best_stop(stops, query.destination_mention)
        if matched_destination is None:
            return None
    if (
        matched_origin is not None
        and matched_destination is not None
        and matched_origin.time.at is not None
        and matched_destination.time.at is not None
        and matched_destination.time.at <= matched_origin.time.at
    ):
        return None

    selection_at = matched_origin.time.at if matched_origin is not None else departure_at
    if selection_at is None:
        return None
    delta_minutes = int((selection_at - now).total_seconds() // 60)
    minutes_until = (
        delta_minutes if query.selection == "next" and delta_minutes >= 0 else None
    )
    return ShuttleTripFact(
        trip_id=trip.trip_id,
        source_record_key=trip.source_record_key,
        route=trip.route,
        service_date=service_date,
        service_day=trip.service_day,
        departure=ShuttleTimedFact(label=trip.departure, at=departure_at),
        stops=stops,
        arrival=ShuttleTimedFact(label=trip.arrival, at=arrival_at),
        matched_origin=matched_origin,
        matched_destination=matched_destination,
        minutes_until=minutes_until,
        source_id=trip.source_id,
        content_hash=trip.content_hash,
    )


def _scheduled_at(
    service_date: date, label: str, not_before: datetime | None = None
) -> datetime | None:
    parsed: time | None = None
    for pattern in ("%I:%M %p", "%I %p", "%H:%M"):
        try:
            parsed = datetime.strptime(label.strip(), pattern).time()
            break
        except ValueError:
            continue
    if parsed is None:
        return None
    value = datetime.combine(service_date, parsed, tzinfo=CAMPUS_TIME_ZONE)
    if not_before is not None and value < not_before:
        value += timedelta(days=1)
    return value


def _apply_time_and_selection(
    query: ShuttleQuery,
    resolved: ResolvedShuttleDay,
    facts: list[ShuttleTripFact],
    now: datetime,
) -> list[ShuttleTripFact]:
    selected = facts
    if query.time is not None:
        target = query.time.clock.hour * 60 + query.time.clock.minute
        window = AROUND_WINDOW_MINUTES if query.time.relation == "around" else 0
        selected = [
            fact
            for fact in selected
            if (basis_at := _basis_time(fact, query.time.basis)) is not None
            and abs(basis_at.hour * 60 + basis_at.minute - target) <= window
        ]
    if query.selection == "next":
        if resolved.service_date == now.date():
            selected = [fact for fact in selected if _selection_time(fact) >= now]
        selected = selected[query.offset : query.offset + (query.count or 1)]
    elif query.selection == "last":
        selected = selected[-1:]
    return selected


def _selection_time(fact: ShuttleTripFact) -> datetime:
    selected = fact.matched_origin.time.at if fact.matched_origin is not None else fact.departure.at
    assert selected is not None
    return selected


def _basis_time(fact: ShuttleTripFact, basis: str) -> datetime | None:
    if basis == "departure":
        return _selection_time(fact)
    if fact.matched_destination is not None:
        return fact.matched_destination.time.at
    return fact.arrival.at


def _execute_query(
    query: ShuttleQuery, now: datetime, data: TrustedShuttleData
) -> tuple[list[ShuttleQueryResult], int]:
    if isinstance(query.day, UpcomingDay):
        return _execute_upcoming(query, now, data)
    resolved = _resolve_day(query, now)
    facts = _facts_for_day(query, resolved, now, data)
    filter_matches = len(facts)
    selected = _apply_time_and_selection(query, resolved, facts, now)
    return [_query_result(query, resolved, selected, len(facts))], filter_matches


def _execute_upcoming(
    query: ShuttleQuery, now: datetime, data: TrustedShuttleData
) -> tuple[list[ShuttleQueryResult], int]:
    needed = (query.count or 1) + query.offset
    candidates: list[tuple[ResolvedShuttleDay, ShuttleTripFact]] = []
    eligible_counts: dict[date, int] = {}
    filter_matches = 0
    first_resolved: ResolvedShuttleDay | None = None
    for days_ahead in range(8):
        service_date = now.date() + timedelta(days=days_ahead)
        resolved = ResolvedShuttleDay(
            label=_date_label(service_date, now),
            service_date=service_date,
            service_day=_service_day(service_date),
        )
        first_resolved = first_resolved or resolved
        facts = _facts_for_day(query, resolved, now, data)
        filter_matches += len(facts)
        if days_ahead == 0:
            facts = [fact for fact in facts if _selection_time(fact) >= now]
        eligible_counts[service_date] = len(facts)
        candidates.extend((resolved, fact) for fact in facts)
        if len(candidates) >= needed:
            break

    candidates.sort(key=lambda item: _selection_time(item[1]))
    chosen = candidates[query.offset : query.offset + (query.count or 1)]
    grouped: dict[date, tuple[ResolvedShuttleDay, list[ShuttleTripFact]]] = {}
    for resolved, fact in chosen:
        assert resolved.service_date is not None
        grouped.setdefault(resolved.service_date, (resolved, []))[1].append(fact)
    results = [
        _query_result(query, resolved, facts, eligible_counts[service_date])
        for service_date, (resolved, facts) in grouped.items()
    ]
    if not results:
        assert first_resolved is not None
        results = [_query_result(query, first_resolved, [], filter_matches)]
    return results, filter_matches


def _query_result(
    query: ShuttleQuery,
    resolved: ResolvedShuttleDay,
    records: list[ShuttleTripFact],
    matched_count: int,
) -> ShuttleQueryResult:
    return ShuttleQueryResult(
        resolved_day=resolved,
        records=records,
        matched_count=max(matched_count, len(records)),
        truncated=query.selection in {"next", "last"} and matched_count > len(records),
        around_window_minutes=(
            15 if query.time is not None and query.time.relation == "around" else None
        ),
    )


def _summary(result: ShuttleQueryResult) -> ShuttleScheduleSummary:
    departures = [record.departure.at for record in result.records if record.departure.at]
    return ShuttleScheduleSummary(
        label=result.resolved_day.label,
        trip_count=len(result.records),
        first_departure_at=min(departures, default=None),
        last_departure_at=max(departures, default=None),
    )


def _candidates(data: TrustedShuttleData) -> list[str]:
    values = {trip.route for trip in data.trips}
    values.update(location for trip in data.trips for location, _ in trip.stops)
    return sorted(values)


def _provenance(data: TrustedShuttleData) -> ShuttleProvenance:
    return ShuttleProvenance(
        dataset_version=data.dataset_version,
        dataset_activated_at=data.dataset_activated_at,
        source_commit_sha=data.source_commit_sha,
        sources=[
            ShuttleSource(
                source_id=source.source_id,
                title=source.title,
                url=source.url,
                trust_tier=source.trust_tier,
                freshness_sla_hours=source.freshness_sla_hours,
                collected_at=source.collected_at,
            )
            for source in data.sources
        ],
    )


def execute_transportation(
    request: ShuttleRequestValue,
    *,
    evaluated_at: datetime | None = None,
    data: TrustedShuttleData | None = None,
) -> ShuttleResult:
    """Execute one validated request using only trusted data and deterministic code."""
    if evaluated_at is not None and evaluated_at.tzinfo is None:
        raise ValueError("evaluated_at must include a timezone")
    now = (evaluated_at or datetime.now(CAMPUS_TIME_ZONE)).astimezone(CAMPUS_TIME_ZONE)
    if isinstance(request, ShuttleClarificationRequest):
        return ShuttleResult(
            outcome="needs_clarification",
            request=request,
            evaluated_at=now,
        )
    if isinstance(request, UnsupportedShuttleRequest):
        return ShuttleResult(
            outcome="unsupported",
            request=request,
            evaluated_at=now,
        )

    trusted = data or load_trusted_shuttle_data()
    provenance = _provenance(trusted)
    if isinstance(request, ShuttleComparisonRequest):
        query_results: list[ShuttleQueryResult] = []
        filter_matches = 0
        for query in request.queries:
            result, matched = _execute_query(query, now, trusted)
            query_results.extend(result)
            filter_matches += matched
        if filter_matches == 0 and any(_has_mentions(query) for query in request.queries):
            return ShuttleResult(
                outcome="no_match",
                request=request,
                evaluated_at=now,
                query_results=query_results,
                candidates=_candidates(trusted),
                provenance=provenance,
            )
        left, right = query_results
        comparison = ShuttleComparisonFact(
            left=_summary(left),
            right=_summary(right),
            right_minus_left_trip_count=len(right.records) - len(left.records),
        )
        return ShuttleResult(
            outcome="success",
            request=request,
            evaluated_at=now,
            query_results=query_results,
            comparison=comparison,
            provenance=provenance,
        )

    query_results, filter_matches = _execute_query(request.query, now, trusted)
    record_count = sum(len(result.records) for result in query_results)
    outcome: Literal["success", "empty", "no_match"] = "success" if record_count else "empty"
    candidates: list[str] = []
    if not record_count and filter_matches == 0 and _has_mentions(request.query):
        outcome = "no_match"
        candidates = _candidates(trusted)
    return ShuttleResult(
        outcome=outcome,
        request=request,
        evaluated_at=now,
        query_results=query_results,
        candidates=candidates,
        provenance=provenance,
    )
