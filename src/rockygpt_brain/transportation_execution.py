"""Deterministic execution and answers for the bounded shuttle capability."""

import os
import re
import socket
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Literal, cast
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import certifi
from psycopg import Connection, OperationalError, connect
from psycopg.rows import dict_row

from rockygpt_brain.transportation import (
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

CAMPUS_TIME_ZONE = ZoneInfo("America/New_York")
SCHEDULE_NOTICE = "These are scheduled timetable times, not live GPS or ETA data."
WEEKDAYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)


@dataclass(frozen=True)
class TrustedSourceData:
    source_id: str
    title: str
    url: str
    trust_tier: Literal["official_primary", "official_secondary", "community"]
    freshness_sla_hours: int
    collected_at: datetime


@dataclass(frozen=True)
class TrustedTripData:
    trip_id: str
    source_record_key: str
    route: str
    service_day: ServiceDay
    sequence: int
    departure: str
    arrival: str
    stops: tuple[tuple[str, str], ...]
    valid_from: date | None
    valid_until: date | None
    content_hash: str
    source_id: str


@dataclass(frozen=True)
class TrustedShuttleData:
    dataset_version: str
    dataset_activated_at: datetime
    source_commit_sha: str | None
    sources: tuple[TrustedSourceData, ...]
    trips: tuple[TrustedTripData, ...]


def load_trusted_shuttle_data(database_url: str | None = None) -> TrustedShuttleData:
    """Read the active trusted shuttle dataset directly from PostgreSQL."""
    url = database_url or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is required for shuttle execution")

    hostname = urlsplit(url).hostname
    if not hostname:
        raise RuntimeError("DATABASE_URL has no hostname")
    try:
        host_addresses = socket.gethostbyname_ex(hostname)[2]
    except OSError as error:
        raise RuntimeError("unable to resolve the trusted shuttle database") from error

    connection: Connection[dict[str, Any]] | None = None
    last_error: OperationalError | None = None
    for host_address in host_addresses:
        try:
            connection = connect(
                url,
                hostaddr=host_address,
                sslrootcert=certifi.where(),
                connect_timeout=5,
                row_factory=dict_row,
            )
            break
        except OperationalError as error:
            last_error = error
    if connection is None:
        raise RuntimeError("unable to connect to the trusted shuttle database") from last_error

    with connection:
        rows: list[dict[str, Any]] = connection.execute(
            """
                SELECT v.version,
                       v.activated_at,
                       v.source_commit_sha,
                       t.id::text AS trip_id,
                       t.source_record_key,
                       t.sequence,
                       t.departure,
                       t.arrival,
                       t.stops,
                       t.valid_from,
                       t.valid_until,
                       t.content_hash,
                       r.name AS route,
                       r.service_day,
                       s.id::text AS source_id,
                       s.title AS source_title,
                       s.canonical_url AS source_url,
                       s.trust_tier,
                       s.freshness_sla_hours,
                       t.collected_at
                  FROM rockygpt_v2.dataset_versions v
                  JOIN rockygpt_v2.shuttle_trips t ON t.dataset_version_id = v.id
                  JOIN rockygpt_v2.shuttle_routes r
                    ON r.id = t.route_id AND r.dataset_version_id = v.id
                  JOIN rockygpt_v2.sources s ON s.id = t.source_id
                 WHERE v.status = 'active'
                 ORDER BY r.service_day, r.name, t.sequence
                """
        ).fetchall()

    if not rows:
        raise RuntimeError("the active trusted dataset has no shuttle trips")

    first = rows[0]
    sources: dict[str, TrustedSourceData] = {}
    trips: list[TrustedTripData] = []
    for row in rows:
        source_id = str(row["source_id"])
        trust_tier = str(row["trust_tier"])
        if trust_tier not in {"official_primary", "official_secondary", "community"}:
            raise RuntimeError(f"unsupported source trust tier in database: {trust_tier}")
        sources[source_id] = TrustedSourceData(
            source_id=source_id,
            title=str(row["source_title"]),
            url=str(row["source_url"]),
            trust_tier=cast(
                Literal["official_primary", "official_secondary", "community"],
                trust_tier,
            ),
            freshness_sla_hours=int(row["freshness_sla_hours"]),
            collected_at=cast(datetime, row["collected_at"]),
        )
        raw_stops = cast(list[dict[str, Any]], row["stops"])
        service_day = str(row["service_day"])
        if service_day not in {"weekday", "saturday", "sunday"}:
            raise RuntimeError(f"unsupported shuttle service day in database: {service_day}")
        trips.append(
            TrustedTripData(
                trip_id=str(row["trip_id"]),
                source_record_key=str(row["source_record_key"]),
                route=str(row["route"]),
                service_day=cast(ServiceDay, service_day),
                sequence=int(row["sequence"]),
                departure=str(row["departure"]),
                arrival=str(row["arrival"]),
                stops=tuple((str(stop["location"]), str(stop["time"])) for stop in raw_stops),
                valid_from=cast(date | None, row["valid_from"]),
                valid_until=cast(date | None, row["valid_until"]),
                content_hash=str(row["content_hash"]),
                source_id=source_id,
            )
        )

    return TrustedShuttleData(
        dataset_version=str(first["version"]),
        dataset_activated_at=cast(datetime, first["activated_at"]),
        source_commit_sha=(
            str(first["source_commit_sha"]) if first["source_commit_sha"] is not None else None
        ),
        sources=tuple(sources.values()),
        trips=tuple(trips),
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


def answer_transportation(result: ShuttleResult) -> str:
    """Render a grounded answer without giving a model room to alter trusted facts."""
    if result.outcome == "needs_clarification":
        return "I couldn't reliably determine the shuttle request. Please rephrase it."
    if result.outcome == "unsupported":
        unsupported_request = cast(UnsupportedShuttleRequest, result.request)
        if unsupported_request.reason == "live_status":
            return (
                "I have the official scheduled shuttle timetable, but not live GPS, delay, "
                "or ETA data."
            )
        return (
            "That information is not available in the official scheduled shuttle data. "
            f"{SCHEDULE_NOTICE}"
        )
    if result.outcome == "no_match":
        return (
            "I couldn't find that route or destination in the official scheduled shuttle data. "
            f"{SCHEDULE_NOTICE}"
        )
    if result.outcome == "empty":
        return f"No scheduled shuttle matches that request. {SCHEDULE_NOTICE}"

    if isinstance(result.request, ShuttleComparisonRequest):
        assert result.comparison is not None
        left = result.comparison.left
        right = result.comparison.right
        return (
            f"The official schedule lists **{left.trip_count} scheduled trips** for "
            f"**{left.label}** and **{right.trip_count} scheduled trips** for "
            f"**{right.label}**. {left.label} runs from {_summary_range(left)}; "
            f"{right.label} runs from {_summary_range(right)}. {SCHEDULE_NOTICE}"
        )

    query_request = cast(ShuttleQueryRequest, result.request)
    records = [record for query_result in result.query_results for record in query_result.records]
    if query_request.answer_kind == "availability":
        sentences = [
            _trip_sentence(record, query_request.show, result.evaluated_at) for record in records
        ]
        return (
            "Yes—" + " ".join(sentences) + f" {SCHEDULE_NOTICE}"
            if sentences
            else f"No scheduled shuttle matches that time. {SCHEDULE_NOTICE}"
        )
    if query_request.query.selection in {"next", "last"}:
        if len(records) == 1:
            sentence = _trip_sentence(records[0], query_request.show, result.evaluated_at)
            return f"{sentence} {SCHEDULE_NOTICE}"
        lines = "\n".join(
            f"{index}. {_trip_sentence(record, query_request.show, result.evaluated_at)}"
            for index, record in enumerate(records, start=1)
        )
        return (
            f"Here are the next **{len(records)} scheduled shuttles**:\n\n"
            f"{lines}\n\n{SCHEDULE_NOTICE}"
        )

    grouped: dict[tuple[str, date], list[ShuttleTripFact]] = {}
    for record in records:
        grouped.setdefault((record.route, record.service_date), []).append(record)
    sections: list[str] = []
    for (route, service_date), route_records in grouped.items():
        entries = ", ".join(_schedule_entry(record, query_request.show) for record in route_records)
        day_label = _date_label(service_date, result.evaluated_at)
        sections.append(f"- **{route}** ({day_label}): {entries}")
    return (
        f"The official scheduled shuttle timetable has **{len(records)} trips**:\n\n"
        + "\n".join(sections)
        + f"\n\n{SCHEDULE_NOTICE}"
    )


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


def _has_mentions(query: ShuttleQuery) -> bool:
    return any((query.route_mention, query.origin_mention, query.destination_mention))


def _request_queries(request: ShuttleRequestValue) -> tuple[ShuttleQuery, ...]:
    if isinstance(request, ShuttleQueryRequest):
        return (request.query,)
    if isinstance(request, ShuttleComparisonRequest):
        return request.queries
    return ()


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


def _summary(result: ShuttleQueryResult) -> ShuttleScheduleSummary:
    departures = [record.departure.at for record in result.records if record.departure.at]
    return ShuttleScheduleSummary(
        label=result.resolved_day.label,
        trip_count=len(result.records),
        first_departure_at=min(departures, default=None),
        last_departure_at=max(departures, default=None),
    )


def _summary_range(summary: ShuttleScheduleSummary) -> str:
    if summary.first_departure_at is None or summary.last_departure_at is None:
        return "no published departures"
    return (
        f"**{summary.first_departure_at.strftime('%-I:%M %p')}** to "
        f"**{summary.last_departure_at.strftime('%-I:%M %p')}**"
    )


def _trip_sentence(record: ShuttleTripFact, show: str, now: datetime) -> str:
    day = _date_label(record.service_date, now)
    departure = f"**{day} at {record.departure.label}**"
    if show == "relative":
        if record.minutes_until == 0:
            detail = "—**right now**"
        elif record.minutes_until is not None:
            unit = "minute" if record.minutes_until == 1 else "minutes"
            detail = f"—in **{record.minutes_until} {unit}**"
        else:
            detail = ""
    elif record.matched_destination is not None:
        destination = record.matched_destination
        detail = f" and reach **{destination.location} at {destination.time.label}**"
    elif show in {"arrival", "both"}:
        detail = f" and finish its scheduled run at **{record.arrival.label}**"
    else:
        detail = ""
    return f"The **{record.route}** is scheduled to depart campus {departure}{detail}."


def _schedule_entry(record: ShuttleTripFact, show: str) -> str:
    if show == "departure":
        return record.departure.label
    if show == "arrival":
        return record.arrival.label
    return f"{record.departure.label}–{record.arrival.label}"


def _date_label(value: date, now: datetime) -> str:
    if value == now.date():
        return "today"
    if value == now.date() + timedelta(days=1):
        return "tomorrow"
    return f"{value.strftime('%A')}, {value.strftime('%B')} {value.day}"
