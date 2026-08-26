from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from rockygpt_brain.capabilities.calendar import execute as calendar
from rockygpt_brain.capabilities.calendar import normalize as calendar_normalize
from rockygpt_brain.capabilities.clubs import execute as clubs
from rockygpt_brain.capabilities.clubs import normalize as clubs_normalize
from rockygpt_brain.capabilities.courses import execute as courses
from rockygpt_brain.capabilities.courses import normalize as courses_normalize
from rockygpt_brain.capabilities.dining import execute as dining
from rockygpt_brain.capabilities.dining import normalize as dining_normalize
from rockygpt_brain.capabilities.directory import execute as directory
from rockygpt_brain.capabilities.directory import normalize as directory_normalize
from rockygpt_brain.capabilities.events import execute as events
from rockygpt_brain.capabilities.events import normalize as events_normalize
from rockygpt_brain.capabilities.hours import execute as hours
from rockygpt_brain.capabilities.hours import normalize as hours_normalize
from rockygpt_brain.capabilities.locations import execute as locations
from rockygpt_brain.capabilities.locations import normalize as locations_normalize
from rockygpt_brain.capabilities.programs import execute as programs
from rockygpt_brain.capabilities.programs import normalize as programs_normalize
from rockygpt_brain.capabilities.transportation import execute as transportation
from rockygpt_brain.capabilities.transportation import normalize as transportation_normalize
from rockygpt_brain.capabilities.types import Reader
from rockygpt_brain.services.data import DataPort

Executor = Callable[[dict[str, str], datetime, DataPort], Awaitable[list[dict[str, Any]]]]


@dataclass(frozen=True, slots=True)
class Capability:
    describes: str
    filters: frozenset[str]
    fields: frozenset[str]
    execute: Executor
    read: dict[str, Reader]
    sort: dict[str, Reader]


CAPABILITIES: dict[str, Capability] = {
    "transportation": Capability(
        describes="campus shuttle and bus departures, routes, origins, and destinations",
        filters=frozenset({"date", "departingAfter", "route", "origin", "destination"}),
        fields=frozenset({"departureTime", "arrivalTime", "route", "origin", "destination"}),
        execute=transportation.run,
        read=transportation_normalize.FIELDS,
        sort=transportation_normalize.SORT,
    ),
    "dining": Capability(
        describes="today's campus dining menu items, meals, stations, and dietary options",
        filters=frozenset({"name", "meal", "station", "dietary"}),
        fields=frozenset(
            {"name", "meal", "station", "calories", "vegan", "vegetarian", "allergens"}
        ),
        execute=dining.run,
        read=dining_normalize.FIELDS,
        sort=dining_normalize.SORT,
    ),
    "events": Capability(
        describes="upcoming campus events, their dates, times, organizers, and descriptions",
        filters=frozenset({"topic", "title", "organizer", "date", "startsAfter"}),
        fields=frozenset(
            {"title", "date", "startTime", "endTime", "organizer", "description", "eventUrl"}
        ),
        execute=events.run,
        read=events_normalize.FIELDS,
        sort=events_normalize.SORT,
    ),
    "hours": Capability(
        describes=("opening hours and open/closed status for campus facilities and dining venues"),
        filters=frozenset({"name", "kind", "date", "day", "openAt"}),
        fields=frozenset({"name", "kind", "day", "schedule", "openNow", "opensAt", "closesAt"}),
        execute=hours.run,
        read=hours_normalize.FIELDS,
        sort=hours_normalize.SORT,
    ),
    "courses": Capability(
        describes=(
            "individual course catalog entries: codes, titles, descriptions, "
            "credits, and attributes"
        ),
        filters=frozenset({"code", "subject", "name", "attribute"}),
        fields=frozenset({"code", "name", "description", "credits", "attributes", "courseUrl"}),
        execute=courses.run,
        read=courses_normalize.FIELDS,
        sort=courses_normalize.SORT,
    ),
    "directory": Capability(
        describes=(
            "public contact information for campus people, departments, and offices; not maps"
        ),
        filters=frozenset({"name", "department"}),
        fields=frozenset({"name", "department", "phone", "email", "office"}),
        execute=directory.run,
        read=directory_normalize.FIELDS,
        sort=directory_normalize.SORT,
    ),
    "calendar": Capability(
        describes=(
            "academic calendar dates, term milestones, breaks, finals, and registration deadlines; "
            "not campus activities"
        ),
        filters=frozenset({"topic", "title", "term", "date", "startsAfter"}),
        fields=frozenset({"term", "date", "startsAt", "title", "description"}),
        execute=calendar.run,
        read=calendar_normalize.FIELDS,
        sort=calendar_normalize.SORT,
    ),
    "clubs": Capability(
        describes="student organizations, clubs, organization categories, and Greek life",
        filters=frozenset({"name", "category"}),
        fields=frozenset({"name", "category", "websiteUrl"}),
        execute=clubs.run,
        read=clubs_normalize.FIELDS,
        sort=clubs_normalize.SORT,
    ),
    "locations": Capability(
        describes=(
            "physical campus buildings, offices, rooms, parking areas, and map links; not hours"
        ),
        filters=frozenset({"name", "type", "building", "room"}),
        fields=frozenset(
            {
                "key",
                "name",
                "type",
                "mapUrl",
                "aliases",
                "buildingName",
                "room",
                "category",
                "description",
                "officeUrl",
            }
        ),
        execute=locations.run,
        read=locations_normalize.FIELDS,
        sort=locations_normalize.SORT,
    ),
    "programs": Capability(
        describes=(
            "academic degree programs, majors, minors, certificates, combined programs, "
            "and graduate degrees; not individual courses"
        ),
        filters=frozenset({"name", "subject", "programKind", "degree", "school", "level"}),
        fields=frozenset(
            {
                "name",
                "degree",
                "programKind",
                "level",
                "school",
                "description",
                "programUrl",
            }
        ),
        execute=programs.run,
        read=programs_normalize.FIELDS,
        sort=programs_normalize.SORT,
    ),
}

CAPABILITY_ALIASES: dict[str, str] = {"shuttle": "transportation"}


def canonical_name(name: str) -> str:
    return CAPABILITY_ALIASES.get(name, name)


def capability_for(name: str) -> Capability | None:
    return CAPABILITIES.get(canonical_name(name))


def catalogue() -> list[dict[str, Any]]:
    return [
        {
            "capability": name,
            "describes": capability.describes,
            "filters": sorted(capability.filters),
            "fields": sorted(capability.fields),
        }
        for name, capability in CAPABILITIES.items()
    ]
