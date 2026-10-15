"""Which shuttle leaves when, worked out by code from the published timetables.

A timetable is one route for one class of service day (weekday, Saturday, Sunday): its trips each
leave campus at a time, call at stops in order and return. Trips are never dated. A student asks
"the next shuttle to the train station"; the Brain turns that into a date on the campus clock,
that date into a service day, keeps the trips that call at the stop, and reads the time. A writer
is handed the answer, not the list and not the clock.

Rules, all in code:
  - A weekday is two timetables (the express to the train, and the long loop). Both are
    considered, because the next shuttle to a stop is the earliest of every trip that calls there.
  - "Next" is strictly after now: at the minute a trip leaves it has left. Once the last trip of
    the day has left, the first trip of the next service day is named with its date.
  - A stop is matched by its published name with "Arrive"/"Depart" prefixes, case and spacing
    folded. Nothing is guessed from a similar name.
  - A trip's first call at a stop is where it is reached going out from campus; its last is where
    it is left going back. `reached_at` is the first, `leaves_stop_at` the last. A ride back to
    campus from a stop is timed by that last call: "the next shuttle back from the train" is the
    next pickup there, even on a trip that left campus before now.
  - Only what the timetable lists is said. The timetable is scheduled service: it says nothing
    about weather, closures or where a shuttle is right now.
"""

import re
from datetime import date, timedelta
from typing import Any

SERVICE_DAYS = ("weekday", "saturday", "sunday")
WHICH = ("next", "first", "last", "all")
MAX_LISTED = 2  # How many upcoming departures "next" shows: the one asked for and the one after.
_CLOCK = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*([AaPp])\.?[Mm]\.?\s*$")
_PREFIX = re.compile(r"^(?:arrive|depart)\s+", re.IGNORECASE)


def service_day(day: date) -> str:
    """The class of service day a date falls in: Saturday, Sunday, or a weekday."""
    return ("weekday", "weekday", "weekday", "weekday", "weekday", "saturday", "sunday")[
        day.weekday()]


def minutes(text: Any) -> int | None:
    """A published clock time ("7:00 AM") as minutes after midnight; None when it is not one."""
    match = _CLOCK.match(text) if isinstance(text, str) else None
    if not match or not 1 <= int(match[1]) <= 12 or int(match[2]) > 59:
        return None
    return (int(match[1]) % 12 + (12 if match[3].casefold() == "p" else 0)) * 60 + int(match[2])


_SAID = re.compile(r"^\s*(\d{1,2})(?::(\d{2}))?\s*([AaPp])\.?[Mm]\.?\s*$")
_SAID_24 = re.compile(r"^\s*([01]?\d|2[0-3]):([0-5]\d)\s*$")


def said_minutes(text: Any) -> int | None:
    """A time as a student or the model writes it ("6pm", "6:30 PM", "18:30") in minutes after
    midnight, or None when it is not a clock time. The timetable's own `minutes` stays strict."""
    if not isinstance(text, str):
        return None
    twelve, full = _SAID.match(text), _SAID_24.match(text)
    if twelve and 1 <= int(twelve[1]) <= 12 and int(twelve[2] or 0) <= 59:
        return ((int(twelve[1]) % 12 + (12 if twelve[3].casefold() == "p" else 0)) * 60
                + int(twelve[2] or 0))
    if full:
        return int(full[1]) * 60 + int(full[2])
    return None


def clock(total: int) -> str:
    """The way the timetable writes a time: 7:00 AM."""
    hour, minute = divmod(total % 1440, 60)
    return f"{hour % 12 or 12}:{minute:02d} {'PM' if hour >= 12 else 'AM'}"


def stop_key(name: Any) -> str:
    """A stop name folded for comparison: no Arrive/Depart prefix, one case, one spacing."""
    text = " ".join(str(name).replace(" ", " ").split())
    return _PREFIX.sub("", text).replace("/ ", "/").casefold()


def stop_name(name: Any) -> str:
    """A stop's name as a student would read it: prefix and stray spaces removed, case kept."""
    text = " ".join(str(name).replace(" ", " ").split())
    return _PREFIX.sub("", text).replace("/ ", "/")


def _trip(route: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any] | None:
    leaves = minutes(raw.get("departure"))
    if leaves is None:
        return None
    stops = []
    last = leaves
    for stop in raw.get("stops") or []:
        at = minutes(stop.get("time")) if isinstance(stop, dict) else None
        if at is None or at < last:  # A time that runs backwards is not read.
            return None
        last = at
        stops.append({"name": stop_name(stop["location"]), "key": stop_key(stop["location"]),
                      "at": at})
    back = minutes(raw.get("arrival"))
    if back is not None and back < last:
        return None
    return {"route": route["route"], "service_day": route["service_day"], "leaves": leaves,
            "back": back, "stops": stops, "sequence": raw.get("sequence")}


def trips_of(routes: list[dict[str, Any]], service: str) -> list[dict[str, Any]]:
    """Every readable trip of the given service day, in departure order. A trip with a clock the
    code cannot read, or times that run backwards, is left out rather than guessed."""
    found = [trip for route in routes if route.get("service_day") == service
             for raw in route.get("trips", []) if (trip := _trip(route, raw)) is not None]
    return sorted(found, key=lambda trip: (trip["leaves"], trip["route"]))


def stop_summary(text: Any, limit: int = 240) -> str | None:
    """A stop description's opening sentences, without links, as many whole sentences as fit in
    `limit` (the first is cut at a word when it alone is longer): the stores and places a page
    names are often in its second sentence ("It features: ShopRite, ...")."""
    if not isinstance(text, str):
        return None
    plain = " ".join(re.sub(r"\s*\(https?://[^)]*\)", "", text).split())
    kept = ""
    for sentence in re.split(r"(?<=[.!?])\s", plain):
        if kept and len(kept) + 1 + len(sentence) > limit:
            break
        kept = f"{kept} {sentence}".strip()
    if len(kept) > limit:
        kept = kept[:limit].rsplit(" ", 1)[0] + "…"
    return kept or None


def stops_served(routes: list[dict[str, Any]]) -> list[str]:
    """The stop names the published timetables list, once each, in first-seen order."""
    seen: dict[str, str] = {}
    for service in SERVICE_DAYS:
        for trip in trips_of(routes, service):
            for stop in trip["stops"]:
                seen.setdefault(stop["key"], stop["name"])
    return list(seen.values())


_DAY_WORDS = {"weekday": "weekdays", "saturday": "Saturdays", "sunday": "Sundays"}


def _stop_label(routes: list[dict[str, Any]], key: str) -> tuple[str | None, list[str]]:
    """A stop's published name and the kinds of day on which any trip calls at it."""
    name: str | None = None
    days = []
    for service in SERVICE_DAYS:
        for trip in trips_of(routes, service):
            for stop in trip["stops"]:
                if stop["key"] == key:
                    name = name or stop["name"]
                    if _DAY_WORDS[service] not in days:
                        days.append(_DAY_WORDS[service])
    return name, days


def _pickup(trip: dict[str, Any], key: str) -> int:
    """When a trip last calls at a stop: where it picks up on its way back to campus."""
    return [stop for stop in trip["stops"] if stop["key"] == key][-1]["at"]


def _shown(trip: dict[str, Any], key: str | None, back: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {"route": trip["route"], "leaves_campus": clock(trip["leaves"])}
    if key is not None:
        calls = [stop for stop in trip["stops"] if stop["key"] == key]
        out["stop"] = calls[0]["name"]
        out["reached_at"] = clock(calls[0]["at"])
        if len(calls) > 1 or back:
            out["leaves_stop_at"] = clock(calls[-1]["at"])
    out["back_on_campus"] = clock(trip["back"]) if trip["back"] is not None else None
    out["calls_at"] = list(dict.fromkeys(stop["name"] for stop in trip["stops"]))
    return out


def departures(
    routes: list[dict[str, Any]], day: date, now_minute: int | None, which: str,
    stop: str | None, after: int | None = None, by: int | None = None, times: bool = True,
    back: bool = False,
) -> dict[str, Any]:
    """The answer to "the {next, first, last} shuttle (to {stop}) on this date".

    `now_minute` is the campus time of day when `day` is today, and None for any other date (so
    nothing on another date has "already left"). `after` keeps only trips that leave campus after
    that minute ("after my 6pm class"); `by` keeps only trips that reach the stop by that minute
    ("a 6:50 train") and answers with the latest of them. `times` is whether "all" lists every
    trip; without it "all" is the day's first, last and count. The result is the facts of the
    answer, not prose: `trips` are the departures to say, `none_left` that every trip today
    has left. `back` is a ride from the stop back to campus: every time and filter then reads
    the trip's pickup there (its last call), not when it left campus."""
    key = stop_key(stop) if stop else None
    service = service_day(day)
    todays = trips_of(routes, service)
    calling = [trip for trip in todays
               if key is None or any(s["key"] == key for s in trip["stops"])]
    published, served_on = _stop_label(routes, key) if key else (None, [])
    result: dict[str, Any] = {
        "date": day.isoformat(), "service_day": service, "which": which, "stop": published,
    }
    back = back and key is not None
    # The minute each trip is said by: leaving campus, or picking up at the stop to come back.
    def when(trip: dict[str, Any]) -> int:
        return _pickup(trip, key) if back and key is not None else trip["leaves"]
    if back:
        result["back_to_campus"] = True
        calling = sorted(calling, key=lambda trip: (when(trip), trip["route"]))
    if not calling:
        result["service"] = "none"
        if key:
            result["served_on"] = served_on  # The days that do call there, when this one does not.
        return result
    day_calling = calling
    if after is not None:
        result["after"] = clock(after)
        calling = [t for t in calling if when(t) > after]
    upcoming = [t for t in calling if now_minute is None or when(t) > now_minute]
    if by is not None and (key is None or back):
        result["by_needs_a_stop"] = True  # "Get there by" means nothing without a place.
        by = None
    if by is not None:
        result["by"] = clock(by)
        reaching = [t for t in upcoming
                    if next(s for s in t["stops"] if s["key"] == key)["at"] <= by]
        result["trips"] = [_shown(t, key) for t in reaching[-MAX_LISTED:]]
        result["none_by"] = not reaching
        if not reaching and upcoming:
            result["earliest_reaches_stop"] = clock(
                next(s for s in upcoming[0]["stops"] if s["key"] == key)["at"])
        return result
    if not calling:
        # Nothing leaves after that time on this date: the day's last trip is the edge of it.
        result.update(trips=[], none_left=True, last_left_at=clock(when(day_calling[-1])))
        result.update(_next_service(routes, day, key, back))
        return result
    if which == "all":
        picked = calling
        if key is None:
            # Where the shuttle goes that day: the stops its trips call at, in first-seen order.
            result["stops_served"] = list(dict.fromkeys(
                s["name"] for t in calling for s in t["stops"]))
        result["first_leaves_campus"] = clock(calling[0]["leaves"])
        result["last_leaves_campus"] = clock(calling[-1]["leaves"])
        result["trip_count"] = len(calling)
        if not times:
            picked = []
    elif which == "first":
        picked = [t for t in calling if when(t) == when(calling[0])]
    elif which == "last":
        picked = [t for t in calling if when(t) == when(calling[-1])]
    else:
        picked = upcoming[:MAX_LISTED]
    result["trips"] = [_shown(t, key, back) for t in picked]
    if which in {"first", "last"} and now_minute is not None:
        result["already_left"] = all(when(t) <= now_minute for t in picked)
    if which == "next":
        result["none_left"] = not upcoming
        if not upcoming:
            result["last_left_at"] = clock(when(day_calling[-1]))
            result.update(_next_service(routes, day, key, back))
    return result


def _next_service(routes: list[dict[str, Any]], day: date, key: str | None,
                  back: bool = False) -> dict[str, Any]:
    """The first trip on the next day that has one to the stop, so "none left" is never a dead
    end. Empty when no day in the coming week has one."""
    for ahead in range(1, 8):
        later = day + timedelta(days=ahead)
        coming = [t for t in trips_of(routes, service_day(later))
                  if key is None or any(s["key"] == key for s in t["stops"])]
        if coming:
            if back and key is not None:
                coming = sorted(coming, key=lambda t: _pickup(t, key))
                first = [t for t in coming if _pickup(t, key) == _pickup(coming[0], key)]
            else:
                first = [t for t in coming if t["leaves"] == coming[0]["leaves"]]
            return {"next_service": {"date": later.isoformat(),
                                     "trips": [_shown(t, key, back) for t in first]}}
    return {}


SCOPE_TEXT = ("These are the scheduled times on the posted timetable. They are not live: "
              "weather, holidays and delays are not in them.")
# A special-day timetable (a holiday) the page names without a year: offered, never applied.
SPECIAL_TEXT = ("Ramapo also posts a special shuttle schedule for {days}. On that day the special "
                "schedule applies, not the regular one.")


def table_of(envelopes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The routes' trips as the plain timetable the functions above read, from the reader's
    route envelopes. A route that has no readable timetable is left out."""
    table = []
    for envelope in envelopes:
        for prop in envelope.get("properties", []):
            if prop.get("key") != "trips":
                continue
            for value in prop.get("values", []):
                body = value.get("value")
                if isinstance(body, dict) and isinstance(body.get("trips"), list):
                    table.append({"route": body["route"], "service_day": body["service_day"],
                                  "trips": body["trips"]})
    return table


def _date_text(iso: str) -> str:
    day = date.fromisoformat(iso)
    return f"{day.strftime('%A, %B')} {day.day}"


def describe(answer: dict[str, Any]) -> str:
    """The answer as plain sentences, for a reply written by code. A writer says the same facts."""
    when = _date_text(answer["date"])
    stop = answer.get("stop")
    to = f" to {stop}" if stop else ""
    if answer.get("stop_unknown"):
        return (f"The posted timetable has no stop called “{answer.get('asked')}”. "
                f"The stops it lists are {', '.join(answer.get('published_stops', []))}.")
    if answer.get("service") == "none":
        text = f"The posted timetable lists no shuttle{to} on {when}."
        if stop and answer.get("served_on"):
            text += f" {stop} is served on {' and '.join(answer['served_on'])}."
        return text
    which = answer["which"]
    where = f" Every trip leaves from {answer['departs_from']}." if answer.get("departs_from") else ""

    def one(trip: dict[str, Any]) -> str:
        text = f"{trip['leaves_campus']} ({trip['route']})"
        if trip.get("reached_at"):
            text += f", reaching {trip['stop']} at {trip['reached_at']}"
        if trip.get("leaves_stop_at"):
            text += f", leaving {trip['stop']} for campus at {trip['leaves_stop_at']}"
        if trip.get("back_on_campus"):
            text += f", back on campus at {trip['back_on_campus']}"
        return text

    trips = answer.get("trips", [])
    if answer.get("by"):
        if answer.get("none_by"):
            text = f"No shuttle{to} is listed that reaches the stop by {answer['by']} on {when}."
            if answer.get("earliest_reaches_stop"):
                text += f" The earliest reaches it at {answer['earliest_reaches_stop']}."
            return text
        return (f"Shuttles{to} that reach the stop by {answer['by']} on {when}: "
                + "; ".join(one(t) for t in trips) + ".")
    if answer.get("none_left"):
        since = f" after {answer['after']}" if answer.get("after") else ""
        text = (f"No more shuttles{to} are listed{since} on {when}; the last one left at "
                f"{answer['last_left_at']}.")
        later = answer.get("next_service")
        if later:
            text += (f" The first on {_date_text(later['date'])} leaves at "
                     f"{later['trips'][0]['leaves_campus']}.")
        return text
    label = {"next": "Next", "first": "First", "last": "Last", "all": "All"}[which]
    if which == "all":
        if answer.get("stops_served") and not trips:
            return (f"On {when} the shuttle calls at {', '.join(answer['stops_served'])}."
                    + where)
        return f"Shuttles{to} on {when}: " + "; ".join(one(t) for t in trips) + "." + where
    since = f" after {answer['after']}" if answer.get("after") else ""
    lead = f"{label} shuttle{to}{since} on {when}: " + "; ".join(one(t) for t in trips) + "."
    if answer.get("already_left"):
        lead += " It has already left today."
    return lead
