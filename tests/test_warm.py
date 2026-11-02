"""Startup warming: per-release reads are filled before the first question, never fatally."""

import asyncio
import logging
from typing import Any

import pytest

from rockygpt_brain.api import app as app_module
from rockygpt_brain.engine import ChatEngine
from rockygpt_brain.retrieval import EvidenceUnavailable, MemoryEntityFacts
from test_engine import ScriptedGateway, facts


class Counting(MemoryEntityFacts):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.offices_listed = 0
        self.warmed = 0

    def list_offices(self, **kwargs: Any) -> dict[str, Any]:
        self.offices_listed += 1
        return super().list_offices(**kwargs)

    def warm_meaning_index(self) -> None:
        self.warmed += 1


def counting() -> Counting:
    base = facts()
    return Counting(dataset_version=base.dataset_version, identity_hash=base.identity_hash,
                    entities=base.entities, contacts=base.contacts, now=base.now)


def test_warm_fills_each_per_release_read_once() -> None:
    reader = counting()
    engine = ChatEngine(ScriptedGateway(), reader)
    seconds = engine.warm()
    assert set(seconds) == {"meaning_index", "directory", "clubs", "events", "venues", "stops",
                            "office_labels", "catalog"}
    assert reader.warmed == 1
    assert engine._directory_cache is not None and engine._club_cache is not None
    assert getattr(engine, "_labels_cache", None) is not None
    listed = reader.offices_listed
    engine._offices_by_service("mailroom")  # Already read: only the cheap listing again.
    assert reader.offices_listed == listed + 1


def test_a_failing_step_is_logged_and_the_rest_still_run(
        caplog: pytest.LogCaptureFixture) -> None:
    class Down(Counting):
        def warm_meaning_index(self) -> None:
            raise EvidenceUnavailable("down")

    base = facts()
    reader = Down(dataset_version=base.dataset_version, identity_hash=base.identity_hash,
                  entities=base.entities, contacts=base.contacts, now=base.now)
    engine = ChatEngine(ScriptedGateway(), reader)
    with caplog.at_level(logging.WARNING, logger="rockygpt_brain"):
        engine.warm()
    assert "brain_warm_failed step=meaning_index" in caplog.text
    assert engine._directory_cache is not None


def test_startup_does_not_wait_past_the_warm_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    class Slow:
        def warm(self) -> None:
            import time
            time.sleep(0.5)

    monkeypatch.setattr(app_module, "WARM_SECONDS", 0.05)
    asyncio.run(asyncio.wait_for(app_module._warm(Slow()), 0.4))  # type: ignore[arg-type]


def test_memory_reader_warms_without_a_meaning_index() -> None:
    facts().warm_meaning_index()  # The base hook only asks; a reader without an index is fine.


def _postgres_reader(monkeypatch: pytest.MonkeyPatch, release: tuple[str, str],
                     indexes: dict[str, Any], loaded: list[tuple[str, str]]) -> Any:
    """A Postgres reader whose active release and stored indexes are given, not queried."""
    from rockygpt_brain.retrieval.postgres import PostgresEntityFacts

    reader = PostgresEntityFacts("postgresql://unused.invalid/db")
    monkeypatch.setattr(reader, "active_release", lambda: release)

    def load(wanted: tuple[str, str]) -> None:  # The real load's bookkeeping, without a database.
        loaded.append(wanted)
        with reader._meaning_lock:
            reader._meaning = (wanted, indexes.get(wanted[0]))
            reader._meaning_loading = False
            reader._meaning_done.set()

    monkeypatch.setattr(reader, "_load_meaning", load)
    return reader


def test_postgres_loads_the_index_on_the_calling_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    loaded: list[tuple[str, str]] = []
    index = object()
    reader = _postgres_reader(monkeypatch, ("release-1", "identities-1"), {"release-1": index},
                              loaded)
    reader.warm_meaning_index()
    assert loaded == [("release-1", "identities-1")]
    assert reader.meaning_index() is index  # Checked a moment ago: no second read.
    assert len(loaded) == 1


def test_a_new_release_index_is_read_before_the_question_goes_on(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """After the nightly update a running Brain reads the new index for the first question,
    instead of searching by words alone while it loads in the background."""
    loaded: list[tuple[str, str]] = []
    old, new = object(), object()
    reader = _postgres_reader(monkeypatch, ("release-2", "identities-2"), {"release-2": new},
                              loaded)
    reader._meaning = (("release-1", "identities-1"), old)
    reader._meaning_checked = float("-inf")  # The minute-long check interval has passed.
    assert reader.meaning_index() is new
    assert loaded == [("release-2", "identities-2")]


def test_a_question_waits_for_the_new_index_another_thread_is_loading(
        monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    loaded: list[tuple[str, str]] = []
    new = object()
    release = ("release-2", "identities-2")
    reader = _postgres_reader(monkeypatch, release, {}, loaded)
    reader._meaning_loading = True
    reader._meaning_done.clear()

    def finish() -> None:
        with reader._meaning_lock:
            reader._meaning = (release, new)
            reader._meaning_loading = False
            reader._meaning_done.set()

    threading.Timer(0.05, finish).start()
    assert reader.meaning_index() is new
    assert loaded == []  # The running load was not doubled.


def test_a_release_without_an_index_is_retried_in_the_background(
        monkeypatch: pytest.MonkeyPatch) -> None:
    loaded: list[tuple[str, str]] = []
    release = ("release-1", "identities-1")
    reader = _postgres_reader(monkeypatch, release, {}, loaded)
    reader._meaning = (release, None)
    reader._meaning_checked = float("-inf")
    retried: list[tuple[str, str]] = []
    monkeypatch.setattr(reader, "_load_in_background", retried.append)
    assert reader.meaning_index() is None
    assert retried == [release] and loaded == []


