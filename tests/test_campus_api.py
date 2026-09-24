"""Student panels read the active release and reshape it without inferring anything."""

from __future__ import annotations

from datetime import date
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from rockygpt_brain.api.app import app

THURSDAY = date(2026, 9, 24)


class FakeCampus:
    """Just enough of CampusData: an active release, tables and artifacts."""

    def __init__(
        self,
        tables: dict[str, list[dict[str, Any]]] | None = None,
        artifacts: dict[str, Any] | None = None,
    ) -> None:
        self.tables = tables or {}
        self.artifacts = artifacts or {}
        self.today = THURSDAY
        self.dataset = {"id": "00000000-0000-0000-0000-000000000001", "version": "test-release"}
        self.closed = False

    def _ensure_loaded(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    def _artifact(self, key: str) -> Any:
        return self.artifacts.get(key)

    def _fetch(self, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        if "release_artifacts" in query:
            key = params[1]
            if key not in self.artifacts:
                return []
            return [{"payload": self.artifacts[key], "content_hash": f"hash-{key}"}]
        for table, rows in self.tables.items():
            if f"rockygpt_v2.{table}" in query:
                return rows
        return []


@pytest.fixture(autouse=True)
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://configured")


def get(fake: FakeCampus, path: str, **kwargs: Any) -> Any:
    with patch("rockygpt_brain.api.campus.CampusData", return_value=fake):
        return TestClient(app).get(path, **kwargs)


PUBLISHED = {"vegan": "published", "vegetarian": "published", "allergens": "published"}
BIRCH_WEEKLY = {
    "name": "Birch Tree Inn", "day": "Thursday", "valid_from": None, "valid_until": None,
    "schedule": "Breakfast: 08:00 AM - 10:30 AM; Lunch: 11:00 AM - 02:00 PM",
}


def test_menu_is_the_panel_markdown_with_only_published_labels() -> None:
    fake = FakeCampus(tables={
        "menu_items": [
            {"meal": "Dinner", "station": "Savory", "name": "Mac (Baked)", "calories": 470,
             "vegan": True, "vegetarian": True, "allergens": ["Milk"],
             "label_coverage": {"vegan": "unknown", "vegetarian": "published",
                                "allergens": "published"}},
            {"meal": "Lunch", "station": "Mix", "name": "Lemon Za'atar Chickpea Salad",
             "calories": 51, "vegan": True, "vegetarian": True, "allergens": ["Sesame"],
             "label_coverage": PUBLISHED},
            {"meal": "Lunch", "station": "Mix", "name": "Lemon Za'atar Chickpea Salad",
             "calories": 51, "vegan": True, "vegetarian": True, "allergens": ["Sesame"],
             "label_coverage": PUBLISHED},
            {"meal": "Breakfast", "station": "Grill", "name": "Egg", "calories": None,
             "vegan": False, "vegetarian": True, "allergens": [], "label_coverage": PUBLISHED},
        ],
        "dining_hours": [BIRCH_WEEKLY],
    })
    response = get(fake, "/v1/menu")
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True and body["closed"] is False
    assert body["date"] == "2026-09-24" and body["datasetVersion"] == "test-release"
    assert body["content"].split("\n") == [
        "## Breakfast", "### Grill", "- **Egg** _[Vegetarian]_",
        "## Lunch", "### Mix",
        "- **Lemon Za'atar Chickpea Salad** (51 cal) _[Vegan, Vegetarian, Contains Sesame]_",
        # Vegan was not published for this item, so it is not claimed.
        "## Dinner", "### Savory", "- **Mac [Baked]** (470 cal) _[Vegetarian, Contains Milk]_",
    ]
    assert fake.closed is True


def test_a_closed_hall_without_a_menu_is_a_closure_not_a_missing_menu() -> None:
    fake = FakeCampus(tables={"menu_items": [], "dining_hours": [
        BIRCH_WEEKLY,
        {"name": "Birch Tree Inn", "day": "Thursday", "schedule": "Closed (seasonal closure)",
         "valid_from": THURSDAY, "valid_until": THURSDAY},
    ]})
    body = get(fake, "/v1/menu/browse", params={"date": "2026-09-24"}).json()
    assert body["content"] is None and body["available"] is False
    assert body["closed"] is True and body["closureReason"] == "Closed (seasonal closure)"


def test_dining_hours_use_a_dated_row_over_the_weekly_one() -> None:
    fake = FakeCampus(tables={"dining_hours": [
        {"name": "Dunkin'", "day": "Thursday", "schedule": "07:30 AM - 03:00 PM",
         "valid_from": None, "valid_until": None},
        {"name": "Dunkin'", "day": "Thursday", "schedule": "Hours unavailable",
         "valid_from": date(2026, 9, 20), "valid_until": date(2026, 9, 26)},
        {"name": "Dunkin'", "day": "Thursday", "schedule": "Closed",
         "valid_from": date(2026, 8, 1), "valid_until": date(2026, 8, 2)},
        BIRCH_WEEKLY,
    ]})
    body = get(fake, "/v1/dining-hours", params={"date": "2026-09-24"}).json()
    assert body["dateFormatted"] == "Thursday, September 24"
    birch, dunkin = body["locations"]
    assert birch["name"] == "Birch Tree Inn" and birch["isOverride"] is False
    assert birch["hours"] == [
        {"label": "Breakfast", "time": "8:00 AM – 10:30 AM"},
        {"label": "Lunch", "time": "11:00 AM – 2:00 PM"},
    ]
    assert dunkin["isOverride"] is True
    assert dunkin["hours"] == [{"label": "Hours", "time": "Not published"}]


def test_shuttle_groups_roadrunner_by_day_and_keeps_only_current_trips() -> None:
    def trip(name: str, day: str, departure: str, **window: Any) -> dict[str, Any]:
        return {
            "name": name, "service_day": day, "route_from": None, "route_until": None,
            "sequence": 0, "departure": departure, "arrival": "8:00 AM",
            "stops": [{"time": "7:30 AM", "location": "Garden State Plaza"}, {"bad": True}],
            "valid_from": window.get("start"), "valid_until": window.get("end"),
        }

    fake = FakeCampus(tables={"shuttle_routes": [
        trip("Weekday Roadrunner Express", "weekday", "7:00 AM"),
        trip("Saturday Roadrunner Express", "saturday", "9:00 AM"),
        trip("Ramsey Route 17", "weekday", "7:05 AM"),
        trip("Weekday Roadrunner Express", "weekday", "6:00 AM", end=date(2026, 9, 1)),
    ]})
    body = get(fake, "/v1/shuttle").json()
    assert [route["departure"] for route in body["weekday"]] == ["7:00 AM"]
    assert [route["departure"] for route in body["saturday"]] == ["9:00 AM"]
    assert body["sunday"] == []
    assert [route["departure"] for route in body["trainLoop"]] == ["7:05 AM"]
    assert body["weekday"][0]["stops"] == [{"location": "Garden State Plaza", "time": "7:30 AM"}]


def test_map_lists_published_buildings_and_the_whole_campus() -> None:
    fake = FakeCampus(artifacts={"campus-buildings": {"buildings": [
        {"name": "Academic Building D", "map_url": "https://map.ramapo.edu/?id=2292#!m/1133346?sbc/",
         "concept3d_id": "1133346", "room_prefixes": ["D"], "category": "Academic Buildings"},
        {"name": "No Marker Hall", "map_url": None, "room_prefixes": []},
    ]}})
    locations = get(fake, "/v1/map").json()["locations"]
    assert [location["key"] for location in locations] == ["layer_campus_map", "building_1133346"]
    assert locations[1]["roomPrefixes"] == ["D"] and locations[1]["type"] == "building"


def test_panel_artifacts_pass_through_and_revalidate_by_hash() -> None:
    events = [{"title": "Game Night", "date": "Thu, Sep 24, 2026"}]
    fake = FakeCampus(artifacts={"events": events})
    response = get(fake, "/v1/data/events")
    assert response.status_code == 200 and response.json() == events
    assert response.headers["etag"] == '"hash-events"'
    again = get(fake, "/v1/data/events", headers={"if-none-match": '"hash-events"'})
    assert again.status_code == 304
    assert get(fake, "/v1/data/secrets").status_code == 404
    assert get(fake, "/v1/data/clubs").status_code == 503


def test_panels_refuse_bad_dates_and_fail_closed_without_a_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert get(FakeCampus(), "/v1/dining-hours", params={"date": "tomorrow"}).status_code == 422
    monkeypatch.delenv("DATABASE_URL")
    assert TestClient(app).get("/v1/menu").status_code == 503
