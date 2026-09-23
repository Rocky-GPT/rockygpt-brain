"""Supplement a menu occurrence only from its unique, exact dated source item."""
from __future__ import annotations

import re
from typing import Any

from rockygpt_brain.retrieval.helpers import _date

NUTRIENT_FIELDS = frozenset({
    "calories", "caloriesFromFat", "fat", "saturatedFat", "transFat", "polyunsaturatedFat",
    "cholesterol", "sodium", "carbohydrates", "dietaryFiber", "sugar", "protein", "potassium",
    "iron", "calcium", "vitaminA", "vitaminC", "vitaminD", "addedSugar",
})
MENU_NUTRIENT_LIMITATION = ("Nutrient values retain source units exactly. An empty value is "
                            "unknown; no unit may be inferred when the source omits it.")
MENU_ARTIFACT_FIELDS = ("description", "ingredients", "nutrients", "isMindful", "isPlantBased")
Occurrence = tuple[str, str, str, str]
MenuIndex = dict[Occurrence, tuple[list[str], dict[str, Any]]]


def _published_name(value: Any) -> str:
    # Same narrow cleanup used for the SQL name/source key at publication.
    if not isinstance(value, str):
        return ""
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    for entity, replacement in (("nbsp", " "), ("amp", "&"), ("lt", "<"), ("gt", ">")):
        value = re.sub(f"&{entity};", replacement, value, flags=re.I)
    return " ".join(value.split())


def menu_occurrence(record: dict[str, Any]) -> Occurrence | None:
    fields = record["fields"]
    day, until = _date(record.get("valid_from")), _date(record.get("valid_until"))
    if day is None or until != day:
        return None
    values = [fields.get(key) for key in ("meal", "station", "name")]
    if not all(isinstance(value, str) and value for value in values):
        return None
    occurrence = (day.isoformat(), *values)
    key = record.get("source_record_key")
    prefix = f"{record.get('source_key')}:"
    if key is None and str(record.get("entity_id", "")).startswith(prefix):
        key = record["entity_id"][len(prefix):]
    # The key alone can be ambiguous when text contains colons. Verify the
    # separately stored date/meal/station/name tuple as well.
    return occurrence if key == ":".join(occurrence) else None


def menu_artifact_index(payload: Any) -> MenuIndex:
    candidates: dict[Occurrence, list[tuple[list[str], dict[str, Any]]]] = {}
    if not isinstance(payload, dict) or not isinstance(payload.get("dates"), list):
        return {}
    for day_index, day in enumerate(payload["dates"]):
        if not isinstance(day, dict) or not isinstance(day.get("date"), str):
            continue
        for meal_index, meal in enumerate(day.get("sections", [])):
            if not isinstance(meal, dict) or not isinstance(meal.get("name"), str):
                continue
            for station_index, station in enumerate(meal.get("groups", [])):
                if not isinstance(station, dict) or not isinstance(station.get("name"), str):
                    continue
                for item_index, item in enumerate(station.get("items", [])):
                    if not isinstance(item, dict) or not _published_name(item.get("formalName")):
                        continue
                    key = (day["date"], meal["name"], station["name"],
                           _published_name(item["formalName"]))
                    path = ["dates", str(day_index), "sections", str(meal_index), "groups",
                            str(station_index), "items", str(item_index)]
                    candidates.setdefault(key, []).append((path, item))
    return {key: items[0] for key, items in candidates.items() if len(items) == 1}


def supplement_menu(record: dict[str, Any], index: MenuIndex) -> dict[str, Any]:
    occurrence = menu_occurrence(record)
    matched = index.get(occurrence) if occurrence else None
    if matched is None:
        return {}
    path, item = matched
    record["artifact_key"], record["artifact_path"] = "menu-week", path
    return {key: item[key] for key in MENU_ARTIFACT_FIELDS if key in item}
