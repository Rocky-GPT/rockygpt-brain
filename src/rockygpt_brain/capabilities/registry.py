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
from rockygpt_brain.capabilities.filters import FilterSpec, date, entity, enum, instant, text
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
Normalizer = Callable[[dict[str, str], datetime, DataPort], Awaitable[dict[str, str]]]


@dataclass(frozen=True, slots=True)
class Capability:
    describes: str  # one line, handed to the planner as written
    filters: dict[str, FilterSpec]  # fields a plan may narrow on and what each may contain
    fields: frozenset[str]  # fields a plan may sort by, compare, or read
    execute: Executor  # required: no entry without the code to run it
    read: dict[str, Reader]  # how each published field comes off a record
    sort: dict[str, Reader]  # where sorting on the published value sorts wrongly
    normalize: Normalizer | None = None  # semantic mentions -> canonical execution filters
    parallel: frozenset[str] = frozenset()  # what tells otherwise-equal answers apart


CAPABILITIES: dict[str, Capability] = {
    "transportation": Capability(
        describes="campus shuttle and bus departures, routes, origins, and destinations",
        filters={
            "date": date(),
            "departingAfter": instant(),
            "route": entity("transportation_route"),
            "origin": entity("transportation_stop"),
            "destination": entity("transportation_stop"),
        },
        fields=frozenset({"departureTime", "arrivalTime", "route", "origin", "destination"}),
        execute=transportation.run,
        read=transportation_normalize.FIELDS,
        sort=transportation_normalize.SORT,
    ),
    "dining": Capability(
        describes="today's campus dining menu items, meals, stations, and dietary options",
        filters={
            "name": text(),
            "meal": enum("breakfast", "lunch", "dinner", "late_night"),
            "station": entity("dining_station"),
            "dietary": enum("vegan", "vegetarian"),
            "date": date(),
        },
        fields=frozenset(
            {"date", "name", "meal", "station", "calories", "vegan", "vegetarian", "allergens"}
        ),
        execute=dining.run,
        read=dining_normalize.FIELDS,
        sort=dining_normalize.SORT,
    ),
    "events": Capability(
        describes="upcoming campus events, their dates, times, organizers, and descriptions",
        filters={
            "topic": text(),
            "title": text(),
            "organizer": entity("event_organization"),
            "date": date(),
            "startsAfter": instant(),
        },
        fields=frozenset(
            {"title", "date", "startTime", "endTime", "organizer", "description", "eventUrl"}
        ),
        execute=events.run,
        read=events_normalize.FIELDS,
        sort=events_normalize.SORT,
    ),
    "hours": Capability(
        describes=("opening hours and open/closed status for campus facilities and dining venues"),
        filters={
            "name": entity("hours_place"),
            "kind": enum("campus", "dining"),
            "date": date(),
            "day": text(),
            "openAt": instant(),
        },
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
        filters={
            "code": text(),
            "subject": entity("course_subject"),
            "name": text(),
            "attribute": entity("course_attribute"),
        },
        fields=frozenset({"code", "name", "description", "credits", "attributes", "courseUrl"}),
        execute=courses.run,
        read=courses_normalize.FIELDS,
        sort=courses_normalize.SORT,
        normalize=courses_normalize.resolve_filters,
    ),
    "directory": Capability(
        describes=(
            "public contact information for campus people, departments, and offices; not maps"
        ),
        filters={
            "name": entity("directory_contact"),
            "department": entity("campus_department"),
        },
        fields=frozenset({"name", "department", "phone", "email", "office"}),
        execute=directory.run,
        read=directory_normalize.FIELDS,
        sort=directory_normalize.SORT,
    ),
    "calendar": Capability(
        describes=(
            "academic calendar dates, term milestones, breaks, finals, and registration/add-drop "
            "deadlines, represented by canonical calendar families and kinds; not arbitrary "
            "topics or campus activities"
        ),
        filters={
            "family": enum(
                "application",
                "break",
                "finals",
                "grades",
                "grading",
                "graduation",
                "holiday",
                "instruction",
                "other",
                "registration",
                "tuition",
                "withdrawal",
                description=(
                    "a broad calendar concept; use this when the question names the activity "
                    "without identifying a specific policy subtype"
                ),
            ),
            "kind": enum(
                "add_drop_deadline",
                "application_deadline",
                "break",
                "classes_begin",
                "classes_end",
                "conferral",
                "finals",
                "grades_due",
                "grading_option_deadline",
                "holiday",
                "independent_study_registration_deadline",
                "other",
                "tuition_refund_deadline",
                "withdrawal_deadline",
                description=(
                    "a specific calendar subtype; use only when the question identifies that "
                    "distinction"
                ),
            ),
            "term": entity("academic_term"),
            "session": entity("academic_session"),
            "date": date(),
            "startsAfter": instant(),
            "startsBefore": instant(),
        },
        fields=frozenset(
            {
                "family",
                "kind",
                "term",
                "termId",
                "session",
                "sessionId",
                "date",
                "startsAt",
                "title",
                "description",
            }
        ),
        execute=calendar.run,
        read=calendar_normalize.FIELDS,
        sort=calendar_normalize.SORT,
        normalize=calendar_normalize.resolve_filters,
        # A term runs several sessions and files several kinds of deadline in
        # each. Asked which day is the last to register, a plan that names
        # neither has not asked about one of them — it has asked about all of
        # them, and each is an answer.
        parallel=frozenset({"kind", "sessionId"}),
    ),
    "clubs": Capability(
        describes="student organizations, clubs, organization categories, and Greek life",
        filters={"name": entity("student_organization"), "category": text()},
        fields=frozenset({"name", "category", "websiteUrl"}),
        execute=clubs.run,
        read=clubs_normalize.FIELDS,
        sort=clubs_normalize.SORT,
    ),
    "locations": Capability(
        describes=(
            "physical campus buildings, offices, rooms, parking areas, and map links; not hours"
        ),
        filters={
            "name": entity("campus_location"),
            "type": enum("building", "office", "parking", "layer"),
            "building": entity("campus_building"),
            "room": text(),
        },
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
        filters={
            "name": entity("academic_program"),
            "subject": entity("academic_subject"),
            "programKind": enum(
                "major", "minor", "certificate", "undeclared", "other", "special"
            ),
            "degree": entity("academic_degree"),
            "school": entity("academic_school"),
            "level": enum("undergraduate", "graduate"),
        },
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
            "filters": [
                capability.filters[name].catalogue(name) for name in sorted(capability.filters)
            ],
            "fields": sorted(capability.fields),
        }
        for name, capability in CAPABILITIES.items()
    ]
