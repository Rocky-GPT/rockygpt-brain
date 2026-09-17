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
    for offset, marker in ((0, "today"), (0, "tonight"), (0, "now"), (1, "tomorrow")):
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
        identity = record["entity_id"]
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


def menu_parts(
    text: str,
    records: list[dict[str, Any]],
    query: SearchQuery,
    day: date,
    name_resolution: dict[str, Any] | None = None,
) -> list[AnswerPart]:
    if not query.filters or not query.filters.meal or query.filters.name:
        raise ValueError("A whole meal list requires a meal filter")
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
    text = f"Published hours for {plain(name)} on {day}: {plain(schedule)}."
    if has_close and not has_hours and not schedule.casefold().startswith("closed"):
        endings = [end for _, end in opening_intervals(schedule, day)]
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
    text, route, named = entity_slot(text, records, "route")
    if not named:
        raise ValueError("Unresolved route")
    selections = set(text.split()) & {"last", "next"}
    selection = next(iter(selections)) if len(selections) == 1 else None
    if selection is None or not set(text.split()) & {
        "shuttle",
        "bus",
        "departure",
        "leave",
        "leaves",
    }:
        raise ValueError("No exact departure requested")
    # Other origins remain in the generated path until their precise published
    # boarding labels (including pickup/drop-off restrictions) are resolved.
    text, campus_origin = remove_phrase(text, "from campus")
    if not campus_origin:
        raise ValueError("Unresolved boarding stop")
    for marker in ("next", "last", "shuttle", "bus", "departure", "leave", "leaves", "time"):
        text, _ = remove_phrase(text, marker)
    if leftovers(text):
        raise ValueError("Unresolved journey qualifiers")
    summary = departure_summary(output, query, now)
    if summary["status"] != "ok":
        raise ValueError("Unverified departure calculation")
    option = next(
        item
        for item in summary["departures"]
        if item["route"] == route and item["origin"] == "campus"
    )[selection]
    if option is None:
        return [
            limitation(
                f"I couldn't find a later scheduled departure from campus on "
                f"{plain(route)} within {summary['date_from']} to {summary['date_to']}. "
                "This does not establish that service has ended beyond those dates."
            )
        ]
    selected = [r for r in records if r["id"] == option["evidence_id"]]
    departure = datetime.fromisoformat(option["departure_at"])
    return [
        fact(
            f"The {selection} published departure from campus on {plain(route)} is "
            f"{departure.strftime('%I:%M %p').lstrip('0')} on {departure.date()} "
            "(America/New_York).",
            selected,
        ),
        limitation(
            "This is a published timetable, not live vehicle status. "
            "Delays and holiday operations are not verified."
        ),
    ]


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
        if query.date_from != day or (query.date_to and query.date_to != day):
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
