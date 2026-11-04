"""Startup warming: per-release reads are filled before the first question, never fatally."""

import asyncio
import logging
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
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
    assert set(seconds) == {"meaning_index", "procedures", "names", "ordinary_words",
                            "directory", "clubs", "events", "venues", "stops", "office_labels",
                            "catalog"}
    assert reader.warmed == 1
    assert engine._directory_cache is not None and engine._club_cache is not None
    assert getattr(engine, "_labels_cache", None) is not None
    assert reader._section_index is not None  # The office sections how-to questions search.
    listed = reader.offices_listed
    engine._offices_by_service("mailroom")  # Already read: only the cheap listing again.
    assert reader.offices_listed == listed + 1


class SlowClubs(Counting):
    """A reader whose club list takes a while and that counts how often it is read."""

    def __init__(self, fail_first: bool = False, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.club_lists = 0
        self.fail_first = fail_first

    def club_directory_ids(self) -> list[str]:
        self.club_lists += 1
        time.sleep(0.05)
        if self.fail_first and self.club_lists == 1:
            raise EvidenceUnavailable("down")
        return super().club_directory_ids()


def slow_clubs(**kwargs: Any) -> SlowClubs:
    base = facts()
    return SlowClubs(dataset_version=base.dataset_version, identity_hash=base.identity_hash,
                     entities=base.entities, contacts=base.contacts, now=base.now, **kwargs)


def test_questions_asking_at_once_wait_for_one_build() -> None:
    """After the 11-04 data release every question began its own copy of each list. Now one
    builds it and the rest wait for that copy."""
    reader = slow_clubs()
    engine = ChatEngine(ScriptedGateway(), reader)
    start = threading.Barrier(8)

    def ask() -> list[dict[str, Any]]:
        start.wait()
        return engine._club_directory()

    with ThreadPoolExecutor(8) as pool:
        lists = list(pool.map(lambda _: ask(), range(8)))
    assert reader.club_lists == 1
    assert all(found is lists[0] for found in lists)  # The one copy built, shared.


def test_a_failed_build_leaves_nothing_and_the_next_question_builds_it() -> None:
    reader = slow_clubs(fail_first=True)
    engine = ChatEngine(ScriptedGateway(), reader)
    with pytest.raises(EvidenceUnavailable):
        engine._club_directory()
    assert engine._club_cache is None
    assert engine._club_directory() is engine._club_directory()
    assert engine._club_cache is not None and reader.club_lists == 2


def test_how_to_sections_are_read_once_when_questions_ask_at_once() -> None:
    reader = counting()
    version, identity_hash = reader.active_release()
    opened: list[int] = []
    real = reader.snapshot

    @contextmanager
    def slow() -> Iterator[Any]:
        opened.append(1)
        time.sleep(0.05)
        with real() as snapshot:
            yield snapshot

    reader.snapshot = slow  # type: ignore[method-assign]
    start = threading.Barrier(8)

    def ask() -> object:
        start.wait()
        return reader._procedure_sections(version, identity_hash)

    with ThreadPoolExecutor(8) as pool:
        found = list(pool.map(lambda _: ask(), range(8)))
    assert len(opened) == 1 and all(sections is found[0] for sections in found)


def test_a_value_is_built_once_per_release() -> None:
    engine = ChatEngine(ScriptedGateway(), facts())
    built: list[str] = []

    def build(name: str) -> list[str]:
        built.append(name)
        return [name]

    first = engine._per_release("_club_cache", "release-1", lambda: build("first"))
    assert engine._per_release("_club_cache", "release-1", lambda: build("again")) is first
    assert engine._per_release("_club_cache", "release-2", lambda: build("next")) == ["next"]
    assert built == ["first", "next"]


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


