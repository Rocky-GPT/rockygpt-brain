"""What Rocky can do.

One entry per lookup, and for each one the fields a plan may name: `filters` to
narrow with, `fields` to sort by, compare, or read. A plan naming anything else
is not run.

This is the file that grows, and it grows by capability — a new kind of thing
Rocky can look up — never by question. There is no entry here for "the next
shuttle" or "the first shuttle to the mall", because those are one capability
asked twice, with different filters and a different sort.

The field names are the plan's vocabulary, not a storage schema. Translating
`date` into whatever the data service calls it is the executor's job.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Capability:
    describes: str  # one line, handed to the planner as written
    filters: frozenset[str]  # fields a plan may narrow on
    fields: frozenset[str]  # fields a plan may sort by, compare, or read


CAPABILITIES: dict[str, Capability] = {
    "shuttle": Capability(
        describes="shuttle and bus departures",
        filters=frozenset({"date", "departingAfter", "route", "origin", "destination"}),
        fields=frozenset({"departureTime", "arrivalTime", "route", "origin", "destination"}),
    ),
    "dining": Capability(
        describes="opening hours of dining halls and cafes",
        filters=frozenset({"date", "venue"}),
        fields=frozenset({"venue", "opensAt", "closesAt"}),
    ),
    "menu": Capability(
        describes="what is being served, by day, venue and meal",
        filters=frozenset({"date", "venue", "meal"}),
        fields=frozenset({"item", "station", "meal", "venue"}),
    ),
    "directory": Capability(
        describes="campus offices and staff, with phone numbers and emails",
        filters=frozenset({"name", "department", "category"}),
        fields=frozenset({"name", "department", "phone", "email", "office"}),
    ),
    "location": Capability(
        describes="buildings, offices and parking on the campus map",
        filters=frozenset({"name", "type", "building"}),
        fields=frozenset({"name", "type", "building", "room"}),
    ),
}


def catalogue() -> list[dict[str, Any]]:
    """The registry as the planner is shown it."""
    return [
        {
            "capability": name,
            "describes": capability.describes,
            "filters": sorted(capability.filters),
            "fields": sorted(capability.fields),
        }
        for name, capability in CAPABILITIES.items()
    ]
