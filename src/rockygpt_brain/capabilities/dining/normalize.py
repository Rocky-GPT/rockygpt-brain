from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from rockygpt_brain.services.data import DataPort


def _text(record: dict[str, Any], name: str) -> str:
    value = record.get(name)
    return value if isinstance(value, str) else ""


def _contains(actual: str, wanted: str) -> bool:
    return wanted.casefold() in actual.casefold()


def _calories(record: dict[str, Any]) -> int:
    match = re.search(r"\d+", _text(record, "calories"))
    return int(match.group()) if match else 0


FIELDS = {
    "date": lambda r: _text(r, "date"),
    "name": lambda r: _text(r, "name"),
    "meal": lambda r: _text(r, "meal"),
    "station": lambda r: _text(r, "station"),
    "calories": lambda r: _text(r, "calories"),
    "vegan": lambda r: r.get("vegan") is True,
    "vegetarian": lambda r: r.get("vegetarian") is True,
    "allergens": lambda r: r.get("allergens") if isinstance(r.get("allergens"), list) else [],
}

SORT = {
    "date": lambda r: _text(r, "date"),
    "name": lambda r: _text(r, "name").casefold(),
    "meal": lambda r: _text(r, "meal").casefold(),
    "station": lambda r: _text(r, "station").casefold(),
    "calories": _calories,
    "vegan": lambda r: r.get("vegan") is True,
    "vegetarian": lambda r: r.get("vegetarian") is True,
}


# The sitting each meal is served as on a day that does not serve it under its
# own name. Brunch is one weekend service covering both morning sittings, and
# the records call it that: a Saturday holds brunch, dinner and late night, and
# no breakfast at all, so a plan narrowing on breakfast matched nothing on the
# day the question is most likely asked about.
#
# A substitution, deliberately, rather than a widened match. Breakfast is
# answered with brunch only where the day serves no breakfast, and the swap is
# visible in the trace as `meal` on the plan becoming `mealServed` on the
# normalized plan. Treating the two as interchangeable in the query would hide
# a real distinction on every day that draws it, and hide it silently.
_STANDS_IN = {
    "breakfast": "brunch",
    "lunch": "brunch",
}


def meals(filters: dict[str, str]) -> list[str]:
    """The service names a record must carry to satisfy the lookup.

    Reads the resolved `mealServed`, not the asked-for `meal`: by the time a
    lookup runs, which services answer the question has been decided and
    written down. Empty where the question named no meal, which is every meal
    rather than none.
    """
    wanted = filters.get("mealServed")
    if not wanted:
        return []
    served: list[str] = []
    for mention in wanted.split(","):
        name = mention.strip().casefold()
        if name and name not in served:
            served.append(name)
    return served


async def _served_on(date: str, now: datetime, data: DataPort) -> set[str]:
    """Which services the day actually holds, as the records spell them."""
    records = await data.dining({"q": "", "at": now.isoformat()})
    return {
        _text(record, "meal").casefold()
        for record in records
        if not date or _text(record, "date") == date
    }


async def resolve_filters(filters: dict[str, str], now: datetime, data: DataPort) -> dict[str, str]:
    """Turn the meals a question asked about into the services that answer it.

    Nothing is substituted while the day serves what was asked for, so a
    weekday breakfast stays breakfast and asking for brunch by name asks only
    for brunch. The day is consulted rather than assumed, because "there is no
    breakfast service today" is a fact about the records and not about the
    word.
    """
    resolved = dict(filters)
    asked = resolved.pop("meal", "").strip()
    if not asked:
        return resolved
    served = await _served_on(resolved.get("date", ""), now, data)
    names: list[str] = []
    for mention in asked.split(","):
        meal = mention.strip()
        if not meal:
            continue
        name = meal.replace("_", " ")
        if served and name.casefold() not in served:
            stands_in = _STANDS_IN.get(meal)
            if stands_in and stands_in in served:
                name = stands_in
        if name not in names:
            names.append(name)
    resolved["mealServed"] = ",".join(names)
    return resolved


def query(filters: dict[str, str], now: datetime) -> dict[str, Any]:
    terms = [filters[name] for name in ("name", "station", "dietary") if name in filters]
    out: dict[str, Any] = {"q": " ".join(terms), "at": now.isoformat()}
    if served := meals(filters):
        out["meals"] = served
    return out


def matches(record: dict[str, Any], filters: dict[str, str]) -> bool:
    if date := filters.get("date"):
        record_date = _text(record, "date")
        if record_date and record_date != date:
            return False
    if served := meals(filters):
        if _text(record, "meal").casefold() not in served:
            return False
    if name := filters.get("name"):
        if not _contains(_text(record, "name"), name):
            return False
    if station := filters.get("station"):
        if not _contains(_text(record, "station"), station):
            return False
    if dietary := filters.get("dietary"):
        choice = dietary.strip().casefold()
        if choice == "vegan":
            return record.get("vegan") is True
        if choice == "vegetarian":
            return record.get("vegetarian") is True
        return False
    return True
