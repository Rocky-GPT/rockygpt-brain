"""What code says about a shuttle question, from the timetable and the clock, with no model.

A pure function of (plan, timetable, now): it reads nothing, writes nothing and calls
nothing. Code follows Jev's top pick even when Jev is unsure, so a found answer restates the
operation, the route, the day with its date, and the stop or "any stop", and an empty result
names the stop and the day, so a student can see a wrong pick. An empty result is a plain
sentence, never a guess.

The data is a weekly timetable with no dates for when it starts or ends, so every sentence
says what the published timetable lists, and never that a shuttle is running. Its copy date is
always stated, and a copy older than its source's freshness limit says it may be outdated. A
stop is a place a trip stops, not a destination: the data doesn't say which way a trip
serves it.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from math import ceil
from typing import Literal

from rockygpt_brain.campus import Route, ServiceDay, Timetable, Trip
from rockygpt_brain.context import CAMPUS_TIMEZONE
from rockygpt_brain.contract import Citation

Operation = Literal["next", "first", "last"]
Kind = Literal["found", "none_left", "no_service", "stop_not_served"]

DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
DAY_KINDS: dict[ServiceDay, str] = {"weekday": "weekdays", "saturday": "Saturdays",
                                     "sunday": "Sundays"}
NO_DATES = "No dates are given for when this timetable starts or ends."
ONE_PAGE = ("The link is the main shuttle page on record. The Saturday, Sunday and Route 17 "
            "timetables may be on other Ramapo pages that are not on record.")
# A day this far ahead, or any day already past, gets a sentence saying the timetable has no dates.
FAR_DAYS = 14


@dataclass(frozen=True)
class ShuttlePlan:
    operation: Operation
    day: date  # the campus date the answer is about, worked out by code from Jev's pick
    stop: str | None = None  # a key of Timetable.stop_menu; None means any stop


@dataclass(frozen=True)
class ShuttleAnswer:
    text: str
    citations: tuple[Citation, ...]
    kind: Kind
    stale: bool  # the copy is older than its source's freshness limit


def service_day_of(day: date) -> ServiceDay:
    return "saturday" if day.weekday() == 5 else "sunday" if day.weekday() == 6 else "weekday"


def time_text(minutes: int) -> str:
    hour, minute = divmod(minutes, 60)
    return f"{hour % 12 or 12}:{minute:02d} {'AM' if hour < 12 else 'PM'}"


def short_date(day: date) -> str:
    return f"{MONTHS[day.month - 1]} {day.day}"


def day_text(day: date, today: date) -> str:
    text = f"{DAY_NAMES[day.weekday()]}, {short_date(day)}"
    if day.year != today.year:
        text += f", {day.year}"
    label = {today: " (today)", today + timedelta(days=1): " (tomorrow)",
             today - timedelta(days=1): " (yesterday)"}.get(day, "")
    return text + label


def pick(trips: list[Trip], plan: ShuttlePlan, today: date, minutes: int) -> Trip | None:
    """The trip a route has for this question, among the trips that fit the stop."""
    if not trips:
        return None
    if plan.operation == "last":
        return max(trips, key=lambda trip: trip.departs)
    if plan.operation == "next" and plan.day == today:
        later = [trip for trip in trips if trip.departs > minutes]
        return min(later, key=lambda trip: trip.departs) if later else None
    # First, or "next" on another day, where every trip is still to come.
    return min(trips, key=lambda trip: trip.departs)


def visits(trip: Trip, key: str) -> int | None:
    """When the trip first stops at the place, in minutes since midnight."""
    return next((stop.minutes for stop in trip.stops if stop.key == key), None)


def cite(route: Route, trip: Trip | None, table: Timetable, stale: bool) -> Citation:
    """The record an answer rests on. Its only link is the source's own."""
    record = route if trip is None else trip
    return Citation(
        id=record.id, title=table.source.title, url=table.source.url,
        record_title=route.name if trip is None else f"{route.name}, {time_text(trip.departs)}",
        collection="shuttle_routes" if trip is None else "shuttle_trips",
        collected_at=(table.collected_at if trip is None else trip.collected_at).isoformat(),
        freshness="stale" if stale else "fresh", trust_tier=table.source.trust_tier,
        limitations=[NO_DATES, ONE_PAGE])


def age_limit(hours: int) -> str:
    """The source's freshness limit as a person says it: 6 hours, 48 hours, 7 days."""
    if hours < 48:
        return f"{hours} hour{'' if hours == 1 else 's'}"
    return f"{ceil(hours / 24)} days"


def copied(table: Timetable, stale: bool) -> str:
    sentence = ("Copied from Ramapo's published shuttle timetables on "
                f"{short_date(table.collected_at.astimezone(CAMPUS_TIMEZONE).date())}.")
    if stale:
        sentence += (f" That was more than {age_limit(table.source.freshness_sla_hours)} ago, "
                     "so it may be outdated.")
    return sentence


def paragraphs(*parts: str) -> str:
    return "\n\n".join(part for part in parts if part)


def answer(plan: ShuttlePlan, table: Timetable, now: datetime) -> ShuttleAnswer:
    now = now.astimezone(CAMPUS_TIMEZONE)
    today, minutes = now.date(), now.hour * 60 + now.minute
    stale = table.is_stale(now)
    when = day_text(plan.day, today)
    choice = next((c for c in table.stop_menu if c.key == plan.stop), None)
    place = choice.place if choice else plan.stop
    where = "to any stop" if plan.stop is None else f"with a stop at {place}"
    notes: list[str] = []
    if plan.day < today or (plan.day - today).days > FAR_DAYS:
        notes.append("The timetable gives no dates, so it may not apply on "
                     f"{short_date(plan.day)}.")
    footer = copied(table, stale)
    routes = table.routes_on(service_day_of(plan.day))

    if not routes:
        return ShuttleAnswer(
            paragraphs(f"The published timetable lists no shuttle from Ramapo on {when}.",
                       " ".join([*notes, footer])), (), "no_service", stale)

    fitting = {route.id: [trip for trip in route.trips
                          if plan.stop is None or visits(trip, plan.stop) is not None]
               for route in routes}
    if plan.stop is not None and not any(fitting.values()):
        elsewhere = [name for kind, name in DAY_KINDS.items() if choice is not None and any(
            visits(trip, choice.key) is not None
            for route in table.routes_on(kind) for trip in route.trips)]
        also = (f"It does list shuttles with that stop on {' and '.join(elsewhere)}."
                if elsewhere else "It lists no shuttle with that stop on any day.")
        return ShuttleAnswer(
            paragraphs(f"The published timetable lists no shuttle from Ramapo {where} on "
                       f"{when}. {also}", " ".join([*notes, footer])),
            tuple(cite(route, None, table, stale) for route in routes), "stop_not_served", stale)

    picked = [(route, trip) for route in routes
              if (trip := pick(fitting[route.id], plan, today, minutes)) is not None]
    if not picked:
        # "Next" today with nothing left: say when the last one went.
        every = [(route, trip) for route in routes for trip in fitting[route.id]]
        last_at = max(trip.departs for _, trip in every)
        last = [(route, trip) for route, trip in every if trip.departs == last_at]
        names = " and ".join(sorted(route.name for route, _ in last))
        return ShuttleAnswer(
            paragraphs(f"The published timetable lists no more shuttles from Ramapo {where} on "
                       f"{when} after {time_text(minutes)}. The last one left at "
                       f"{time_text(last_at)} ({names}).", " ".join([*notes, footer])),
            tuple(cite(route, trip, table, stale) for route, trip in last), "none_left", stale)

    picked.sort(key=lambda item: (item[1].departs, item[0].name))
    word = "next" if plan.operation == "next" and plan.day == today else (
        "last" if plan.operation == "last" else "first")
    entries: list[str] = []
    any_left = False
    for route, trip in picked:
        left = word != "next" and plan.day == today and trip.departs <= minutes
        any_left = any_left or left
        entry = (f"{route.name} left at {time_text(trip.departs)} today" if left
                 else f"{route.name} leaves at {time_text(trip.departs)}")
        if choice is not None and (at := visits(trip, choice.key)) is not None:
            entry += f" and {'stopped' if left else 'stops'} at {choice.place} at {time_text(at)}"
        entries.append(entry)
    if word == "next":
        # A route with trips that fit but none left is not the only shuttle: say so.
        running = {route.id for route, _ in picked}
        for route in routes:
            if fitting[route.id] and route.id not in running:
                last_left = max(trip.departs for trip in fitting[route.id])
                with_stop = "" if plan.stop is None else f" with a stop at {place}"
                notes.insert(0, f"{route.name} has no more trips{with_stop} after "
                                f"{time_text(minutes)} (its last left at {time_text(last_left)}).")
    if any_left:
        notes.append(f"The time now is {time_text(minutes)}.")
    head = f"The published timetable lists the {word} shuttle from Ramapo {where} on {when}"
    if word == "next":
        head += f" after {time_text(minutes)}"
    if len(entries) == 1:
        main = f"{head}: the {entries[0]}."
    else:
        main = f"{head}, one per route:\n\n" + "\n".join(f"- {entry}" for entry in entries)
    return ShuttleAnswer(
        paragraphs(main, " ".join([*notes, footer])),
        tuple(cite(route, trip, table, stale) for route, trip in picked), "found", stale)
