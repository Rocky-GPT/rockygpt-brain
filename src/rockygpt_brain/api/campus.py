"""Read-only campus panels for the student app.

The student app's Dining, Shuttle, Map, Events, Clubs, Calendar and Majors
panels browse published data instead of asking a question. These routes read
the active release: menus, dining hours and shuttle trips from the same
tables chat retrieval uses, so a panel and an answer cannot disagree about
what was published, and events, clubs, calendar, programs, courses and
buildings from the release artifacts the data pipeline publishes for them.

Nothing here infers a value. Rows are reshaped for display and every
payload names the dataset version it came from.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse

from rockygpt_brain.retrieval.data import CAMPUS_ZONE, CampusData

router = APIRouter(prefix="/v1")

# Published artifacts the panels draw exactly as released.
PANEL_ARTIFACTS = frozenset({"events", "clubs", "calendar", "programs", "courses"})
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
CAMPUS_MAP_URL = "https://map.ramapo.edu/?id=2292"
DINING_HALL = "Birch Tree Inn"
DateParam = Annotated[str | None, Query(alias="date", pattern=r"^\d{4}-\d{2}-\d{2}$")]
_TIME_RANGE = re.compile(
    r"^(?:(?P<label>.*?):\s*)?(?P<start>\d{1,2}:\d{2}\s*[AP]M)\s*-\s*(?P<end>\d{1,2}:\d{2}\s*[AP]M)$",
    re.IGNORECASE,
)


@contextmanager
def _campus() -> Iterator[CampusData]:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise HTTPException(status_code=503, detail="Campus data is unavailable")
    data = CampusData(database_url, datetime.now(CAMPUS_ZONE))
    try:
        data._ensure_loaded()
        yield data
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=503, detail="Campus data is unavailable") from None
    finally:
        data.close()


def _day(value: str | None, data: CampusData) -> date:
    if value is None:
        return data.today
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=422, detail="date must be YYYY-MM-DD") from None


def _applies(row: dict[str, Any], day: date) -> bool:
    start, end = row.get("valid_from"), row.get("valid_until")
    return (start is None or start <= day) and (end is None or day <= end)


# ─── Artifacts ────────────────────────────────────────────────────────────


@router.get("/data/{artifact}")
def panel_artifact(artifact: str, request: Request) -> Response:
    """A published artifact exactly as released, revalidated by its content hash."""
    if artifact not in PANEL_ARTIFACTS:
        raise HTTPException(status_code=404, detail="Unknown campus dataset")
    with _campus() as data:
        rows = data._fetch(
            "SELECT payload, content_hash FROM rockygpt_v2.release_artifacts "
            "WHERE dataset_version_id=%s::uuid AND artifact_key=%s",
            (data.dataset["id"], artifact),
        )
        version = str(data.dataset["version"])
    if not rows:
        raise HTTPException(status_code=503, detail="Campus data is unavailable")
    etag = f'"{rows[0]["content_hash"]}"'
    headers = {"ETag": etag, "Cache-Control": "no-cache", "X-RockyGPT-Release": version}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return JSONResponse(rows[0]["payload"], headers=headers)


# ─── Dining ───────────────────────────────────────────────────────────────


def _meal_rank(meal: str) -> int:
    name = meal.lower()
    for rank, word in ((10, "breakfast"), (20, "continental"), (25, "brunch")):
        if word in name:
            return rank
    if "lunch" in name:
        return 35 if "lite" in name else 30
    if "dinner" in name:
        return 40
    return 50 if "late" in name else 100


def _plain(text: str) -> str:
    # The panel's parser reads the first "(… cal)" group and the first **name**,
    # so a name may not carry its own parentheses or emphasis markers.
    return text.replace("(", "[").replace(")", "]").replace("*", "").replace("_[", "[").strip()


def _menu_markdown(rows: list[dict[str, Any]]) -> str | None:
    meals: dict[str, dict[str, list[str]]] = {}
    for row in rows:
        meal = str(row.get("meal") or "").strip()
        name = _plain(str(row.get("name") or ""))
        if not meal or not name:
            continue
        station = _plain(str(row.get("station") or "")) or "Menu"
        items = meals.setdefault(meal, {}).setdefault(station, [])
        if any(line.startswith(f"- **{name}**") for line in items):
            continue
        coverage = row.get("label_coverage") or {}
        tags = []
        if row.get("vegan") and coverage.get("vegan") == "published":
            tags.append("Vegan")
        if row.get("vegetarian") and coverage.get("vegetarian") == "published":
            tags.append("Vegetarian")
        if coverage.get("allergens") == "published":
            tags += [
                f"Contains {allergen.strip()}"
                for allergen in row.get("allergens") or []
                if isinstance(allergen, str) and allergen.strip()
            ]
        line = f"- **{name}**"
        if isinstance(row.get("calories"), int):
            line += f" ({row['calories']} cal)"
        if tags:
            line += f" _[{', '.join(tags)}]_"
        items.append(line)
    if not meals:
        return None
    lines: list[str] = []
    for meal in sorted(meals, key=_meal_rank):
        lines.append(f"## {meal}")
        for station, items in meals[meal].items():
            lines.append(f"### {station}")
            lines.extend(items)
    return "\n".join(lines)


def _period_time(start: str, end: str) -> str:
    def clean(value: str) -> str:
        return re.sub(r"\s+", " ", value.strip().upper()).lstrip("0")

    return f"{clean(start)} – {clean(end)}"


def _periods(schedule: str | None) -> list[dict[str, str]]:
    if not schedule or not schedule.strip():
        return [{"label": "Hours", "time": "Not published"}]
    text = schedule.strip()
    if text.lower().startswith("closed"):
        return [{"label": "Status", "time": text}]
    if text.lower() == "hours unavailable":
        return [{"label": "Hours", "time": "Not published"}]
    periods = []
    for segment in (part.strip() for part in text.split(";")):
        match = _TIME_RANGE.match(segment)
        if match:
            periods.append({
                "label": (match.group("label") or "Open").strip(),
                "time": _period_time(match.group("start"), match.group("end")),
            })
        elif segment:
            periods.append({"label": "Hours", "time": segment})
    return periods or [{"label": "Hours", "time": "Not published"}]


def _dining_hours(data: CampusData, day: date) -> list[dict[str, Any]]:
    rows = data._fetch(
        "SELECT name, day, schedule, valid_from, valid_until FROM rockygpt_v2.dining_hours "
        "WHERE dataset_version_id=%s::uuid ORDER BY name",
        (data.dataset["id"],),
    )
    weekday = WEEKDAYS[day.weekday()]
    names = sorted({str(row["name"]) for row in rows}, key=lambda n: (n != DINING_HALL, n))
    locations = []
    for name in names:
        todays = [row for row in rows if row["name"] == name and row["day"] == weekday]
        # A dated row (a holiday, a closure) replaces the weekly one it covers.
        dated = [row for row in todays if row.get("valid_from") and _applies(row, day)]
        weekly = [row for row in todays if not row.get("valid_from") and not row.get("valid_until")]
        chosen = dated[0] if dated else (weekly[0] if weekly else None)
        locations.append({
            "name": name,
            "emoji": "",
            "isOverride": bool(dated),
            "overrideNote": "Special hours" if dated else None,
            "hours": _periods(chosen["schedule"] if chosen else None),
        })
    return locations


@router.get("/dining-hours")
def dining_hours(date_param: DateParam = None) -> dict[str, Any]:
    with _campus() as data:
        day = _day(date_param, data)
        return {
            "date": day.isoformat(),
            "dateFormatted": f"{day:%A, %B} {day.day}",
            "locations": _dining_hours(data, day),
            "datasetVersion": str(data.dataset["version"]),
        }


def _menu(data: CampusData, day: date) -> dict[str, Any]:
    rows = data._fetch(
        "SELECT meal, station, name, calories, vegan, vegetarian, allergens, label_coverage "
        "FROM rockygpt_v2.menu_items WHERE dataset_version_id=%s::uuid "
        "AND valid_from <= %s AND coalesce(valid_until, valid_from) >= %s "
        "ORDER BY meal, station, name",
        (data.dataset["id"], day, day),
    )
    content = _menu_markdown(rows)
    hall = next((loc for loc in _dining_hours(data, day) if loc["name"] == DINING_HALL), None)
    closure = next(
        (period["time"] for period in (hall or {}).get("hours", [])
         if period["time"].lower().startswith("closed")),
        None,
    )
    return {
        "date": day.isoformat(),
        "available": content is not None,
        "content": content,
        # A closed hall with no menu is a closure, not a missing menu.
        "closed": content is None and closure is not None,
        "closureReason": closure,
        "datasetVersion": str(data.dataset["version"]),
    }


@router.get("/menu")
def menu_today() -> dict[str, Any]:
    with _campus() as data:
        return _menu(data, data.today)


@router.get("/menu/browse")
def menu_for_date(date_param: DateParam = None) -> dict[str, Any]:
    with _campus() as data:
        return _menu(data, _day(date_param, data))


# ─── Shuttle ──────────────────────────────────────────────────────────────


@router.get("/shuttle")
def shuttle() -> dict[str, Any]:
    """Roadrunner Express by service day, plus the weekday train-station loop."""
    with _campus() as data:
        rows = data._fetch(
            "SELECT r.name, r.service_day, r.valid_from AS route_from, "
            "r.valid_until AS route_until, t.sequence, t.departure, t.arrival, t.stops, "
            "t.valid_from, t.valid_until FROM rockygpt_v2.shuttle_routes r "
            "JOIN rockygpt_v2.shuttle_trips t ON t.route_id = r.id "
            "WHERE r.dataset_version_id=%s::uuid AND t.dataset_version_id=%s::uuid "
            "ORDER BY r.name, t.sequence",
            (data.dataset["id"], data.dataset["id"]),
        )
        today = data.today
        version = str(data.dataset["version"])
    schedule: dict[str, list[dict[str, Any]]] = {
        "weekday": [], "saturday": [], "sunday": [], "trainLoop": [],
    }
    for row in rows:
        route_window = {"valid_from": row["route_from"], "valid_until": row["route_until"]}
        if not _applies(row, today) or not _applies(route_window, today):
            continue
        stops = [
            {"location": str(stop["location"]), "time": str(stop["time"])}
            for stop in row.get("stops") or []
            if isinstance(stop, dict) and stop.get("location") and stop.get("time")
        ]
        trip = {"departure": row["departure"], "arrival": row["arrival"], "stops": stops}
        service_day = str(row["service_day"]).lower()
        if "roadrunner" in str(row["name"]).lower() and service_day in schedule:
            schedule[service_day].append(trip)
        elif service_day == "weekday":
            schedule["trainLoop"].append(trip)
    return {**schedule, "datasetVersion": version}


# ─── Map ──────────────────────────────────────────────────────────────────


@router.get("/map")
def campus_map() -> dict[str, Any]:
    """Every published building with its map marker, plus the whole-campus view."""
    with _campus() as data:
        payload = data._artifact("campus-buildings")
        version = str(data.dataset["version"])
    buildings = payload.get("buildings", []) if isinstance(payload, dict) else []
    locations: list[dict[str, Any]] = [{
        "key": "layer_campus_map", "name": "Ramapo campus", "type": "layer",
        "mapUrl": CAMPUS_MAP_URL, "aliases": ["campus", "campus map"], "roomPrefixes": [],
    }]
    for building in buildings:
        name, url = building.get("name"), building.get("map_url")
        if not isinstance(name, str) or not isinstance(url, str) or not url.startswith("https://"):
            continue
        prefixes = [
            prefix for prefix in building.get("room_prefixes") or [] if isinstance(prefix, str)
        ]
        marker = building.get("concept3d_id") or re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
        locations.append({
            "key": f"building_{marker}",
            "name": name,
            "type": "building",
            "mapUrl": url,
            "aliases": prefixes,
            "roomPrefixes": prefixes,
            "description": building.get("category"),
        })
    return {"locations": locations, "datasetVersion": version}
