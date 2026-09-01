"""Render deterministic transportation results without changing their facts."""

from datetime import date, datetime
from typing import cast

from rockygpt_brain.capabilities.transportation.execution import _date_label
from rockygpt_brain.capabilities.transportation.models import (
    ShuttleComparisonRequest,
    ShuttleQueryRequest,
    ShuttleResult,
    ShuttleScheduleSummary,
    ShuttleTripFact,
    UnsupportedShuttleRequest,
)

SCHEDULE_NOTICE = "These are scheduled timetable times, not live GPS or ETA data."


def answer_transportation(result: ShuttleResult) -> str:
    """Render a grounded answer without giving a model room to alter trusted facts."""
    if result.outcome == "needs_clarification":
        return "I couldn't reliably determine the shuttle request. Please rephrase it."
    if result.outcome == "unsupported":
        unsupported_request = cast(UnsupportedShuttleRequest, result.request)
        if unsupported_request.reason == "capability_unavailable":
            return "Campus transportation is temporarily unavailable."
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
