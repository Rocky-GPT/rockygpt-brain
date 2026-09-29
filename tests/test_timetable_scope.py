"""A multi-day "next" claim is checked against every calculated timetable (09-29)."""

import json
from datetime import datetime
from typing import Any
from unittest.mock import Mock
from zoneinfo import ZoneInfo

from rockygpt_brain.contracts import Answer, ChatMessage
from rockygpt_brain.core.reviewer import review_answer
from test_engine import RECORD, review

# Monday night, after the last departure of the day.
MONDAY_NIGHT = datetime(2026, 9, 28, 22, tzinfo=ZoneInfo("America/New_York"))


def trip(record_id: str) -> dict[str, Any]:
    return {**RECORD, "id": record_id, "title": record_id, "collection": "shuttle"}


def lookup(ids: list[str], calculation: str = "ok") -> dict[str, Any]:
    return {
        "tool": "search_campus", "status": "ok", "evidence_ids": ids,
        "schedule_calculations": {"status": calculation},
    }


def scope_for(parts: list[dict[str, Any]], retrievals: list[dict[str, Any]]) -> dict[str, Any]:
    evidence = {
        record_id: trip(record_id)
        for retrieval in retrievals for record_id in retrieval["evidence_ids"]
    }
    evidence[RECORD["id"]] = RECORD
    client = Mock()
    client.create.return_value = review(*["supported"] * len(parts))
    review_answer(
        Answer.model_validate({"status": "answered", "parts": parts}),
        messages=[ChatMessage(role="user", content="When is the next shuttle?")],
        evidence=evidence,
        client=client,
        model="test",
        now=MONDAY_NIGHT,
        timeout=10,
        retrievals=retrievals,
    )
    scope: dict[str, Any] = json.loads(client.create.call_args.kwargs["input"])["citation_scope"]
    return scope


MONDAY = ["shuttle:mon-0700", "shuttle:mon-2140"]
TUESDAY = ["shuttle:tue-0700", "shuttle:tue-0800"]
NEXT_TUESDAY = {
    "kind": "campus_fact", "text": "The next shuttle is Tuesday at 7:00 AM.",
    "evidence_ids": ["shuttle:tue-0700"],
}
REGISTRAR = {
    "kind": "campus_fact", "text": "The Registrar is in D-224.", "evidence_ids": [RECORD["id"]],
}


def test_a_later_days_trip_brings_the_earlier_days_timetable_into_scope() -> None:
    scope = scope_for([NEXT_TUESDAY], [lookup(MONDAY), lookup(TUESDAY)])
    assert set(scope["0"]) == {*MONDAY, *TUESDAY}
    assert scope["0"][0] == "shuttle:tue-0700"


def test_an_uncalculated_lookups_trips_are_not_added() -> None:
    scope = scope_for([NEXT_TUESDAY], [lookup(MONDAY, "unavailable"), lookup(TUESDAY)])
    assert set(scope["0"]) == set(TUESDAY)


def test_non_shuttle_citations_keep_their_own_scope() -> None:
    scope = scope_for([NEXT_TUESDAY, REGISTRAR], [lookup(MONDAY), lookup(TUESDAY)])
    assert scope["1"] == [RECORD["id"]]
