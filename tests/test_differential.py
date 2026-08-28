"""The harness's own tests: a differential run that cannot fail is worthless.

Every case here breaks a port on purpose, in one of the ways a reimplementation
actually breaks one, and asserts the harness both notices and files it at the
severity that decides whether a method may be cut over. A green differential
run means something only because these are green too.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest
from tools.differential.capture import CorpusRejected, capture, validated
from tools.differential.corpus import CASES, Case, cases
from tools.differential.diff import Severity, compare
from tools.differential.stub import StubData

from rockygpt_brain.services.data import DataPort, DataUnavailable

NOW = datetime(2026, 9, 16, 10, 30, tzinfo=ZoneInfo("America/New_York"))

CLUBS = [
    {"name": "#WeAreRCNJ", "category": "Athletics", "websiteUrl": "https://a", "internalId": 1},
    {"name": "Chess Club", "category": "Academic", "websiteUrl": "https://b", "internalId": 2},
    {"name": "Debate", "category": "Academic", "websiteUrl": "https://c", "internalId": 3},
]

CASE = Case(name="t/clubs", capability="clubs", filters={}, now=NOW, covers="test")


class Mutant:
    """A DataPort that answers like `inner` but bends the records on the way out."""

    def __init__(self, inner: DataPort, bend: Any) -> None:
        self._inner = inner
        self._bend = bend

    def __getattr__(self, name: str) -> Any:
        inner_method = getattr(self._inner, name)

        async def call(query: dict[str, Any]) -> list[dict[str, Any]]:
            return cast(list[dict[str, Any]], self._bend(await inner_method(query)))

        return call


async def _diff(bend: Any) -> list[Any]:
    base = StubData(clubs=CLUBS)
    left = await capture(CASE, base)
    right = await capture(CASE, cast(DataPort, Mutant(StubData(clubs=CLUBS), bend)))
    return compare(left, right, "base", "mutant")


async def test_identical_ports_do_not_diverge() -> None:
    assert await _diff(lambda rows: rows) == []


async def test_reordering_is_warned_not_ignored() -> None:
    found = await _diff(lambda rows: list(reversed(rows)))
    assert found, "a reordered result must not compare equal"
    assert {d.kind for d in found} == {"reordered"}
    assert {d.severity for d in found} == {Severity.WARN}


async def test_a_dropped_record_blocks() -> None:
    found = await _diff(lambda rows: rows[:-1])
    assert any(d.kind == "missing" and d.severity is Severity.BLOCKING for d in found)


async def test_an_added_record_blocks() -> None:
    extra = {"name": "Extra", "category": "X", "websiteUrl": "https://d", "internalId": 4}
    found = await _diff(lambda rows: [*rows, extra])
    assert any(d.kind == "extra" and d.severity is Severity.BLOCKING for d in found)


async def test_drift_in_a_published_field_blocks() -> None:
    def bend(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{**r, "category": r["category"].upper()} for r in rows]

    found = await _diff(bend)
    assert found
    assert all(d.kind == "field-drift" for d in found)
    assert all(d.severity is Severity.BLOCKING for d in found)
    assert any("category" in d.detail for d in found)


async def test_drift_in_an_unpublished_field_is_only_information() -> None:
    """`internalId` is not in the clubs registry entry, so it cannot reach an answer."""

    def bend(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{**r, "internalId": r["internalId"] + 100} for r in rows]

    found = await _diff(bend)
    assert found
    assert all(d.severity is Severity.INFO for d in found)
    assert all(d.kind == "internal-drift" for d in found)


async def test_a_failing_port_blocks() -> None:
    def bend(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        raise DataUnavailable("boom")

    found = await _diff(bend)
    assert any(d.severity is Severity.BLOCKING for d in found)
    assert any(d.kind in {"failure", "call-error"} for d in found)


async def test_a_differently_shaped_query_blocks() -> None:
    """Two ports asked different questions is a finding upstream of any record."""
    base = StubData(clubs=CLUBS)
    left = await capture(CASE, base)
    right = await capture(
        Case(name="t/clubs", capability="clubs", filters={"category": "Academic"}, now=NOW),
        StubData(clubs=CLUBS),
    )
    found = compare(left, right, "base", "mutant")
    assert any(d.kind == "unasked" and d.severity is Severity.BLOCKING for d in found)


# --- the corpus itself ------------------------------------------------------


def test_every_corpus_case_names_a_real_capability() -> None:
    from rockygpt_brain.capabilities.registry import capability_for

    for case in CASES:
        assert capability_for(case.capability) is not None, case.name


def test_every_corpus_filter_survives_plan_validation() -> None:
    """A case the planner's own validation would reject can never run.

    This is the check that would have caught the first draft of the corpus,
    which wrote instants the way a person says them and 400ed against the data
    service for a reason no implementation difference would ever reproduce.
    """
    from rockygpt_brain.capabilities.registry import capability_for

    for case in CASES:
        entry = capability_for(case.capability)
        assert entry is not None
        try:
            validated(case.filters, entry, case.now)
        except CorpusRejected as exc:  # pragma: no cover - the assert is the report
            pytest.fail(f"{case.name}: {exc}")


def test_corpus_case_names_are_unique() -> None:
    names = [case.name for case in CASES]
    assert len(names) == len(set(names))


def test_no_case_reads_the_wall_clock() -> None:
    """Every case pins its own instant, which is what makes a diff mean anything."""
    for case in CASES:
        assert case.now.tzinfo is not None, case.name


def test_only_filter_selects_by_name_or_capability() -> None:
    assert all(c.capability == "hours" for c in cases("hours"))
    assert {c.name for c in cases("transportation/route")} == {"transportation/route"}
    assert cases(None) == CASES
