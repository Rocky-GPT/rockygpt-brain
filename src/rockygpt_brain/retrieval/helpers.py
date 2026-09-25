"""Data transformation and parsing helpers for campus retrieval."""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime
from typing import Any

from rockygpt_brain.retrieval.models import CAMPUS_ZONE


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def _tokens(text: str) -> set[str]:
    # General Unicode tokenization; letter/digit runs let CMPS147 match CMPS 147.
    pieces: list[str] = []
    current = ""
    previous_numeric = False
    for char in text.casefold():
        if not char.isalnum():
            if current:
                pieces.append(current)
                current = ""
        else:
            numeric = char.isnumeric()
            if current and numeric != previous_numeric:
                pieces.append(current)
                current = ""
            current += char
            previous_numeric = numeric
    if current:
        pieces.append(current)
    return set(pieces)


def _values(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(_values(v) for v in value.values())
    if isinstance(value, list):
        return " ".join(_values(v) for v in value)
    return "" if value is None or isinstance(value, bool) else str(value)


def _date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.astimezone(CAMPUS_ZONE).date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.astimezone(CAMPUS_ZONE).date() if parsed.tzinfo else parsed.date()
        except ValueError:
            return None
    return None


def _instant(value: Any) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if isinstance(value, datetime) and value.tzinfo:
        return value.astimezone(UTC)
    return None


def _bounded(value: Any, budget: int) -> Any:
    """Bound nested artifact fields while retaining valid JSON and truncation markers."""
    if len(_json(value)) <= budget:
        return value
    if isinstance(value, str):
        return value[: max(0, budget - 30)] + " [truncated; read for details]"
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        remaining = budget
        for key, child in value.items():
            if remaining < 100:
                result["_truncated"] = True
                break
            result[key] = _bounded(child, min(remaining, max(400, budget // 3)))
            remaining = budget - len(_json(result))
        return result
    if isinstance(value, list):
        output: list[Any] = []
        for child in value:
            remaining = budget - len(_json(output))
            if remaining < 100:
                output.append({"_truncated": True})
                break
            output.append(_bounded(child, remaining))
        return output
    return value


def _meal_key(value: Any) -> str:
    """A meal or venue label as published hours and menus both spell it."""
    return " ".join(str(value).split()).casefold()


def _meal_position(orders: dict[tuple[str, str], list[str]], record: dict[str, Any]) -> int:
    """A menu record's place in its venue and date's meal order (0 where it has none)."""
    key = (str(record["fields"].get("venue", "")), str(record.get("valid_from") or ""))
    order = [_meal_key(meal) for meal in orders.get(key, [])]
    meal = _meal_key(record["fields"].get("meal", ""))
    return order.index(meal) if meal in order else len(order)


def _dining_periods(value: Any) -> dict[tuple[Any, ...], list[dict[str, str]]]:
    """Index only fully matched published venue/day/window/interval descriptions."""
    indexed: dict[tuple[Any, ...], list[dict[str, str]]] = {}

    def clock(parts: dict[str, Any]) -> str:
        hour, minute, period = (str(parts.get(k, "")) for k in ("hour", "minute", "period"))
        if not hour.isdigit() or not minute.isdigit() or period not in ("AM", "PM"):
            return ""
        return f"{hour.zfill(2)}:{minute.zfill(2)} {period}"

    def visit(node: Any) -> None:
        if isinstance(node, list):
            for child in node:
                visit(child)
        elif isinstance(node, dict):
            if "name" in node and "openingHours" in node:
                opening = node["openingHours"]
                windows = [(None, None, opening.get("standardHours", []))]
                windows.extend(
                    (
                        season.get("from", "")[:10],
                        season.get("to", "")[:10],
                        season.get("openingHours", []),
                    )
                    for season in opening.get("seasonalHours", [])
                )
                for first, last, schedules in windows:
                    for schedule in schedules:
                        periods: list[dict[str, str]] = []
                        intervals: list[str] = []
                        labeled_intervals: list[str] = []
                        for entry in schedule.get("hours", []):
                            start = clock(entry.get("startTime") or {})
                            end = clock(entry.get("finishTime") or {})
                            if not start or not end:
                                break
                            intervals.append(f"{start} - {end}")
                            label = str(entry.get("label") or "").strip()
                            labeled_intervals.append(
                                f"{label}: {start} - {end}" if label else f"{start} - {end}"
                            )
                            if entry.get("label"):
                                periods.append(
                                    {"label": entry["label"], "start": start, "end": end}
                                )
                        else:
                            if periods:
                                for day in schedule.get("days", []):
                                    for display in (intervals, labeled_intervals):
                                        key = (
                                            node["name"], day.get("value"), first, last,
                                            "; ".join(display),
                                        )
                                        indexed[key] = periods
            else:
                for child in node.values():
                    visit(child)

    visit(value)
    return indexed


def _dining_schedules(value: Any) -> dict[tuple[Any, ...], dict[str, Any]]:
    """Reconstruct published dining intervals; incomplete clocks never mean closed."""
    indexed: dict[tuple[Any, ...], dict[str, Any]] = {}
    ambiguous: set[tuple[Any, ...]] = set()
    weekdays = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

    def clock(value: Any) -> str | None:
        if not isinstance(value, dict):
            return None
        hour, minute, period = (str(value.get(key, "")) for key in ("hour", "minute", "period"))
        if not (re.fullmatch(r"0?[1-9]|1[0-2]", hour)
                and re.fullmatch(r"[0-5]\d", minute) and period.upper() in {"AM", "PM"}):
            return None
        return f"{hour.zfill(2)}:{minute} {period.upper()}"

    def visit(node: Any) -> None:
        if isinstance(node, list):
            for child in node:
                visit(child)
        elif isinstance(node, dict):
            if "name" not in node or not isinstance(node.get("openingHours"), dict):
                for child in node.values():
                    visit(child)
                return
            opening = node["openingHours"]
            windows: list[tuple[str | None, str | None, Any, bool]] = [
                (None, None, opening.get("standardHours", []), False)
            ]
            for season in opening.get("seasonalHours", []):
                first_date, last_date = _date(season.get("from")), _date(season.get("to"))
                if first_date is not None and last_date is not None:
                    windows.append((first_date.isoformat(), last_date.isoformat(),
                                    season.get("openingHours", []), True))
            for first, last, groups, seasonal in windows:
                for day in weekdays:
                    matching = [g for g in groups if any(
                        d.get("value") == day for d in g.get("days", [])
                    )]
                    if not matching and not seasonal:
                        continue
                    intervals: list[str] = []
                    periods: list[dict[str, str]] = []
                    for group in matching:
                        hours = group.get("hours", [])
                        if not hours:
                            intervals.append("Hours unavailable")
                        for entry in hours:
                            label = str(entry.get("label") or "").strip()
                            if re.match(r"^(closed|no service)\b", label, re.IGNORECASE):
                                intervals.append("Closed")
                                continue
                            start = clock(entry.get("startTime"))
                            end = clock(entry.get("finishTime"))
                            prefix = f"{label}: " if label else ""
                            if start is None or end is None:
                                intervals.append(prefix + "Hours unavailable")
                                continue
                            intervals.append(prefix + f"{start} - {end}")
                            if label:
                                periods.append({"label": label, "start": start, "end": end})
                    schedule = "; ".join(intervals) or "Hours unavailable"
                    if seasonal and schedule == "Closed":
                        schedule = "Closed (seasonal closure)"
                    key = (node["name"], day, first, last)
                    result = {"schedule": schedule, "periods": periods}
                    if key in indexed and indexed[key] != result:
                        ambiguous.add(key)
                    indexed[key] = result

    visit(value)
    return {key: result for key, result in indexed.items() if key not in ambiguous}
