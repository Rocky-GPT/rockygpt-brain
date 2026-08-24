"""Declarative CODE capabilities owned by Python, not the language model."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal


class CodeAction(StrEnum):
    CAMPUS_HOURS = "campus_hours"
    DINING_HOURS = "dining_hours"
    MENU = "menu"
    CONTACTS = "contacts"
    CLUBS = "clubs"
    EVENTS = "events"
    PROGRAMS = "programs"
    ACADEMIC_DATES = "academic_dates"
    MAP = "map"
    SHUTTLE = "shuttle"


class SortMetric(StrEnum):
    """Meaning requested by the user, independent of any DATA field name."""

    TIME = "time"
    DATE = "date"
    NAME = "name"
    CALORIES = "calories"
    PRICE = "price"
    DISTANCE = "distance"


class TimeScope(StrEnum):
    ALL = "all"
    REMAINING = "remaining"
    ACTIVE = "active"


@dataclass(frozen=True, slots=True)
class Capability:
    path: str
    method: Literal["GET", "POST"]
    filter_parameters: dict[str, str]
    sort_fields: dict[SortMetric, str]
    time_scopes: frozenset[TimeScope] = frozenset()
    include_time: bool = True


CAPABILITIES: dict[CodeAction, Capability] = {
    CodeAction.CAMPUS_HOURS: Capability(
        "/v1/search/campus-hours",
        "GET",
        {"query": "q", "day": "day"},
        {SortMetric.NAME: "name"},
    ),
    CodeAction.DINING_HOURS: Capability(
        "/v1/search/dining-hours",
        "GET",
        {"query": "q", "day": "day"},
        {SortMetric.NAME: "name"},
    ),
    CodeAction.MENU: Capability(
        "/v1/search/menu",
        "GET",
        {"query": "q", "meal": "meal"},
        {SortMetric.NAME: "name", SortMetric.CALORIES: "calories"},
    ),
    CodeAction.CONTACTS: Capability(
        "/v1/search/contacts",
        "GET",
        {"query": "q"},
        {SortMetric.NAME: "name"},
    ),
    CodeAction.CLUBS: Capability(
        "/v1/search/clubs",
        "GET",
        {"query": "q"},
        {SortMetric.NAME: "name"},
    ),
    CodeAction.EVENTS: Capability(
        "/v1/search/events",
        "GET",
        {"query": "q"},
        {
            SortMetric.NAME: "title",
            SortMetric.DATE: "date",
            SortMetric.TIME: "startTime",
        },
    ),
    CodeAction.PROGRAMS: Capability(
        "/v1/search/programs",
        "GET",
        {"query": "q"},
        {SortMetric.NAME: "name"},
    ),
    CodeAction.ACADEMIC_DATES: Capability(
        "/v1/search/academic-dates",
        "GET",
        {"query": "q"},
        {SortMetric.NAME: "title", SortMetric.DATE: "date"},
    ),
    CodeAction.MAP: Capability(
        "/v1/map",
        "GET",
        {"query": "q"},
        {},
        include_time=False,
    ),
    CodeAction.SHUTTLE: Capability(
        "/v2/capabilities/shuttle/query",
        "POST",
        {
            "route": "route",
            "origin": "origin",
            "destination": "destination",
            "serviceDate": "serviceDate",
        },
        {
            SortMetric.NAME: "route",
            SortMetric.DATE: "serviceDate",
            SortMetric.TIME: "matchedOrigin.time",
        },
        frozenset({TimeScope.ALL, TimeScope.REMAINING, TimeScope.ACTIVE}),
    ),
}


def capability_guide() -> str:
    """Generate the AI #1 capability guide from the executable registry."""

    lines: list[str] = []
    for action, capability in CAPABILITIES.items():
        filters = ", ".join(capability.filter_parameters) or "none"
        sorts = ", ".join(metric.value for metric in capability.sort_fields) or "none"
        scopes = ", ".join(scope.value for scope in capability.time_scopes) or "none"
        lines.append(
            f"- {action.value}: filters [{filters}]; sort concepts [{sorts}]; "
            f"time scopes [{scopes}]"
        )
    return "\n".join(lines)
