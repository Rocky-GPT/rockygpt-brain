"""The Templates page lists every answer code writes, with examples the real code writes."""

import re
from pathlib import Path
from typing import Any, get_args

import pytest
from fastapi.testclient import TestClient

from rockygpt_brain.api.app import app
from rockygpt_brain.campus.formats import SAFETY_NET
from rockygpt_brain.campus.profile_answers import Template
from rockygpt_brain.core.routing import COMPLETE_MENU, DETAILS, DISH, MENU_CONDITION
from rockygpt_brain.core.templates import template_catalog

ENGINE = Path(__file__).parents[1] / "src/rockygpt_brain/core/engine.py"


def by_id() -> dict[str, dict[str, Any]]:
    return {entry["id"]: entry for entry in template_catalog()["templates"]}


def test_every_example_is_written_by_its_template() -> None:
    templates = by_id()
    for entry in templates.values():
        for example in entry["examples"]:
            assert example["error"] is None, (entry["id"], example["error"])
            assert example["answer"]
    answers = {key: entry["examples"][0]["answer"]
               for key, entry in templates.items()}
    assert "That's 6 of the 8 lunch items." in answers["menu"]
    assert "That's all 8 lunch items on the published menu." in answers["full_menu"]
    assert answers["hours"].startswith("Birch Tree Inn on Monday, September 21: Breakfast")
    assert templates["hours"]["examples"][1]["answer"].startswith(
        "Lunch at Birch Tree Inn on Monday, September 21 is 11:30 AM to 2:00 PM.")
    assert "Phone: 201-684-7695" in answers["contact_facts"]
    assert "Office: D-224" in answers["directory_contact"]
    assert answers["menu_list"].startswith("Published dinner menu at Birch Tree Inn")
    assert "scheduled to close at 11:00 PM" in answers["hours_list"]
    assert "Ramsey Route 17 at 12:30 PM" in answers["shuttle"]
    # Each kind of danger Jev can read shows its own safety block.
    blocks = [example["answer"] for example in templates["safety_block"]["examples"]]
    assert all(any(block.startswith(text) for block in blocks) for text in SAFETY_NET.values())


def test_every_mode_and_jev_template_the_brain_writes_is_listed() -> None:
    templates = by_id()
    modes = {entry["mode"] for entry in templates.values()}
    # Every response mode the engine names for an answer code writes.
    written = set(re.findall(r'"(exact_\w+)"', ENGINE.read_text()))
    assert written - {"exact_plus_reviewed"} <= modes
    assert set(get_args(Template)) <= set(templates)


def test_jev_s_questions_are_its_live_wording() -> None:
    templates = by_id()
    wording = {text for entry in templates.values()
               for condition in entry["when"]
               for text in condition["wording"]}
    assert {DETAILS["menu"][0], DETAILS["hours"][0], MENU_CONDITION[0], COMPLETE_MENU[0],
            DISH[0]} <= wording


def test_the_route_serves_the_catalog_on_a_development_brain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BRAIN_ENVIRONMENT", "development")
    response = TestClient(app).get("/v1/templates")
    assert response.status_code == 200
    body = response.json()
    assert [group["id"] for group in body["groups"]] == ["jev", "gpt", "fixed"]
    assert body["thresholds"]["yes"] >= 0.9
    assert len(body["templates"]) == len(by_id())
