"""What Rocky can look up. The only such list.

One entry per lookup, and an entry exists only when there is code behind it.
That rule is the whole point of this file: the registry is what the planner is
shown, so anything listed here is something the planner may plan, and a plan
Rocky cannot run is a turn that fails. A capability that is declared but not
built is not a smaller product, it is a broken one — it fails at execution,
after the question was understood and a plan was made, where nothing can
recover it.

So `execute` is a required field. There is no way to add a name here without
supplying the code, and no second list to keep in step with this one.

An entry also carries how to read its records and how to sort them. Those used
to be a `capability == "shuttle"` branch in the generic code; keeping them
beside the declaration means adding a capability never means editing execute.

This is the file that grows, and it grows by capability — never by question.
There is no entry for "the next shuttle" or "the first shuttle to the mall",
because those are one capability asked twice with a different sort.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from rockygpt_brain.capabilities.shuttle import execute as shuttle
from rockygpt_brain.capabilities.shuttle import normalize
from rockygpt_brain.capabilities.shuttle.normalize import Reader
from rockygpt_brain.services.data import DataPort

#: What every executor looks like: the filters a plan named, the clock, and a
#: way to reach the data service. Deliberately not a `Plan` — a capability
#: does not need to know that lanes or operations exist.
Executor = Callable[[dict[str, str], datetime, DataPort], Awaitable[list[dict[str, Any]]]]


@dataclass(frozen=True, slots=True)
class Capability:
    describes: str  # one line, handed to the planner as written
    filters: frozenset[str]  # fields a plan may narrow on
    fields: frozenset[str]  # fields a plan may sort by, compare, or read
    execute: Executor  # required: no entry without the code to run it
    read: dict[str, Reader]  # how each published field comes off a record
    sort: dict[str, Reader]  # where sorting on the published value sorts wrongly


CAPABILITIES: dict[str, Capability] = {
    "shuttle": Capability(
        describes="shuttle and bus departures",
        filters=frozenset({"date", "departingAfter", "route", "origin", "destination"}),
        fields=frozenset({"departureTime", "arrivalTime", "route", "origin", "destination"}),
        execute=shuttle.run,
        read=normalize.FIELDS,
        sort=normalize.SORT,
    ),
}


def catalogue() -> list[dict[str, Any]]:
    """The registry as the planner is shown it. Names only what can run."""
    return [
        {
            "capability": name,
            "describes": capability.describes,
            "filters": sorted(capability.filters),
            "fields": sorted(capability.fields),
        }
        for name, capability in CAPABILITIES.items()
    ]
