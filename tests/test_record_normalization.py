"""Cleanup preserves source distinctions instead of manufacturing facts."""

from copy import deepcopy
from typing import Any

import pytest

from rockygpt_brain.retrieval.normalization import (
    course_credits,
    faculty_phone_display,
    normalize_record,
)
from rockygpt_brain.retrieval.processing import enrich_records


def record(collection: str, **fields: Any) -> dict[str, Any]:
    return {
        "id": "stable-id",
        "collection": collection,
        "title": fields.get("name", "Example"),
        "fields": fields,
        "coverage": {"fields": {key: "published" for key in fields}},
        "limitations": [],
        "url": "https://example.edu/",
        "collected_at": "2026-09-21",
    }


@pytest.mark.parametrize(
    "raw,expected",
    [
        (4, 4),
        ("4", 4),
        (0, 0),
        (1.5, 1.5),
        ({"min": 0, "max": 4, "operator": ""}, 4),
        ({"min": 0, "max": 4, "operator": "TO"}, {"min": 0, "max": 4}),
        ({"min": 1, "max": 4, "operator": "TO"}, {"min": 1, "max": 4}),
        ({"min": 0, "operator": ""}, None),
        ("", None),
        (False, None),
        ({"min": 4, "max": 1, "operator": "TO"}, None),
    ],
)
def test_credit_shapes_preserve_ranges_zero_and_unknown(raw: Any, expected: Any) -> None:
    assert course_credits(raw) == expected
    assert course_credits(expected) == expected


@pytest.mark.parametrize(
    "location,access",
    [
        ("-", None),
        ("Private Location (sign in to display)", "sign_in_required"),
        ("Private Location (register to display)", "registration_required"),
    ],
)
def test_event_placeholders_are_not_physical_locations(location: str, access: str | None) -> None:
    row = record("events", location=location, starts_at="2026-09-21 14:00:00-04:00")
    normalize_record(row)
    assert row["fields"]["location"] is None
    assert row["coverage"]["fields"]["location"] == "not_published"
    assert row["fields"].get("location_access") == access
    assert row["fields"]["starts_at"] == "2026-09-21T14:00:00-04:00"
    before = deepcopy(row)
    normalize_record(row)
    assert row == before


def test_empty_faculty_fields_are_unknown_and_role_title_is_preserved() -> None:
    row = record(
        "faculty", name="Example", title="Professor", bio="", courses=[], phone="201.555.0100"
    )
    normalize_record(row)
    assert row["fields"]["bio"] is None
    assert row["coverage"]["fields"]["courses"] == "not_published"
    assert row["fields"]["title"] == "Professor"
    assert row["fields"]["phone"] == "201.555.0100"  # Original profile evidence.
    assert faculty_phone_display(row["fields"]["phone"]) == "(201) 555-0100"
    assert faculty_phone_display("201-555-0100 ext 2") == "201-555-0100 ext 2"
    assert (
        faculty_phone_display("(201) 555-0100 (use email instead)")
        == "(201) 555-0100 (use email instead)"
    )


def test_program_excerpts_do_not_claim_verified_careers_and_decode_entities() -> None:
    row = record(
        "programs",
        name="Example",
        description="Today&#8217;s program",
        careers="  Careers &amp; more  ",
    )
    normalize_record(row)
    assert row["fields"]["description"] == "Today’s program"
    assert row["fields"]["program_page_excerpt"] == "Careers & more"
    assert "careers" not in row["fields"]
    assert row["limitations"]
    before = deepcopy(row)
    normalize_record(row)
    assert row == before


def test_same_name_programs_never_borrow_metadata_from_each_other() -> None:
    payload = {
        "schools": [
            {
                "majors": [
                    {
                        "name": "Nursing",
                        "url": "https://example.edu/one",
                        "catalogUrl": "https://catalog.example/one",
                    },
                    {
                        "name": "Nursing",
                        "url": "https://example.edu/two",
                        "catalogUrl": "https://catalog.example/two",
                    },
                ]
            }
        ]
    }
    one = record("programs", name="Nursing", program_url="https://example.edu/one")
    unknown = record("programs", name="Nursing")
    enrich_records("programs", [one, unknown], lambda _: payload)
    assert one["fields"]["catalogUrl"] == "https://catalog.example/one"
    assert "catalogUrl" not in unknown["fields"]


@pytest.mark.parametrize("collection", ["contacts", "campus_hours", "dining_hours", "menu"])
def test_completed_collections_are_outside_general_cleanup(collection: str) -> None:
    row = record(collection, name="Example", hours=[], phone="201.555.0100")
    original = deepcopy(row)
    normalize_record(row)
    assert row == original
