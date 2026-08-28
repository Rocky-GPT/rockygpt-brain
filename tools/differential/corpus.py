"""The cases two DataPort implementations are held to.

Every case pins its own clock. Nothing here may read the wall clock, because a
differential run whose two sides saw different instants reports drift that is
not there: `timeScope: "remaining"` on transportation drops trips that have
already left, so the same port called a minute apart legitimately answers
differently. A pinned `now` is what makes a diff mean "the implementations
disagree" rather than "time passed between the two calls".

The values are real ones from the Ramapo dataset rather than invented strings.
A filter matching nothing produces two empty lists, which compare equal and
prove nothing — the most expensive way for this harness to lie is to pass.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

CAMPUS = ZoneInfo("America/New_York")

# A Wednesday in term, mid-morning: dining is between meals, shuttles have both
# departed and remaining trips, and campus hours are open. A weekend or a break
# instant would leave half the capabilities answering "closed" and comparing
# equal for the wrong reason.
PINNED_NOW = datetime(2026, 9, 16, 10, 30, tzinfo=CAMPUS)

# Saturday of the same week. Shuttle timetables are per service day and the
# weekend tables are a separate code path in the repository layer.
PINNED_WEEKEND = datetime(2026, 9, 19, 14, 0, tzinfo=CAMPUS)


@dataclass(frozen=True)
class Case:
    """One capability lookup, run identically against both implementations."""

    name: str
    capability: str
    filters: Mapping[str, str]
    now: datetime = PINNED_NOW
    # Why this case is in the corpus. Printed beside any divergence it finds,
    # so a failure arrives with the reason someone thought it was worth testing.
    covers: str = ""


def _date(moment: datetime) -> str:
    return moment.date().isoformat()


CASES: tuple[Case, ...] = (
    # --- transportation -----------------------------------------------------
    # getShuttleTrips carries historical default-route behaviour that
    # listShuttleTrips deliberately does not. An unfiltered call is where a
    # port that picked the wrong one shows itself.
    Case(
        name="transportation/unfiltered",
        capability="transportation",
        filters={},
        covers="full_day scope, no route default applied",
    ),
    Case(
        name="transportation/remaining",
        capability="transportation",
        filters={"departingAfter": "2:00 PM"},
        covers="timeScope=remaining truncation against a pinned asOf",
    ),
    Case(
        name="transportation/route",
        capability="transportation",
        filters={"route": "Roadrunner Express"},
        covers="route filter on the named entity",
    ),
    Case(
        name="transportation/origin-destination",
        capability="transportation",
        filters={"origin": "Ramapo College", "destination": "Garden State Plaza"},
        covers="separate origin/destination filters, the V2-only path",
    ),
    Case(
        name="transportation/weekend",
        capability="transportation",
        filters={"date": _date(PINNED_WEEKEND)},
        now=PINNED_WEEKEND,
        covers="Saturday service day selects a different timetable",
    ),
    # The fetch limit is 100. Two ports returning the same 100 trips in a
    # different order feed a different 100 to the answer once anything above
    # this truncates, so ordering is a finding, not a formatting detail.
    Case(
        name="transportation/limit-boundary",
        capability="transportation",
        filters={"date": _date(PINNED_NOW)},
        covers="ordering at the 100-record fetch limit",
    ),
    # --- hours --------------------------------------------------------------
    # findCampusHoursByVenue exists so a resolved venue is fetched rather than
    # word-matched; the bug it closed returned a different building. Both the
    # named and unnamed paths are here because they are different SQL.
    Case(
        name="hours/campus-by-venue",
        capability="hours",
        filters={"name": "Library (Main Building)", "kind": "campus"},
        covers="exact venue fetch, not word overlap",
    ),
    Case(
        name="hours/campus-unnamed",
        capability="hours",
        filters={"kind": "campus"},
        covers="every campus venue for the pinned day",
    ),
    Case(
        name="hours/dining-seasonal",
        capability="hours",
        filters={"kind": "dining"},
        covers="seasonal override resolution at the pinned instant",
    ),
    # No kind: the executor gathers campus and dining concurrently and tags
    # each. Both ports must agree on both halves.
    Case(
        name="hours/both-kinds",
        capability="hours",
        filters={},
        covers="concurrent campus+dining gather and kind tagging",
    ),
    Case(
        name="hours/open-at",
        capability="hours",
        filters={"openAt": "8:00 PM", "kind": "campus"},
        covers="open/closed status computed against a pinned instant",
    ),
    # --- dining -------------------------------------------------------------
    Case(
        name="dining/today",
        capability="dining",
        filters={},
        covers="today's menu for the pinned date",
    ),
    Case(
        name="dining/meal",
        capability="dining",
        filters={"meal": "lunch"},
        covers="meal filter",
    ),
    Case(
        name="dining/dietary",
        capability="dining",
        filters={"dietary": "vegan"},
        covers="dietary flags survive the port boundary as booleans",
    ),
    # --- calendar -----------------------------------------------------------
    Case(
        name="calendar/term",
        capability="calendar",
        filters={"term": "Fall 2026"},
        covers="term entity resolution",
    ),
    Case(
        name="calendar/family",
        capability="calendar",
        filters={"family": "registration"},
        covers="canonical family, the parallel-answer path",
    ),
    Case(
        name="calendar/kind",
        capability="calendar",
        filters={"kind": "classes_begin"},
        covers="specific kind rather than family",
    ),
    Case(
        name="calendar/window",
        capability="calendar",
        filters={"startsAfter": _date(PINNED_NOW)},
        covers="instant comparison and date wire format",
    ),
    # --- events -------------------------------------------------------------
    Case(
        name="events/upcoming",
        capability="events",
        filters={},
        covers="unfiltered upcoming events relative to the pinned now",
    ),
    Case(
        name="events/organizer",
        capability="events",
        filters={"organizer": "Center for Student Involvement"},
        covers="organizer entity filter",
    ),
    Case(
        name="events/topic",
        capability="events",
        filters={"topic": "training"},
        covers="free-text topic search ranking",
    ),
    # --- directory ----------------------------------------------------------
    # findContactByName is the exact-match sibling of findContacts. A port that
    # implements one as the other looks correct until two people share a word.
    Case(
        name="directory/by-name",
        capability="directory",
        filters={"name": "Rikki Abzug"},
        covers="exact contact fetch",
    ),
    Case(
        name="directory/department",
        capability="directory",
        filters={"department": "Anisfield School of Business"},
        covers="department filter over the contact set",
    ),
    Case(
        name="directory/unfiltered",
        capability="directory",
        filters={},
        covers="the whole contact list, where ordering shows",
    ),
    # --- courses ------------------------------------------------------------
    # The subject filter is why nothing_matched exists: CS matched nothing
    # because the catalogue files computer science under CMPS. Both spellings
    # are here so a port that quietly normalises one is visible.
    Case(
        name="courses/subject-canonical",
        capability="courses",
        filters={"subject": "CMPS"},
        covers="subject code as the catalogue files it",
    ),
    Case(
        name="courses/subject-colloquial",
        capability="courses",
        filters={"subject": "CS"},
        covers="a subject the catalogue does not use — empty is the correct answer",
    ),
    Case(
        name="courses/code",
        capability="courses",
        filters={"code": "CMPS 147"},
        covers="course code lookup",
    ),
    # --- programs -----------------------------------------------------------
    Case(
        name="programs/school",
        capability="programs",
        filters={"school": "Anisfield School of Business"},
        covers="school filter",
    ),
    Case(
        name="programs/kind-level",
        capability="programs",
        filters={"programKind": "major", "level": "undergraduate"},
        covers="two enum filters combined",
    ),
    Case(
        name="programs/name",
        capability="programs",
        filters={"name": "Accounting"},
        covers="program name search",
    ),
    # --- clubs --------------------------------------------------------------
    Case(
        name="clubs/unfiltered",
        capability="clubs",
        filters={},
        covers="all 255 clubs — the widest result in the corpus",
    ),
    Case(
        name="clubs/category",
        capability="clubs",
        filters={"category": "Athletics"},
        covers="category is a prefix of several real categories",
    ),
    # --- locations ----------------------------------------------------------
    Case(
        name="locations/unfiltered",
        capability="locations",
        filters={},
        covers="every mapped location",
    ),
    Case(
        name="locations/type",
        capability="locations",
        filters={"type": "parking"},
        covers="type enum filter",
    ),
    Case(
        name="locations/name",
        capability="locations",
        filters={"name": "Bradley Center"},
        covers="location entity with aliases",
    ),
)


def cases(only: str | None = None) -> tuple[Case, ...]:
    """Every case, or those whose name or capability starts with `only`."""
    if only is None:
        return CASES
    prefix = only.strip().casefold()
    return tuple(
        case
        for case in CASES
        if case.name.casefold().startswith(prefix) or case.capability.casefold().startswith(prefix)
    )


def horizon() -> timedelta:
    """How far apart the corpus's pinned instants sit, for the run header."""
    moments = [case.now for case in CASES]
    return max(moments) - min(moments)
