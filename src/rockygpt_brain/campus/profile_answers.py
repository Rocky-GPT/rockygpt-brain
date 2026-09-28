"""Menu and hours answers code writes from a profile lookup, with no GPT writer or checker.

Jev decides the request is plain: what a meal serves, or when a place is open on a day,
and nothing more. Code then checks that the lookup proves the answer (the right place,
day, meal and diet, every dish that matched, and fresh official records that publish
each field the answer states) and states only what the records hold. Anything it can't
prove returns None, and GPT writes and is reviewed as before.
"""

import re
from datetime import date
from typing import Any, Literal

from rockygpt_brain.contracts import Answer, AnswerPart
from rockygpt_brain.retrieval.exact import plain
from rockygpt_brain.retrieval.menu_artifacts import MENU_NUTRIENT_LIMITATION
from rockygpt_brain.retrieval.profiles import (
    SCHEDULE_LIMITATION,
    UNLABELED_MEAL_LIMITATION,
    ProfileQuery,
)

Template = Literal["menu", "full_menu", "hours"]
TRUSTED = {"official_primary", "official_secondary"}
CURRENT = {"fresh", "static"}
DIET_LIMITATION = "Dietary labels are published menu data, not an allergy safety guarantee."
# Caveats every such record carries that don't touch what these answers state. Any other
# caveat (stale, disagreeing, partly unavailable hours) sends the answer to GPT.
MENU_CAVEATS = {DIET_LIMITATION, MENU_NUTRIENT_LIMITATION}
HOURS_CAVEATS = {SCHEDULE_LIMITATION}
# An answer part may cite at most 50 records and hold 6,000 characters.
PART_RECORDS = 50
PART_CHARACTERS = 5800


def _normalized(value: Any) -> str:
    return " ".join(str(value).split()).casefold()


def _day(value: date) -> str:
    return f"{value:%A, %B} {value.day}"


def _clock(value: str) -> str:
    """A published clock as the student reads it: '01:00 PM' is 1:00 PM."""
    return re.sub(r"^0(?=\d)", "", " ".join(value.split()))


def _entity(output: dict[str, Any], query: ProfileQuery) -> dict[str, Any] | None:
    resolution = output.get("resolution") or {}
    entity = resolution.get("entity")
    if (
        output.get("status") != "ok"
        or resolution.get("status") != "matched"
        or not isinstance(entity, dict)
        or not isinstance(entity.get("name"), str)
        or not entity["name"].strip()
        or (query.entity_id is not None and entity.get("id") != str(query.entity_id))
    ):
        return None
    return entity


def _applies(record: dict[str, Any], day: date) -> bool:
    try:
        start = date.fromisoformat(record["valid_from"]) if record.get("valid_from") else None
        end = date.fromisoformat(record["valid_until"]) if record.get("valid_until") else None
    except (TypeError, ValueError):
        return False
    return (start is None or start <= day) and (end is None or day <= end)


def _proven(record: dict[str, Any], collections: set[str], entity_id: str, day: date,
            caveats: set[str], fields: tuple[str, ...]) -> bool:
    """A fresh official record of this entity for this day, publishing these fields."""
    coverage = record.get("coverage", {}).get("fields", {})
    return (
        record.get("collection") in collections
        and record.get("trust_tier") in TRUSTED
        and record.get("freshness") in CURRENT
        and not record.get("content_truncated")
        and set(record.get("limitations", [])) <= caveats
        and entity_id in {record.get("related_to_entity_id"), record.get("canonical_entity_id")}
        and _applies(record, day)
        and all(coverage.get(field) == "published" for field in fields)
        and all(isinstance(record["fields"].get(field), str)
                and record["fields"][field].strip() for field in fields)
    )


def _section(output: dict[str, Any], name: str, query: ProfileQuery,
             day: date) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
    """A section that returned every record it matched, whole, and those records."""
    component = (output.get("components") or {}).get(name)
    if (
        not isinstance(component, dict)
        or component.get("status") != "available"
        or component.get("truncated")
        or component.get("failed_links")
        or component.get("linked_records_missing")
        or component.get("conflicts")
        or component.get("total_matches") != component.get("returned_count")
        or component.get("service_date") != day.isoformat()
        or _normalized(component.get("meal")) != _normalized(query.meal)
    ):
        return None
    ids = component.get("evidence_ids") or []
    records = {record["id"]: record for record in output.get("records", [])
               if record.get("id") in ids}
    if not ids or len(records) != len(ids):
        return None
    return component, [records[record_id] for record_id in ids]


def _meal_period(output: dict[str, Any], query: ProfileQuery, entity_id: str,
                 day: date) -> tuple[str, dict[str, Any]] | None:
    """The requested meal's one published period that day, and its record."""
    section = _section(output, "hours", query, day)
    if section is None or query.meal is None:
        return None
    _, records = section
    periods = set()
    for record in records:
        fields = record["fields"]
        if (
            not _proven(record, {"dining_hours"}, entity_id, day, HOURS_CAVEATS, ("name",))
            or fields.get("service_date") != day.isoformat()
            or fields.get("meal_coverage") != "published"
        ):
            return None
        for period in fields.get("requested_meal_periods") or []:
            if not all(isinstance(period.get(key), str) and period[key].strip()
                       for key in ("start", "end")):
                return None
            periods.add((_clock(period["start"]), _clock(period["end"])))
    if len(periods) != 1 or len(records) != 1:
        return None
    start, end = periods.pop()
    return f"{start} to {end}", records[0]


def _fact(text: str, records: list[dict[str, Any]]) -> AnswerPart:
    return AnswerPart(kind="campus_fact", text=text,
                      evidence_ids=list(dict.fromkeys(record["id"] for record in records)))


def menu_answer(output: dict[str, Any], query: ProfileQuery, *, full: bool,
                dishes: set[str] | None = None) -> Answer | None:
    """A meal's dishes by station, from a lookup that returned every dish it matched.

    `dishes`, when given, are the record IDs Jev says are dishes a student would choose:
    only those are listed, with how many items the meal has in all. Without them, or
    when they are every item or none, every item is listed.
    """
    if query.date is None or query.meal is None or "menu" not in query.include:
        return None
    day = query.date
    entity = _entity(output, query)
    section = _section(output, "menu", query, day)
    if entity is None or section is None:
        return None
    component, records = section
    if component.get("complete") is not True or component.get("diet") != query.diet:
        return None
    for record in records:
        fields = record["fields"]
        if (
            not _proven(record, {"menu"}, entity["id"], day, MENU_CAVEATS, ("name", "meal"))
            or record.get("valid_from") != day.isoformat()
            or _normalized(fields["meal"]) != _normalized(query.meal)
            or (query.diet is not None and (
                fields.get(query.diet) is not True
                or record["coverage"]["fields"].get(query.diet) != "published"))
        ):
            return None
    shown = records
    if not full and dishes is not None and 0 < len(dishes & {r["id"] for r in records}) \
            < len(records):
        shown = [record for record in records if record["id"] in dishes]
    stations: dict[str, list[dict[str, Any]]] = {}
    for record in shown:
        station = record["fields"].get("station")
        label = station.strip() if isinstance(station, str) and station.strip() else ""
        stations.setdefault(label, []).append(record)
    meal = query.meal.strip().lower()
    diet = f"{query.diet} " if query.diet else ""
    period = _meal_period(output, query, entity["id"], day)
    title = (f"{query.meal.strip().capitalize()} at {plain(entity['name'])} on {_day(day)}"
             + (f", {period[0]}" if period else "") + ":")
    if query.diet:
        title = (f"Published {diet}{meal} dishes at {plain(entity['name'])} on {_day(day)}"
                 + (f" ({meal} is {period[0]})" if period else "") + ":")
    lines: list[tuple[str, list[dict[str, Any]]]] = [(title, [period[1]] if period else [])]
    for label, items in stations.items():
        if len(items) > PART_RECORDS:
            return None
        names = ", ".join(plain(record["fields"]["name"]) for record in items)
        lines.append((f"- **{plain(label)}:** {names}" if label else f"- {names}", items))
    if len(shown) < len(records):
        closing = (f"That's {len(shown)} of the {len(records)} {diet}{meal} items. Ask for "
                   f"the full {diet}{meal} menu to see them all.")
    else:
        closing = f"That's all {len(records)} {diet}{meal} items on the published menu."
    parts: list[AnswerPart] = []
    text, cited = "", list[dict[str, Any]]()
    for line, items in lines:
        if text and (len(cited) + len(items) > PART_RECORDS
                     or len(text) + len(line) > PART_CHARACTERS):
            parts.append(_fact(text, cited))
            text, cited = "", []
        text = f"{text}\n{line}" if text else line
        cited = [*cited, *items]
    parts.append(_fact(text, cited))
    parts.append(AnswerPart(kind="guidance", text=closing, evidence_ids=[]))
    if query.diet:
        parts.append(AnswerPart(
            kind="limitation", evidence_ids=[],
            text=f"These are the dishes the menu labels {query.diet}; a dish with no label is "
            "left out. Labels aren't an allergy guarantee, so ask dining staff if you need "
            "to be sure."))
    if len(parts) > 12:
        return None
    return Answer(status="answered", parts=parts)


def hours_answer(output: dict[str, Any], query: ProfileQuery) -> Answer | None:
    """A place's published hours on one day, or one meal's, from one schedule."""
    if query.date is None or "hours" not in query.include:
        return None
    day = query.date
    entity = _entity(output, query)
    section = _section(output, "hours", query, day)
    if entity is None or section is None:
        return None
    _, records = section
    name = plain(entity["name"])
    if query.meal is not None:
        period = _meal_period(output, query, entity["id"], day)
        if period is None:
            return None
        return Answer(status="answered", parts=[_fact(
            f"{query.meal.strip().capitalize()} at {name} on {_day(day)} is {period[0]}.",
            [period[1]])])
    schedules = set()
    for record in records:
        fields = record["fields"]
        if (
            not _proven(record, {"campus_hours", "dining_hours"}, entity["id"], day,
                        HOURS_CAVEATS | {UNLABELED_MEAL_LIMITATION}, ("name", "schedule"))
            or fields.get("service_date") != day.isoformat()
        ):
            return None
        schedules.add((record["collection"], " ".join(fields["schedule"].split())))
    if len(schedules) != 1:
        return None  # Two schedules, such as a building's and its help desk's: GPT says which.
    _, schedule = schedules.pop()
    if "unavailable" in schedule.casefold():
        return None  # The data's placeholder for hours it couldn't verify.
    periods = [period for record in records[:1] for period in record["fields"].get("periods")
               or [] if record["coverage"]["fields"].get("periods") == "published"]
    if schedule.casefold() == "closed":
        text = f"{name} is closed on {_day(day)}, according to its published hours."
    elif periods and all(isinstance(period.get(key), str) and period[key].strip()
                         for period in periods for key in ("label", "start", "end")):
        text = f"{name} on {_day(day)}: " + "; ".join(
            f"{plain(period['label'])} {_clock(period['start'])} to {_clock(period['end'])}"
            for period in periods) + "."
    else:
        text = f"{name}'s published hours on {_day(day)}: {plain(schedule)}."
    parts = [_fact(text, records)]
    if all(not record.get("valid_from") and not record.get("valid_until") for record in records):
        parts.append(AnswerPart(
            kind="limitation", evidence_ids=[],
            text="This is the regular published schedule; special-date exceptions have not "
            "been confirmed."))
    return Answer(status="answered", parts=parts)


def profile_answer(template: Template, output: dict[str, Any], query: ProfileQuery,
                   dishes: set[str] | None = None) -> Answer | None:
    if template == "hours":
        return hours_answer(output, query)
    return menu_answer(output, query, full=template == "full_menu", dishes=dishes)
