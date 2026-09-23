"""Menu nutrition stays attached to one dated, meal/station-scoped occurrence."""
from __future__ import annotations

import copy
from typing import Any
from uuid import UUID

import pytest

import test_projection
from rockygpt_brain.retrieval.entity_facts import EntityFacts
from rockygpt_brain.retrieval.graph import GraphData
from rockygpt_brain.retrieval.menu_artifacts import menu_artifact_index, supplement_menu
from rockygpt_brain.retrieval.processing import enrich_records
from rockygpt_brain.retrieval.projection import valid_value
from test_projection import ENTITY_ID, Fixture

fixture = test_projection.fixture


def payload() -> dict[str, Any]:
    return {"version": 1, "dates": [{"date": "2026-09-21", "sections": [
        {"name": "Lunch", "groups": [{"name": "Grill", "items": [{
            "formalName": "Same dish", "description": "Source description.",
            "ingredients": "Wheat, water.",
            "nutrients": {"fat": "6", "saturatedFat": "3g", "addedSugar": "", "calories": "205"},
            "isMindful": False, "isPlantBased": True,
        }]}]},
    ]}]}


def record() -> dict[str, Any]:
    return {"collection": "menu", "id": "menu:one", "source_key": "dining",
            "source_record_key": "2026-09-21:Lunch:Grill:Same dish",
            "valid_from": "2026-09-21", "valid_until": "2026-09-21",
            "fields": {"meal": "Lunch", "station": "Grill", "name": "Same dish"},
            "limitations": [], "coverage": {"fields": {}}}


@pytest.mark.parametrize("wrong", ["date", "meal", "station", "name", "key", "duplicate"])
def test_no_nutrition_from_other_occurrences_or_ambiguous_duplicates(wrong: str) -> None:
    raw = payload()
    row = record()
    if wrong == "date":
        raw["dates"][0]["date"] = "2026-09-22"
    elif wrong == "meal":
        raw["dates"][0]["sections"][0]["name"] = "Dinner"
    elif wrong == "station":
        raw["dates"][0]["sections"][0]["groups"][0]["name"] = "Other"
    elif wrong == "name":
        raw["dates"][0]["sections"][0]["groups"][0]["items"][0]["formalName"] = "Other dish"
    elif wrong == "key":
        row["source_record_key"] = "2026-09-21:Lunch:Other:Same dish"
    else:
        items = raw["dates"][0]["sections"][0]["groups"][0]["items"]
        items.append(copy.deepcopy(items[0]))
    assert supplement_menu(row, menu_artifact_index(raw)) == {}
    assert "artifact_path" not in row


def test_menu_search_enrichment_keeps_explicit_units_empty_values_and_source_locator() -> None:
    row = record()
    raw = payload()
    enrich_records("menu", [row], lambda key: raw if key == "menu-week" else None)
    assert row["fields"]["ingredients"] == "Wheat, water."
    assert row["fields"]["nutrients"] == {"fat": "6", "saturatedFat": "3g", "addedSugar": "",
                                         "calories": "205"}
    assert row["artifact_path"] == ["dates", "0", "sections", "0", "groups", "0", "items", "0"]
    assert row["artifact_key"] == "menu-week"
    assert row["fields"]["isMindful"] is False
    assert "no unit may be inferred" in row["limitations"][0]
    assert valid_value(row["fields"]["nutrients"], "nutrients")
    assert not valid_value({"fat": True}, "nutrients")
    assert not valid_value({"extra": "6g"}, "nutrients")


def test_menu_graph_occurrence_exposes_nutrients_with_preserved_sql_row(fixture: Fixture) -> None:
    data, snapshot, records = fixture
    data._artifacts["menu-week"] = payload()
    raw = copy.deepcopy(records["menu"][0]["raw_record"])
    raw["source_record_key"] = "2026-09-21:Lunch:Grill:Same dish"
    before = copy.deepcopy(raw)
    records["menu"] = [GraphData(data)._record("menu", {
        "record": raw, "source": data.sources["dining"]})]
    facts = EntityFacts(data, snapshot).build(UUID(ENTITY_ID), include_records=True)
    menu = next(group for group in facts.record_groups if group.key == "menu_offerings")
    props = {prop.key: prop for prop in menu.records[0].properties}
    assert props["ingredients"].values[0].value == "Wheat, water."
    assert props["nutrients"].values[0].value["fat"] == "6"
    assert props["nutrients"].values[0].value["saturatedFat"] == "3g"
    assert props["nutrients"].values[0].value["addedSugar"] == ""
    assert "no unit may be inferred" in props["nutrients"].assertions[0].limitations[0]
    assert props["plant_based"].values[0].value is True
    source = next(source for source in facts.sources if source.collection == "menu")
    assert source.artifact_key == "menu-week" and source.artifact_path[-2:] == ["items", "0"]
    assert raw == before == records["menu"][0]["raw_record"]
