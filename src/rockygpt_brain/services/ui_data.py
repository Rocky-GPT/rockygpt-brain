from __future__ import annotations

import re
from datetime import UTC, date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from rockygpt_brain.services.artifacts import ArtifactPort, PublishedArtifact
from rockygpt_brain.services.directory_query import (
    build_directory_payload,
    load_map_locations,
    resolve_map_location,
)

CAMPUS_TIMEZONE = ZoneInfo("America/New_York")
PUBLIC_ARTIFACTS = frozenset({"calendar", "clubs", "courses", "events", "hours", "programs"})


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _array(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _iso_date(value: str) -> date:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("date must use YYYY-MM-DD")
    return date.fromisoformat(value)


def _target_instant(value: date) -> datetime:
    # Midday avoids a date crossing while converting into the campus timezone.
    return datetime.combine(value, time(12), UTC).astimezone(CAMPUS_TIMEZONE)


def _date_in_season(target: datetime, start: Any, finish: Any) -> bool:
    if not isinstance(start, str) or not isinstance(finish, str):
        return False
    try:
        start_at = datetime.fromisoformat(start.replace("Z", "+00:00"))
        finish_at = datetime.fromisoformat(finish.replace("Z", "+00:00"))
    except ValueError:
        return False
    if start_at.tzinfo is None:
        start_at = start_at.replace(tzinfo=UTC)
    if finish_at.tzinfo is None:
        finish_at = finish_at.replace(tzinfo=UTC)
    return start_at <= target.astimezone(UTC) <= finish_at


def _format_time(value: Any) -> str | None:
    raw = _object(value)
    hour = raw.get("hour")
    minute = raw.get("minute")
    period = raw.get("period")
    if not all(isinstance(part, str) and part for part in (hour, minute, period)):
        return None
    return f"{hour}:{minute} {period}"


def _format_range(value: Any) -> dict[str, str]:
    raw = _object(value)
    start = _format_time(raw.get("startTime"))
    finish = _format_time(raw.get("finishTime"))
    label = raw.get("label") if isinstance(raw.get("label"), str) else None
    if not start or not finish:
        return {**({"label": label} if label else {}), "time": "Closed"}
    formatted = f"{start} - {finish}"
    return {**({"label": label} if label else {}), "time": formatted}


def _emoji(name: str) -> str:
    known = {"birch tree inn": "🍽️", "dunkin'": "☕", "the atrium": "🥗"}
    lowered = name.casefold()
    if lowered in known:
        return known[lowered]
    return "☕" if "starbucks" in lowered else "🏢"


def _opening_hours(fragment: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    main = _object(_object(fragment.get("content")).get("main"))
    name = main.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError("dining location has no name")
    return name, _object(main.get("openingHours"))


def _today_hours(fragment: dict[str, Any], target: datetime) -> dict[str, Any]:
    name, opening = _opening_hours(fragment)
    weekday = target.strftime("%A")
    seasonal = _array(opening.get("seasonalHours"))

    for season_value in seasonal:
        season = _object(season_value)
        if not _date_in_season(target, season.get("from"), season.get("to")):
            continue
        groups = _array(season.get("openingHours"))
        if not groups:
            return {
                "name": name,
                "emoji": _emoji(name),
                "todayLabel": weekday,
                "isOverride": True,
                "overrideNote": "Seasonal closure",
                "hours": [{"time": "Closed"}],
            }
        for group_value in groups:
            group = _object(group_value)
            days = [
                _object(day_value).get("value") for day_value in _array(group.get("days"))
            ]
            if weekday not in days:
                continue
            hours = [_format_range(value) for value in _array(group.get("hours"))]
            hours = hours or [{"time": "Closed"}]
            first_label = hours[0].get("label")
            closed = all(entry["time"] == "Closed" for entry in hours)
            return {
                "name": name,
                "emoji": _emoji(name),
                "todayLabel": weekday,
                "isOverride": True,
                **(
                    {"overrideNote": first_label or "Seasonal closure"}
                    if closed or first_label
                    else {}
                ),
                "hours": hours,
            }
        return {
            "name": name,
            "emoji": _emoji(name),
            "todayLabel": weekday,
            "isOverride": True,
            "overrideNote": "Seasonal closure",
            "hours": [{"time": "Closed"}],
        }

    for group_value in _array(opening.get("standardHours")):
        group = _object(group_value)
        days = [_object(day_value).get("value") for day_value in _array(group.get("days"))]
        if weekday not in days:
            continue
        hours = [_format_range(value) for value in _array(group.get("hours"))]
        hours = hours or [{"time": "Closed"}]
        closed = all(entry["time"] == "Closed" for entry in hours)
        has_seasonal = bool(seasonal)
        return {
            "name": name,
            "emoji": _emoji(name),
            "todayLabel": weekday,
            "isOverride": closed and has_seasonal,
            **(
                {"overrideNote": "Seasonal closure"} if closed and has_seasonal else {}
            ),
            "hours": hours,
        }

    has_seasonal = bool(seasonal)
    return {
        "name": name,
        "emoji": _emoji(name),
        "todayLabel": weekday,
        "isOverride": has_seasonal,
        **({"overrideNote": "Seasonal closure"} if has_seasonal else {}),
        "hours": [{"time": "Closed"}],
    }


def _general_hours(fragment: dict[str, Any]) -> dict[str, Any]:
    name, opening = _opening_hours(fragment)
    schedule: list[dict[str, Any]] = []
    for group_value in _array(opening.get("standardHours")):
        group = _object(group_value)
        days = ", ".join(
            str(_object(day_value).get("value"))
            for day_value in _array(group.get("days"))
            if _object(day_value).get("value")
        )
        schedule.append(
            {
                "days": days,
                "hours": [_format_range(value) for value in _array(group.get("hours"))],
            }
        )
    if not schedule:
        schedule.append(
            {
                "days": "Summer Schedule",
                "hours": [{"label": "Status", "time": "Closed for seasonal break"}],
            }
        )
    return {"name": name, "emoji": _emoji(name), "schedule": schedule}


def _dining_fragments(payload: Any) -> list[dict[str, Any]]:
    composition = _object(payload).get("composition")
    subject = _object(_object(composition).get("subject"))
    fragments: list[dict[str, Any]] = []
    for region_value in _array(subject.get("regions")):
        region = _object(region_value)
        fragments.extend(
            fragment
            for value in _array(region.get("fragments"))
            if (fragment := _object(value)) and fragment.get("type") == "Location"
        )
    if not fragments:
        raise ValueError("dining-hours artifact has no locations")
    return fragments


def _birch_closed(payload: Any, target: datetime) -> bool:
    for fragment in _dining_fragments(payload):
        name, _ = _opening_hours(fragment)
        if "birch" not in name.casefold():
            continue
        resolved = _today_hours(fragment, target)
        return all(entry.get("time") == "Closed" for entry in resolved["hours"])
    return False


def _normalize_menu_item(value: Any) -> dict[str, Any] | None:
    raw = _object(value)
    name = raw.get("formalName")
    if not isinstance(name, str) or not name.strip():
        return None
    result: dict[str, Any] = {"formalName": name.strip()}
    for key in ("description", "calories"):
        candidate = raw.get(key)
        if isinstance(candidate, str) and candidate.strip():
            result[key] = candidate.strip()
    for key in ("isVegan", "isVegetarian"):
        if isinstance(raw.get(key), bool):
            result[key] = raw[key]
    return result


def _menu_markdown(sections: list[Any]) -> str:
    lines: list[str] = []
    for section_value in sections:
        section = _object(section_value)
        section_name = section.get("name")
        if not isinstance(section_name, str) or not section_name.strip():
            continue
        lines.extend((f"## {section_name.strip()}", ""))
        for group_value in _array(section.get("groups")):
            group = _object(group_value)
            group_name = group.get("name")
            if isinstance(group_name, str) and group_name.strip():
                lines.extend((f"### {group_name.strip()}", ""))
            for item_value in _array(group.get("items")):
                item = _normalize_menu_item(item_value)
                if item is None:
                    continue
                line = f"- **{item['formalName']}**"
                if item.get("calories"):
                    line += f" ({item['calories']}cal)"
                tags = [
                    label
                    for key, label in (("isVegan", "Vegan"), ("isVegetarian", "Vegetarian"))
                    if item.get(key)
                ]
                if tags:
                    line += f" _[{', '.join(tags)}]_"
                lines.append(line)
                if item.get("description"):
                    lines.append(f"> {item['description']}")
            lines.append("")
    return "\n".join(lines)


class UiDataService:
    """Presentation-compatible campus data, owned and served by Brain."""

    def __init__(self, artifacts: ArtifactPort) -> None:
        self._artifacts = artifacts

    async def artifact(self, key: str) -> PublishedArtifact:
        if key not in PUBLIC_ARTIFACTS:
            raise KeyError(key)
        return await self._artifacts.artifact(key)

    async def directory(self) -> tuple[dict[str, Any], PublishedArtifact]:
        artifact = await self._artifacts.artifact("faculty")
        return (
            build_directory_payload(
                artifact.payload,
                generated_at=artifact.activated_at,
                release_version=artifact.release_version,
            ),
            artifact,
        )

    async def map(self, query: str | None = None) -> dict[str, Any]:
        locations = load_map_locations()
        result: dict[str, Any] = {"locations": locations}
        if query is not None:
            result["resolved"] = resolve_map_location(query, locations)
        return result

    async def shuttle(self) -> tuple[Any, PublishedArtifact]:
        artifact = await self._artifacts.artifact("shuttle-schedule")
        return artifact.payload, artifact

    async def menu(self) -> tuple[dict[str, Any], PublishedArtifact]:
        target = datetime.now(CAMPUS_TIMEZONE)
        hours = await self._artifacts.artifact("dining-hours")
        menu = await self._artifacts.artifact("menu-context")
        if _birch_closed(hours.payload, target):
            return (
                {
                    "content": None,
                    "success": True,
                    "available": False,
                    "closed": True,
                    "closureReason": "Seasonal closure",
                },
                menu,
            )
        content = _object(menu.payload).get("content")
        if not isinstance(content, str):
            raise ValueError("menu-context artifact has no content")
        generated = re.search(r"\*Generated \(UTC\):\s*([^*]+)\*", content)
        generated_at = generated.group(1).strip() if generated else None
        return (
            {
                "content": content,
                "success": True,
                "available": True,
                "generatedUtc": generated_at,
                "fileUpdatedUtc": generated_at,
                "releaseVersion": menu.release_version,
            },
            menu,
        )

    async def menu_browse(self, value: str) -> tuple[dict[str, Any], PublishedArtifact]:
        requested = _iso_date(value)
        target = _target_instant(requested)
        hours = await self._artifacts.artifact("dining-hours")
        menu = await self._artifacts.artifact("menu-week")
        if _birch_closed(hours.payload, target):
            return (
                {
                    "content": None,
                    "success": True,
                    "available": False,
                    "closed": True,
                    "closureReason": "Seasonal closure",
                    "date": value,
                },
                menu,
            )
        dates = _array(_object(menu.payload).get("dates"))
        entry = next((_object(item) for item in dates if _object(item).get("date") == value), {})
        sections = _array(entry.get("sections"))
        has_items = any(
            _normalize_menu_item(item) is not None
            for section in sections
            for group in _array(_object(section).get("groups"))
            for item in _array(_object(group).get("items"))
        )
        if not sections or not has_items:
            return (
                {
                    "content": None,
                    "success": True,
                    "available": False,
                    "date": value,
                    "releaseVersion": menu.release_version,
                },
                menu,
            )
        return (
            {
                "content": _menu_markdown(sections),
                "success": True,
                "available": True,
                "date": value,
                "releaseVersion": menu.release_version,
            },
            menu,
        )

    async def dining_hours(self, value: str | None) -> tuple[dict[str, Any], PublishedArtifact]:
        requested = _iso_date(value) if value else datetime.now(CAMPUS_TIMEZONE).date()
        target = _target_instant(requested)
        artifact = await self._artifacts.artifact("dining-hours")
        fragments = _dining_fragments(artifact.payload)
        locations = [_today_hours(fragment, target) for fragment in fragments]
        general = [_general_hours(fragment) for fragment in fragments]

        def sort_key(item: dict[str, Any]) -> tuple[int, str]:
            return (0 if "birch" in item["name"].casefold() else 1, item["name"])

        locations.sort(key=sort_key)
        general.sort(key=sort_key)
        return (
            {
                "success": True,
                "today": target.strftime("%A"),
                "dateFormatted": f"{target.strftime('%A, %B')} {target.day}, {target.year}",
                "locations": locations,
                "generalHours": general,
                "releaseVersion": artifact.release_version,
            },
            artifact,
        )
