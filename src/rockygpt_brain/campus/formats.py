"""Exact data formats with request and coverage checks, after model-selected retrieval.

A request quote only selects a candidate. It does not certify its meaning. Code
matches the entity, date and requested fields and rejects unconsumed qualifiers.
Unrecognized wording remains eligible for the ordinary reviewed prose path.
"""

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from pydantic import Field

from rockygpt_brain.campus.schedules import departure_summary, opening_intervals
from rockygpt_brain.contracts import Answer, AnswerPart, ChatMessage
from rockygpt_brain.retrieval.exact import ContactQuery, contact_answer, plain
from rockygpt_brain.retrieval.models import SearchQuery


class SearchCall(SearchQuery):
    request_text: str | None = Field(
        default=None,
        max_length=2000,
        description=(
            "For an exact menu, hours or departure request, quote its complete atomic "
            "part verbatim from the final user message, including qualifiers. Use null "
            "for interpretation, policy, planning or unresolved references. Different "
            "independent requests can each quote their own non-overlapping part. "
            "A separate private or unanswerable task does not make an exact part null. "
            "This only proposes an exact format; the server independently validates it."
        ),
    )


class ContactCall(ContactQuery):
    request_text: str | None = Field(
        default=None,
        max_length=2000,
        description="Verbatim complete contact-only part of the final request, or null.",
    )


@dataclass
class ExactPiece:
    quote: str
    answer: Answer
    complete: bool = True


def words(value: str) -> str:
    return " ".join(re.findall(r"[\w]+", value.casefold().replace("’", "'")))


# Function words only. Content words must match the requested data shape/fields.
GRAMMAR = set(
    "what whats when is are does do can you i me my the a an for at of on in to "
    "please show give tell list get and also it s".split()
)


def remove_phrase(text: str, phrase: str) -> tuple[str, bool]:
    pattern = rf"(?<!\w){re.escape(words(phrase))}(?!\w)"
    replaced, count = re.subn(pattern, " ", text)
    return " ".join(replaced.split()), count > 0


def leftovers(text: str) -> bool:
    return bool(set(words(text).split()) - GRAMMAR)


def independent_quote(quote: str, request: str) -> bool:
    """Do not exempt a phrase cut out of a qualified or negated request."""
    positions = list(re.finditer(re.escape(quote), request))
    if len(positions) != 1:
        return False
    match = positions[0]
    before, after = request[: match.start()].strip(), request[match.end() :].strip()
    starts_part = not before or bool(re.search(r"(?:[?.;]|\band)$", before, re.I))
    ends_part = not after or bool(re.match(r"^[?.;]|^and\b", after, re.I))
    return starts_part and ends_part


def request_date(text: str, now: datetime) -> tuple[date, str]:
    """Resolve explicit simple dates independently of the model's query date."""
    selected: set[date] = set()
    # Students type "tmrw": "atrium hours tmrw" named no day, so Jev read it as one to
    # work out and GPT wrote the lookup instead of code.
    for offset, marker in ((0, "today"), (0, "tonight"), (0, "now"), (1, "tomorrow"),
                           (1, "tmrw"), (1, "tmr"), (1, "tomorow")):
        text, found = remove_phrase(text, marker)
        if found:
            selected.add(now.date() + timedelta(days=offset))
    for match in list(re.finditer(r"\b\d{4} \d{2} \d{2}\b", text)):
        selected.add(date.fromisoformat(match[0].replace(" ", "-")))
        text = text.replace(match[0], " ")
    for weekday in range(7):
        name = (date(2026, 9, 14) + timedelta(days=weekday)).strftime("%A").casefold()
        text, found = remove_phrase(text, name)
        if found:
            # 'this Saturday' follows the same calendar-week rule as the controller.
            # 'next Saturday' is intentionally not erased: it needs interpretation.
            selected.add(now.date() + timedelta(days=weekday - now.weekday()))
            text, _ = remove_phrase(text, "this")
    if len(selected) > 1:
        raise ValueError("Conflicting dates")
    return next(iter(selected), now.date()), " ".join(text.split())


def entity_slot(
    text: str,
    records: list[dict[str, Any]],
    field: str,
    name_resolution: dict[str, Any] | None = None,
) -> tuple[str, str, bool]:
    names = {r["fields"].get(field) for r in records}
    if len(names) != 1 or not isinstance(name := next(iter(names)), str) or not name:
        raise ValueError("Unresolved entity")
    # Only exact published names or explicitly published identity aliases.
    aliases = {name}
    for record in records:
        aliases.update(a for a in record.get("aliases", []) if isinstance(a, str))
    if name_resolution is not None:
        if name_resolution["field"] != field or name_resolution["canonical_name"] != name:
            raise ValueError("Name resolution does not describe the returned entity")
        # This is code-resolved shorthand, not a newly asserted published alias.
        aliases.add(name_resolution["query"])
    found = False
    for alias in sorted(aliases, key=len, reverse=True):
        text, matched = remove_phrase(text, alias)
        found = found or matched
    return text, name, found


def current_records(output: dict[str, Any], day: date) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = output.get("records", [])
    if output.get("status") != "ok" or not records:
        raise ValueError("No verified records")
    identities: dict[str, str] = {}
    for record in records:
        if (
            not record.get("entity_id")
            or not record.get("source_key")
            or record.get("freshness") not in {"fresh", "static"}
            or record.get("trust_tier") not in {"official_primary", "official_secondary"}
            or record.get("content_truncated")
            or (record.get("valid_from") and day < date.fromisoformat(record["valid_from"]))
            or (record.get("valid_until") and day > date.fromisoformat(record["valid_until"]))
        ):
            raise ValueError("Record applicability is unverified")
        # A trip runs under one identity every day it runs; each day is its own record.
        identity = json.dumps([record["entity_id"], record["fields"].get("service_date")])
        value = json.dumps(record["fields"], sort_keys=True, ensure_ascii=False)
        if identity in identities and identities[identity] != value:
            raise ValueError("Conflicting field values")
        identities[identity] = value
    return records


def published(record: dict[str, Any], *fields: str) -> None:
    if any(record.get("coverage", {}).get("fields", {}).get(f) != "published" for f in fields):
        raise ValueError("Unknown field coverage")


def fact(text: str, records: list[dict[str, Any]]) -> AnswerPart:
    return AnswerPart(
        kind="campus_fact", text=text, evidence_ids=list(dict.fromkeys(r["id"] for r in records))
    )


def limitation(text: str) -> AnswerPart:
    return AnswerPart(kind="limitation", text=text, evidence_ids=[])


# Public Safety's verified numbers by published fact key, in the order they are shown.
SAFETY_FACTS = (("safety.emergency_phone", "emergency"),
                ("safety.non_emergency_phone", "non-emergency"))
# Shown first when Jev reads danger in a request, above whatever else the answer says.
# Written by code, never by a model; Public Safety's numbers follow from their records.
SAFETY_NET = {
    "self_harm": "If you might hurt yourself, please get help now. Call or text 988 "
    "(Suicide & Crisis Lifeline) any time, or call 911 if you're in immediate danger.",
    "danger": "If you're in danger right now, call 911.",
}


# Shown with every failed answer, written by code with no model call. When the budget ran
# out, "someone passed out and isn't waking up" got only "monthly AI allowance exhausted"
# (09-28): the safety block needs Jev, which the budget also stops.
FAILURE_HELP = ("If you or someone else is in danger, call 911. If you might hurt yourself, "
                "call or text 988 (Suicide & Crisis Lifeline).")


def failure_help(records: list[dict[str, Any]]) -> dict[str, Any]:
    """The emergency guidance a failure carries: fixed text, then Public Safety's numbers
    exactly as their records publish them, when there are any."""
    numbers = safety_part(records)
    sources = {record["url"]: {"title": str(record.get("title") or "Public Safety"),
                               "url": record["url"]}
               for record in records if str(record.get("url", "")).startswith("https://")}
    return {
        "text": FAILURE_HELP + (f" {numbers.text}" if numbers is not None else ""),
        "sources": list(sources.values()) if numbers is not None else [],
    }


def safety_part(records: list[dict[str, Any]]) -> AnswerPart | None:
    """Ramapo Public Safety's numbers exactly as their critical-fact records publish them."""
    by_key = {record.get("fields", {}).get("fact_key"): record for record in records}
    found = [(label, by_key[key]) for key, label in SAFETY_FACTS
             if key in by_key and isinstance(by_key[key]["fields"].get("fact_value"), str)]
    if not found:
        return None
    numbers = "; ".join(f"{label} {record['fields']['fact_value']}" for label, record in found)
    return fact(f"Ramapo College Public Safety: {numbers}.", [record for _, record in found])


def menu_parts(
    text: str,
    records: list[dict[str, Any]],
    query: SearchQuery,
    day: date,
    name_resolution: dict[str, Any] | None = None,
) -> list[AnswerPart]:
    if not query.filters or not query.filters.meal or query.filters.name:
        raise ValueError("A whole meal list requires a meal filter")
    # Broad meal questions need a reviewed selection, not an exhaustive dump of
    # ingredients, garnishes and dishes that share the same source station.
    if not set(words(text).split()) & {"menu", "list", "all", "full", "complete"}:
        raise ValueError("A meal overview needs a reviewed summary")
    meal = query.filters.meal
    text, meal_named = remove_phrase(text, meal)
    if not meal_named:
        raise ValueError("The requested meal is not established")
    text, venue, _ = entity_slot(text, records, "venue", name_resolution)
    for label in ("vegan", "vegetarian"):
        text, named = remove_phrase(text, label)
        value = getattr(query.filters, label)
        if named != (value is True) or value is False:
            raise ValueError("Dietary filter does not match the request")
    extra_fields = []
    for phrase, field in (("allergens", "allergens"), ("calories", "calories")):
        text, found = remove_phrase(text, phrase)
        if found:
            extra_fields.append(field)
    for marker in (
        "menu",
        "all",
        "full",
        "complete",
        "food",
        "items",
        "options",
        "choices",
        "listed",
        "available",
        "serving",
        "served",
        "eat",
        "dining",
    ):
        text, _ = remove_phrase(text, marker)
    if leftovers(text):
        raise ValueError("Unresolved menu qualifiers")
    lines = []
    for record in records:
        f = record["fields"]
        published(record, "name", "meal", "venue")
        if f["meal"].casefold() != meal.casefold() or record.get("valid_from") != str(day):
            raise ValueError("Wrong meal or date")
        for label in ("vegan", "vegetarian"):
            if getattr(query.filters, label) is True:
                published(record, label)
                if f.get(label) is not True:
                    raise ValueError("Wrong dietary flag")
        line = "- " + plain(f["name"])
        for field in extra_fields:
            coverage = record.get("coverage", {}).get("fields", {}).get(field)
            value = f.get(field)
            if coverage != "published" or value is None or value == []:
                rendered = "not specified in the published menu"
            else:
                rendered = ", ".join(map(str, value)) if isinstance(value, list) else str(value)
                if field == "calories":
                    rendered += " kcal"
            line += f"; {field}: {plain(rendered)}"
        lines.append(line)
    labels = [label for label in ("vegan", "vegetarian") if getattr(query.filters, label) is True]
    prefix = (" / ".join(labels) + " ") if labels else ""
    title = f"Published {prefix}{plain(meal.lower())} menu at {plain(venue)} for {day}:"
    parts: list[AnswerPart] = []
    chunk_lines: list[str] = []
    chunk_records: list[dict[str, Any]] = []
    for line, record in zip(lines, records, strict=True):
        if chunk_lines and (
            len(chunk_records) == 50 or len(title + "\n".join([*chunk_lines, line])) > 5900
        ):
            parts.append(fact(title + "\n" + "\n".join(chunk_lines), chunk_records))
            chunk_lines, chunk_records = [], []
        chunk_lines.append(line)
        chunk_records.append(record)
    parts.append(fact(title + "\n" + "\n".join(chunk_lines), chunk_records))
    if "allergens" in extra_fields:
        parts.append(
            limitation(
                "Menu labels do not establish allergy safety. Ask dining staff "
                "about ingredients and cross-contact."
            )
        )
    return parts


def hours_parts(
    text: str,
    records: list[dict[str, Any]],
    day: date,
    name_resolution: dict[str, Any] | None = None,
) -> list[AnswerPart]:
    text, name, named = entity_slot(text, records, "name", name_resolution)
    if not named:
        raise ValueError("The venue has not been independently resolved")
    has_close = bool(set(text.split()) & {"close", "closes", "closing", "end", "ends"})
    has_hours = bool(set(text.split()) & {"hours", "open", "opens", "opening"})
    if not has_close and not has_hours:
        raise ValueError("No requested hours field")
    for marker in (
        "hours",
        "close",
        "closes",
        "closing",
        "end",
        "ends",
        "time",
        "open",
        "opens",
        "opening",
        "regular",
        "scheduled",
    ):
        text, _ = remove_phrase(text, marker)
    if leftovers(text):
        raise ValueError("Unresolved schedule qualifiers")
    schedules = {r["fields"].get("schedule") for r in records}
    if len(schedules) != 1:
        raise ValueError("Conflicting schedules")
    for record in records:
        published(record, "name", "schedule", "day")
        if record["fields"].get("service_date") != str(day):
            raise ValueError("Wrong service date")
    schedule = next(iter(schedules))
    if not isinstance(schedule, str) or not schedule.strip():
        raise ValueError("Missing schedule")
    if "hours unavailable" in schedule.casefold():
        # The data's placeholder for hours it could not verify; GPT must say so, not code.
        raise ValueError("Hours are not published")
    text = f"Published hours for {plain(name)} on {day}: {plain(schedule)}."
    if has_close and not has_hours and not schedule.casefold().startswith("closed"):
        intervals = opening_intervals(records[0]["fields"].get("hours", schedule), day)
        endings = [end for _, end in intervals]
        last = max(endings, key=lambda value: value.timestamp())
        text = (
            f"{plain(name)} is scheduled to close at {last.strftime('%I:%M %p').lstrip('0')} "
            f"on {last.date()} (America/New_York)."
        )
    parts = [fact(text, records)]
    if all(not r.get("valid_from") and not r.get("valid_until") for r in records):
        parts.append(
            limitation(
                "This is the regular published schedule; special-date "
                "exceptions have not been confirmed."
            )
        )
    return parts


def departure_parts(
    text: str,
    records: list[dict[str, Any]],
    query: SearchQuery,
    output: dict[str, Any],
    now: datetime,
) -> list[AnswerPart]:
    try:
        text, route, named = entity_slot(text, records, "route")
    except ValueError:  # A timetable with several routes names none on its own.
        route, named = "", False
    # With no route in the question, a complete timetable fetched without a
    # route filter answers it for every route, each named in the answer.
    if not named and query.filters is not None and query.filters.route:
        raise ValueError("Unresolved route")
    said = set(text.split())
    # "What's the first shuttle today?" was answered with the next one (09-28).
    selections = {"first" if word == "earliest" else word
                  for word in said & {"first", "earliest", "last", "next"}}
    selection = next(iter(selections)) if len(selections) == 1 else None
    if selection is None or not said & {
        "shuttle",
        "bus",
        "departure",
        "leave",
        "leaves",
        "leaving",
    }:
        raise ValueError("No exact departure requested")
    summary = departure_summary(output, query, now)
    if summary["status"] != "ok":
        raise ValueError("Unverified departure calculation")
    campus = {item["route"]: item for item in summary["departures"] if item["origin"] == "campus"}
    # A published stop the question names ("to Garden State Plaza") selects the trips
    # that reach it. Campus is the boarding stop when the question names none. Other
    # origins remain in the generated path until their precise published boarding
    # labels (including pickup/drop-off restrictions) are resolved.
    stops = sorted({item["stop"] for group in campus.values() for item in group["destinations"]},
                   key=lambda stop: len(words(stop)), reverse=True)
    destination = None
    for stop in stops:
        text, matched = remove_phrase(text, stop)
        if matched:
            if destination is not None:
                raise ValueError("Two destinations")
            destination = stop
    for phrase in ("from campus", "leave campus", "leaves campus", "leaving campus"):
        text, _ = remove_phrase(text, phrase)
    for marker in (
        "first", "earliest", "next", "last", "shuttle", "bus", "departure", "leave", "leaves",
        "leaving", "time", *(("goes", "going", "that", "which", "reaches", "stops")
                             if destination else ()),
    ):
        text, _ = remove_phrase(text, marker)
    if leftovers(text):
        raise ValueError("Unresolved journey qualifiers")
    routes = [route] if named else sorted({r["fields"]["route"] for r in records})
    if not set(routes) <= set(campus):
        raise ValueError("A route's campus departures are withheld")
    groups = {name: campus[name] for name in routes}
    if destination is not None:
        groups = {name: item for name, group in groups.items() for item in group["destinations"]
                  if item["stop"] == destination}
        if not groups:
            raise ValueError("No route reaches the named stop")
    asked = str(query.date_from)

    def chosen_for(group: dict[str, Any]) -> dict[str, Any] | None:
        # The first and last shuttle are the asked day's, whatever the time now; the next
        # is the first one after now, even on the next day the lookup fetched.
        day = next((item for item in group.get("days", []) if item["service_date"] == asked),
                   None)
        chosen: dict[str, Any] | None = (
            group["next"] if selection == "next" else day[selection] if day else None)
        return chosen

    def moment(chosen: dict[str, Any]) -> float:
        return datetime.fromisoformat(chosen["departure_at"]).timestamp()

    found = sorted(
        ((name, chosen) for name, group in groups.items()
         if (chosen := chosen_for(group)) is not None),
        key=lambda item: moment(item[1]),
    )
    if destination is not None and found:
        # "The first shuttle to Garden State Plaza" is one trip, whichever route runs it.
        found = [found[-1] if selection == "last" else found[0]]

    def clock(value: str) -> str:
        return datetime.fromisoformat(value).strftime("%I:%M %p").lstrip("0")

    def left(chosen: dict[str, Any]) -> bool:
        return moment(chosen) <= now.timestamp()

    def trip(name: str, chosen: dict[str, Any]) -> str:
        arrival = (f", arriving at {clock(chosen['arrives_at'])}"
                   if destination is not None else "")
        gone = " (already left)" if selection == "last" and left(chosen) else ""
        return f"{plain(name)} at {clock(chosen['departure_at'])}{arrival}{gone}"

    def cited(*trips: dict[str, Any]) -> list[dict[str, Any]]:
        ids = {chosen["evidence_id"] for chosen in trips}
        return [record for record in records if record["id"] in ids]

    reaching = f" that reaches {plain(destination)}" if destination is not None else ""
    parts: list[AnswerPart] = []
    selected = cited(*(chosen for _, chosen in found))
    dates = {datetime.fromisoformat(chosen["departure_at"]).date() for _, chosen in found}
    if len(found) == 1 and destination is None:
        name, chosen = found[0]
        departure = datetime.fromisoformat(chosen["departure_at"])
        parts.append(fact(
            f"The {selection} published departure from campus on {plain(name)} on "
            f"{departure.date()} was {clock(chosen['departure_at'])} (America/New_York); it "
            "has already left."
            if selection == "last" and left(chosen) else
            f"The {selection} published departure from campus on {plain(name)} is "
            f"{clock(chosen['departure_at'])} on {departure.date()} (America/New_York).",
            selected,
        ))
    elif len(found) == 1:
        name, chosen = found[0]
        departure = datetime.fromisoformat(chosen["departure_at"])
        parts.append(fact(
            f"The {selection} published departure from campus{reaching} on {departure.date()} "
            f"(America/New_York) was {trip(name, chosen).removesuffix(' (already left)')}; "
            "it has already left."
            if selection == "last" and left(chosen) else
            f"The {selection} published departure from campus{reaching} on {departure.date()} "
            f"(America/New_York) is {trip(name, chosen)}.",
            selected,
        ))
    elif found and len(dates) == 1:
        parts.append(fact(
            f"The {selection} published departures from campus{reaching} on "
            f"{next(iter(dates))} (America/New_York) are: "
            + "; ".join(trip(*item) for item in found)
            + ".",
            selected,
        ))
    elif found:
        # The next shuttle on one route may be tomorrow's first while another runs tonight.
        parts.append(fact(
            f"The {selection} published departures from campus{reaching} (America/New_York) "
            "are: " + "; ".join(
                f"{trip(name, chosen)} on {datetime.fromisoformat(chosen['departure_at']).date()}"
                for name, chosen in found) + ".",
            selected,
        ))
    span = (
        f"on {summary['date_from']}"
        if summary["date_from"] == summary["date_to"]
        else f"within {summary['date_from']} to {summary['date_to']}"
    )
    if selection == "last":
        # A day's last shuttle that has already left is answered with the next one: asked
        # at 11:10 PM (09-28), "when is the last shuttle?" said only that no later
        # departure was found, route by route.
        for name, chosen in found:
            if not left(chosen):
                continue
            later = groups[name]["next"]
            route_named = f" on {plain(name)}" if destination is None else ""
            if later is None:
                parts.append(limitation(
                    f"I couldn't find a later scheduled departure from campus{reaching}"
                    f"{route_named} {span}. This does not establish that service has ended "
                    "after that."
                ))
                continue
            departure = datetime.fromisoformat(later["departure_at"])
            arrival = (f", arriving at {clock(later['arrives_at'])}"
                       if destination is not None else "")
            parts.append(fact(
                f"The next published departure from campus{reaching}{route_named} is "
                f"{clock(later['departure_at'])} on {departure.date()} (America/New_York)"
                f"{arrival}.",
                cited(later),
            ))
    missing = ([name for name, group in groups.items() if chosen_for(group) is None]
               if destination is None else [] if found else [""])
    for name in missing:
        route_named = f" on {plain(name)}" if name else ""
        parts.append(limitation(
            f"I couldn't find a later scheduled departure from campus{reaching}{route_named} "
            f"{span}. This does not establish that service has ended after that."
        ))
    if found:
        parts.append(limitation(
            "This is a published timetable, not live vehicle status. "
            "Delays and holiday operations are not verified."
        ))
    return parts


def exact_search(
    quote: str | None,
    messages: list[ChatMessage],
    query: SearchQuery,
    output: dict[str, Any],
    now: datetime,
) -> ExactPiece | None:
    if not quote or not independent_quote(quote, messages[-1].content):
        return None
    try:
        name_resolution = None
        if query.query.strip():
            name_resolution = output.get("coverage", {}).get("name_resolution")
            if (
                not isinstance(name_resolution, dict)
                or name_resolution.get("query") != query.query
                or name_resolution.get("basis") != "unique_published_name_prefix"
            ):
                return None
            # The lookup proves uniqueness; independently verify prefix semantics.
            prefix = words(query.query)
            canonical = words(name_resolution["canonical_name"])
            if canonical != prefix and not canonical.startswith(prefix + " "):
                return None
        day, text = request_date(words(quote), now)
        # A departures lookup also fetches the next day, for the shuttle after the day's
        # last one; the answer is still about the day asked.
        through = {day, day + timedelta(days=1)} if query.collection == "shuttle" else {day}
        if query.date_from != day or (query.date_to and query.date_to not in through):
            return None
        records = current_records(output, day)
        if any(r.get("collection") != query.collection for r in records):
            return None
        complete = output.get("truncated") is False and output.get("total_matches") == len(records)
        if query.collection == "menu":
            parts = menu_parts(text, records, query, day, name_resolution)
        elif query.collection in {"campus_hours", "dining_hours"}:
            if not complete:
                return None
            if "now" in words(quote).split() and "hours" not in words(quote).split():
                return None  # An open-now conclusion also needs preceding overnight service.
            parts = hours_parts(text, records, day, name_resolution)
        elif query.collection == "shuttle":
            parts = departure_parts(text, records, query, output, now)
        else:
            return None
        if not complete:
            parts.append(limitation("Only part of the matching published list was retrieved."))
        has_facts = any(p.kind == "campus_fact" for p in parts)
        return ExactPiece(
            quote,
            Answer(
                status="answered"
                if has_facts and complete
                else "partial"
                if has_facts
                else "unavailable",
                parts=parts,
            ),
            complete,
        )
    except (KeyError, TypeError, ValueError, StopIteration):
        return None


# Words a plain "what events are on tomorrow" may use besides its day. A moment in the
# day ("tonight", "now") or a kind of event ("club", "free food") needs GPT.
EVENT_WORDS = {"event", "events", "happening", "going", "campus", "any", "there",
               "activities", "activity", "things", "stuff", "ramapo", "scheduled", "planned"}
MOMENT_WORDS = {"tonight", "now", "later", "evening", "morning", "afternoon", "night",
                "left", "still", "upcoming", "next", "soon"}


def plain_event_list(text: str, now: datetime) -> bool:
    """Whether a request asks only for every event on one day."""
    said = set(words(text).split())
    if said & MOMENT_WORDS or not said & {"event", "events", "happening"}:
        return False
    try:
        _, rest = request_date(words(text), now)
    except ValueError:
        return False
    return not set(rest.split()) - GRAMMAR - EVENT_WORDS


def _event_end(record: dict[str, Any], day: date) -> datetime | None:
    """When an event ends that day, from its published end time, or None."""
    starts = datetime.fromisoformat(record["fields"]["starts_at"])
    label = record["fields"].get("end_time")
    if not isinstance(label, str) or not label.strip():
        return None
    for shape in ("%I:%M %p", "%I %p"):
        try:
            clock = datetime.strptime(label.strip().upper(), shape).time()
        except ValueError:
            continue
        return datetime.combine(day, clock, tzinfo=starts.tzinfo)
    raise ValueError("Unreadable end time")


def events_answer(output: dict[str, Any], query: SearchQuery, now: datetime) -> Answer | None:
    """Every published event on one day, soonest first, from a whole-day events search.

    Code states each event's title, published times and, when one is published, its
    location, and nothing else about it. Today, events already over are counted, not
    listed. Anything it can't prove (a cut-off list, an unreadable time) goes to GPT.
    """
    try:
        day = query.date_from
        if (query.collection != "events" or day is None or query.date_to not in {None, day}
                or query.query.strip() or query.filters is not None
                or output.get("truncated") is not False):
            return None
        records = current_records(output, day)
        if output.get("total_matches") != len(records) or len(records) > 50:
            return None
        listed: list[tuple[datetime, str, dict[str, Any]]] = []
        over = 0
        for record in records:
            fields = record["fields"]
            published(record, "title", "starts_at", "start_time")
            if (record.get("collection") != "events"
                    or fields.get("occurrence_date") != day.isoformat()):
                return None
            starts = datetime.fromisoformat(fields["starts_at"])
            ends = _event_end(record, day)
            if day == now.date() and (ends or starts) <= now:
                if ends is None:
                    return None  # Started earlier with no end: GPT says whether it's over.
                over += 1
                continue
            time = plain(fields["start_time"])
            if ends is not None:
                published(record, "end_time")
                time += " to " + plain(fields["end_time"])
            line = f"- {time}: {plain(fields['title'])}"
            location = fields.get("location")
            if (isinstance(location, str) and location.strip()
                    and record["coverage"]["fields"].get("location") == "published"):
                line += f", {plain(location)}"
            listed.append((starts, line, record))
        listed.sort(key=lambda item: (item[0], item[1]))
        heading = f"Events on {day:%A, %B} {day.day}"
        if not listed:
            if not over:
                return None  # An empty day is for GPT to say what the listing covers.
            return Answer(status="answered", parts=[fact(
                f"{heading}: all {over} published events have already ended.",
                records)])
        per_part = -(-len(listed) // 11)
        parts = []
        for start in range(0, len(listed), per_part):
            chunk = listed[start:start + per_part]
            text = "\n".join(line for _, line, _ in chunk)
            parts.append(fact(f"{heading}:\n{text}" if not start else text,
                              [record for _, _, record in chunk]))
        if over:
            parts.append(AnswerPart(kind="guidance", evidence_ids=[], text=(
                f"{over} earlier {'event has' if over == 1 else 'events have'} already "
                "ended today.")))
        return Answer(status="answered", parts=parts)
    except (KeyError, TypeError, ValueError):
        return None


def exact_contact(
    quote: str | None,
    messages: list[ChatMessage],
    query: ContactQuery,
    output: dict[str, Any],
    now: datetime,
) -> ExactPiece | None:
    if not quote or not independent_quote(quote, messages[-1].content):
        return None
    result = contact_answer([ChatMessage(role="user", content=quote)], query, output, now.date())
    return ExactPiece(quote, result) if result is not None else None


def combine_exact(
    messages: list[ChatMessage],
    pieces: list[ExactPiece],
    *,
    fallback: bool,
    allow_remaining: bool = False,
) -> Answer | None:
    if not pieces:
        return None
    remaining = messages[-1].content
    parts: list[AnswerPart] = []
    seen: dict[str, Answer] = {}
    for piece in pieces:
        if piece.quote in seen:
            if seen[piece.quote] != piece.answer:
                return None
            continue
        seen[piece.quote] = piece.answer
        if piece.quote not in remaining:
            return None  # Overlapping request quotes cannot silently drop a task.
        remaining = remaining.replace(piece.quote, " ", 1)
        parts.extend(piece.answer.parts)
    if not fallback and (
        (leftovers(remaining) and not allow_remaining) or not all(p.complete for p in pieces)
    ):
        return None
    if fallback:
        if not any(part.kind == "campus_fact" for part in parts):
            return None
        # All pieces are code-rendered. Keep their scope/allergy/partial-list
        # limitations alongside their facts; those qualifications are essential.
        parts.append(
            limitation(
                "These are the details I could verify. I couldn't reliably "
                "complete the rest of your request."
            )
        )
    statuses = {piece.answer.status for piece in pieces}
    has_facts = any(p.kind == "campus_fact" for p in parts)
    status = (
        "partial"
        if fallback or (has_facts and statuses != {"answered"})
        else "answered"
        if has_facts
        else "clarification"
        if "clarification" in statuses
        else "unavailable"
    )
    try:
        return Answer.model_validate({"status": status, "parts": parts})
    except ValueError:
        return None
