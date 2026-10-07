"""The release graph: a LadybugDB copy of one published release, read through the shared reader.

The graph may only hand the reader what the source adapter handed it. These tests hold it to that
by comparing every reader operation against the in-memory source, then cover the background
build, release changes, in-place edits, the failures, and the engine limits.
"""

import asyncio
import logging
import os
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, tzinfo
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import psycopg
import pytest

import rockygpt_brain.api.app as app_module
from rockygpt_brain.contract import ChatRequest
from rockygpt_brain.engine import ChatEngine
from rockygpt_brain.retrieval import (
    DatasetChanged,
    EntityFacts,
    EvidenceUnavailable,
    MemoryEntityFacts,
    PostgresEntityFacts,
    graph_store,
)
from rockygpt_brain.retrieval.entity_facts import ReleaseInputs, Snapshot
from rockygpt_brain.retrieval.graph_store import (
    GraphEntityFacts,
    GraphUnavailable,
    ReleaseGraphFacts,
    build_release_graph,
    graph_path,
)
from rockygpt_brain.retrieval.projection import OFFICE_FIELDS
from rockygpt_brain.turn import intake
from test_campus_graph import Script, call

pytest.importorskip("ladybug")

NOW = datetime(2026, 10, 6, 12, tzinfo=UTC)
VERSION, IDENTITY = "release-1", "identities-1"
DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def office(entity_id: str, name: str, *, aliases: list[str] | None = None,
           schedules: list[str] | None = None, kind: str = "office") -> dict[str, Any]:
    links: list[dict[str, Any]] = [{"collection": "contacts", "source_key": "directory",
                                    "source_record_keys": [f"office:{entity_id}"]}]
    if schedules:
        links.append({"collection": "campus_hours", "source_key": "campus-hours",
                      "source_record_keys": [f"{s}:{d}" for s in schedules for d in DAYS]})
    return {"id": entity_id, "kind": kind, "name": name, "aliases": aliases or [], "links": links}


def contact(record_id: str, entity_id: str = "registrar", **changes: Any) -> dict[str, Any]:
    return {
        "id": record_id, "source_key": "directory", "source_record_key": f"office:{entity_id}",
        "name": entity_id.title(), "email": "published@example.edu", "phone": "(201) 555-0100",
        "office": "D224", "collected_at": NOW, "freshness_sla_hours": 24,
        "canonical_url": "https://example.edu/directory", "content_hash": f"hash-{record_id}",
        **changes,
    }


def week(name: str, tag: str, text: str, *, since: Any = "2026-08-26", until: Any = "2026-12-16",
         ) -> list[dict[str, Any]]:
    return [{
        "id": f"{tag}-{name}-{day}", "source_key": "campus-hours",
        "source_record_key": f"{name}:{day}", "name": name, "day": day,
        "schedule": text if day not in DAYS[5:] else "Hours unavailable",
        "notes": f"{name} note", "source_url": "https://example.edu/hours/",
        "collected_at": NOW, "valid_from": since, "valid_until": until,
        "content_hash": f"hash-{tag}-{day}", "canonical_url": "https://example.edu/campus-hours/",
        "freshness_sla_hours": 4_320,
    } for day in DAYS]


def source(entities: list[dict[str, Any]], contacts: list[dict[str, Any]], *,
           schedules: list[dict[str, Any]] | None = None,
           aliases: list[dict[str, Any]] | None = None) -> MemoryEntityFacts:
    return MemoryEntityFacts(
        dataset_version=VERSION, identity_hash=IDENTITY, entities=entities, contacts=contacts,
        schedules=schedules, alias_sources=aliases, now=lambda: NOW)


def basic() -> MemoryEntityFacts:
    return source([office("registrar", "Registrar")], [contact("r1")])


def conflicts() -> MemoryEntityFacts:
    return source(
        [office("registrar", "Registrar"), office("bursar", "Bursar")],
        [contact("r1"), contact("r2"), contact("r3", email="PUBLISHED@example.edu"),
         contact("r4", phone=None, phones=["201-555-0101", "201-555-0102"],
                 valid_from=date(2026, 9, 1), valid_until=None,
                 normalization_metadata={"notes": ["a", {"nested": True}], "n": 1.5}),
         contact("b1", "bursar", email=None)])


def hours() -> MemoryEntityFacts:
    return source(
        [office("library", "Library", schedules=["Regular", "Summer"]),
         office("registrar", "Registrar")],
        [contact("l1", "library"), contact("r1")],
        schedules=[*week("Regular", "a", "8am-5pm"),
                   *week("Summer", "b", "9am-3pm", since=date(2026, 5, 20),
                         until=date(2026, 8, 25))])


def aliases() -> MemoryEntityFacts:
    entities = [office("a", "Student Records Office", aliases=["Student Services"]),
                office("b", "Student Success", aliases=["Student Services"]),
                office("p", "A Person", kind="person")]
    return source(entities, [contact("a1", "a"), contact("b1", "b"), contact("p1", "p")],
                  aliases=[{"entity_id": "a", "alias": "Student Services",
                            "sources": [{"basis": "department"}]}])


def absences() -> MemoryEntityFacts:
    check = {"url": "https://example.edu/nursing/", "section": "Contact Us",
             "checked_at": "2026-10-05T08:00:00+00:00"}
    claims = [{"field": "email", "checks": [check]}, {"field": "hours", "checks": [check]},
              {"field": "office", "checks": [check, {**check, "section": "Location"}]}]
    return source(
        [office("nursing", "Nursing"), office("library", "Library", schedules=["Regular"])],
        [contact("n1", "nursing", email=None, office=None,
                 normalization_metadata={"evidence": {"source_urls": [check["url"]],
                                                      "not_published": claims}}),
         contact("l1", "library")],
        schedules=week("Regular", "a", "8am-5pm"))


SCENARIOS: dict[str, Callable[[], MemoryEntityFacts]] = {
    "basic": basic, "conflicts": conflicts, "hours": hours, "aliases": aliases,
    "absences": absences}
QUERIES = ("Registrar", "student services", "library hours", "no such office anywhere")


def operations(service: EntityFacts) -> dict[str, Any]:
    result: dict[str, Any] = {"ready": service.readiness()}
    listing = service.list_offices(with_ids=True, limit=500)
    result["list"] = listing
    for query in QUERIES:
        result[f"search {query}"] = service.search_offices(query)
    for entry in listing["offices"]:
        for fields in (list(OFFICE_FIELDS), ["email"], ["hours"], ["name", "phones"]):
            for as_of in (NOW, NOW + timedelta(days=200)):
                key = f"{entry['entity_id']} {fields} {as_of.date()}"
                result[key] = service.get_office_facts(
                    entry["entity_id"], fields, VERSION, identity_hash=IDENTITY, as_of=as_of)
    return result


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    return tmp_path / "graphs"


def opened(memory: MemoryEntityFacts, folder: Path) -> GraphEntityFacts:
    return GraphEntityFacts(build_release_graph(memory, folder), now=lambda: NOW)


# ---- the graph file itself -------------------------------------------------------------------


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_the_graph_gives_the_reader_the_same_answers_as_its_source(
        name: str, folder: Path) -> None:
    memory = SCENARIOS[name]()
    expected = operations(memory)
    assert any(key.startswith(("registrar", "library", "a ")) for key in expected)
    assert operations(opened(memory, folder)) == expected


def test_a_confirmed_absence_survives_the_graph_with_its_proof() -> None:
    memory = absences()
    expected = memory.get_office_facts("nursing", ["email", "hours", "offices"], VERSION,
                                       identity_hash=IDENTITY, as_of=NOW)
    statuses = {p["key"]: p["status"] for p in expected["properties"]}
    assert statuses == dict.fromkeys(("email", "hours", "offices"), "not_published")
    absence = next(p for p in expected["properties"] if p["key"] == "offices")["absence"]
    assert [c["section"] for c in absence["checks"]] == ["Contact Us", "Location"]


def test_evidence_keeps_the_types_the_reader_was_given(folder: Path) -> None:
    memory = conflicts()
    with memory.snapshot() as expected, opened(memory, folder).snapshot() as actual:
        entity = next(e for e in expected.entities if e["id"] == "registrar")
        before, after = expected.contact_reader(entity), actual.contact_reader(entity)
    assert after == before
    typed = next(row for row in after if row["id"] == "r4")
    assert type(typed["collected_at"]) is datetime and type(typed["valid_from"]) is date
    assert typed["valid_until"] is None and typed["phones"] == ["201-555-0101", "201-555-0102"]


def test_evidence_keeps_its_key_order_and_the_alias_sources(folder: Path) -> None:
    row = contact("r1", normalization_metadata={"zebra": 1, "apple": {"yak": 1, "ant": 2}})
    memory = source([office("registrar", "Registrar")], [row],
                    aliases=[{"entity_id": "registrar", "alias": "Reg",
                              "sources": [{"basis": "department"}]}])
    with memory.snapshot() as expected, opened(memory, folder).snapshot() as actual:
        entity = expected.entities[0]
        before, after = expected.contact_reader(entity)[0], actual.contact_reader(entity)[0]
        assert actual.alias_sources == expected.alias_sources != []
    assert list(after) == list(before)
    assert list(after["normalization_metadata"]) == ["zebra", "apple"]
    assert list(after["normalization_metadata"]["apple"]) == ["yak", "ant"]


def test_a_time_zone_survives_so_freshness_arithmetic_matches_the_source(folder: Path) -> None:
    new_york = ZoneInfo("America/New_York")
    collected = datetime(2026, 11, 1, 0, 30, tzinfo=new_york)  # The night the clocks go back.
    memory = source([office("registrar", "Registrar")],
                    [contact("r1", collected_at=collected, freshness_sla_hours=4)])
    graph = opened(memory, folder)
    with graph.snapshot() as snapshot:
        row = snapshot.contact_reader(snapshot.entities[0])[0]
    assert getattr(row["collected_at"].tzinfo, "key", None) == "America/New_York"
    assert row["collected_at"] + timedelta(hours=4) == collected + timedelta(hours=4)
    as_of = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
    assert (graph.get_office_facts("registrar", ["email"], VERSION, as_of=as_of)
            == memory.get_office_facts("registrar", ["email"], VERSION, as_of=as_of))


def test_a_stale_release_pin_fails_like_the_source(folder: Path) -> None:
    graph = opened(basic(), folder)
    with pytest.raises(DatasetChanged):
        graph.get_office_facts("registrar", ["email"], "release-0", identity_hash=IDENTITY)
    with pytest.raises(DatasetChanged):
        graph.search_offices("Registrar", dataset_version=VERSION, identity_hash="other")


def test_evidence_for_a_kind_the_graph_did_not_build_fails_instead_of_saying_none(
        folder: Path) -> None:
    with opened(aliases(), folder).snapshot() as snapshot:
        person = next(e for e in snapshot.entities if e["kind"] == "person")
        with pytest.raises(EvidenceUnavailable, match="no evidence for that kind"):
            snapshot.contact_reader(person)
        assert [e["id"] for e in snapshot.entities] == ["a", "b", "p"]


class OddZone(tzinfo):
    def utcoffset(self, dt: datetime | None) -> timedelta:
        return timedelta(hours=1)

    def dst(self, dt: datetime | None) -> timedelta:
        return timedelta(0)

    def tzname(self, dt: datetime | None) -> str:
        return "odd"


def test_values_the_graph_cannot_keep_exactly_stop_the_build(folder: Path) -> None:
    odd = datetime(2026, 1, 1, tzinfo=OddZone())
    for bad in (contact("r1", email=Decimal("1.5")), contact("r1", email=("a", "b")),
                contact("r1", collected_at=odd), contact("r1", extra={"$date": "x"})):
        with pytest.raises(GraphUnavailable):
            build_release_graph(source([office("registrar", "Registrar")], [bad]), folder)
    clash = source([office("registrar", "Registrar")], [contact("r1"), contact("r1", email="x")])
    with pytest.raises(GraphUnavailable, match="share one id"):
        build_release_graph(clash, folder)
    assert not folder.exists() or list(folder.iterdir()) == []  # Nothing half-built is left.


def test_a_directory_that_cannot_be_written_is_a_graph_problem_not_a_crash(
        tmp_path: Path) -> None:
    blocker = tmp_path / "a-file"
    blocker.write_text("not a directory")
    with pytest.raises(GraphUnavailable, match="could not be built"):
        build_release_graph(basic(), blocker / "graphs")


def test_a_file_in_another_format_is_refused(folder: Path, monkeypatch: pytest.MonkeyPatch,
                                              ) -> None:
    path = build_release_graph(basic(), folder)
    monkeypatch.setattr(graph_store, "GRAPH_FORMAT", "0")
    with pytest.raises(GraphUnavailable, match="unsupported format"):
        GraphEntityFacts(path)


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permissions")
def test_a_built_file_opens_and_reads_from_a_read_only_folder(folder: Path) -> None:
    path = build_release_graph(basic(), folder)
    path.chmod(0o444)
    folder.chmod(0o555)
    try:
        graph = GraphEntityFacts(path, now=lambda: NOW)
        assert graph.get_office_facts("registrar", ["email"], VERSION)["evidence_count"] == 1
    finally:
        folder.chmod(0o755)


def test_the_engine_is_held_to_small_memory_and_one_thread(
        folder: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = graph_store.ladybug_module()
    seen: list[dict[str, Any]] = []

    class Recording:
        Connection = real.Connection

        @staticmethod
        def Database(path: str, **kwargs: Any) -> Any:  # noqa: N802 - mirrors the engine's API.
            seen.append(kwargs)
            return real.Database(path, **kwargs)

    monkeypatch.setattr(graph_store, "ladybug_module", lambda: Recording)
    opened(basic(), folder)
    assert [call["read_only"] for call in seen] == [False, True]  # Built, then opened.
    for kwargs in seen:
        assert kwargs["buffer_pool_size"] == graph_store.BUFFER_POOL_BYTES == 64 * 1024 * 1024
        assert kwargs["max_num_threads"] == 1
        assert kwargs["max_db_size"] == graph_store.MAX_DB_BYTES


# ---- the release graph that follows the source ----------------------------------------------


class Counting(MemoryEntityFacts):
    """A source that counts how often the graph reads it, and can be held up or broken."""

    def __init__(self, memory: MemoryEntityFacts) -> None:
        super().__init__(
            dataset_version=memory.dataset_version, identity_hash=memory.identity_hash,
            entities=memory.entities, contacts=memory.contacts, schedules=memory.schedules,
            alias_sources=memory.alias_sources, now=lambda: NOW)
        self.snapshots = 0
        self.builds = 0
        self.failure: Exception | None = None
        self.build_error: Exception | None = None
        self.gate: threading.Event | None = None
        self.fingerprint = "rows-1"

    @contextmanager
    def snapshot(self) -> Iterator[Snapshot]:
        self.snapshots += 1
        with super().snapshot() as snapshot:
            yield snapshot

    def active_release(self) -> tuple[str, str]:
        if self.failure is not None:
            raise self.failure
        return self.dataset_version, self.identity_hash

    def evidence_fingerprint(self) -> str:
        if self.failure is not None:
            raise self.failure
        return self.fingerprint

    def release_inputs(self, kinds: Any) -> ReleaseInputs:
        self.builds += 1
        if self.gate is not None:
            self.gate.wait(10)
        if self.build_error is not None:
            raise self.build_error
        return replace(super().release_inputs(kinds), fingerprint=self.fingerprint)


def until(condition: Callable[[], bool], seconds: float = 5.0) -> None:
    """Wait for a background thread to reach a point; fail the test if it never does."""
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "the background build never got there"
        time.sleep(0.005)


def email(service: EntityFacts, *, version: str = VERSION, identity: str = IDENTITY) -> str:
    result = service.get_office_facts("registrar", ["email"], version, identity_hash=identity)
    return str(result["properties"][0]["values"][0]["value"])


def follow(counting: Counting, folder: Path, **kwargs: Any) -> ReleaseGraphFacts:
    return ReleaseGraphFacts(counting, folder, now=lambda: NOW, **kwargs)


def test_the_first_question_is_answered_from_the_source_while_the_graph_builds(
        folder: Path) -> None:
    counting = Counting(basic())
    counting.gate = threading.Event()  # Hold the background build open.
    service = follow(counting, folder)
    assert email(service) == "published@example.edu"  # Nobody waits for the build.
    until(lambda: counting.builds == 1)
    assert service.stats()["building"] is True and service.serving == "memory (graph not ready)"
    for _ in range(8):
        assert email(service) == "published@example.edu"
    assert counting.builds == 1  # One build in flight, however many questions arrive.
    counting.gate.set()
    assert service.wait_for_build()
    assert email(service) == "published@example.edu" and service.serving == "graph"
    stats = service.stats()
    assert stats["servedFromSource"] == 9 and stats["servedFromGraph"] >= 1
    assert stats["builds"] == 1 and stats["lastError"] is None and stats["lastBuildMs"] is not None


def test_the_release_graph_is_built_once_and_then_reused(folder: Path) -> None:
    counting = Counting(basic())
    service = follow(counting, folder)
    email(service)
    assert service.wait_for_build()
    before = counting.snapshots
    for _ in range(3):
        assert email(service) == "published@example.edu"
    assert counting.snapshots == before and counting.builds == 1  # The source is not read again.
    again = follow(counting, folder)  # A restart reuses the file when nothing changed.
    email(again)
    assert again.wait_for_build() and counting.builds == 1
    assert [p.name for p in folder.iterdir()] == [graph_path(folder, VERSION, IDENTITY).name]


def test_simultaneous_questions_neither_wait_nor_start_a_second_build(folder: Path) -> None:
    counting = Counting(hours())
    service = follow(counting, folder)
    gate, answers, errors = threading.Barrier(8), [], []

    def ask() -> None:
        try:
            gate.wait()
            answers.append(email(service))
        except Exception as error:  # noqa: BLE001 - the test reports whatever went wrong.
            errors.append(error)

    threads = [threading.Thread(target=ask) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == [] and answers == ["published@example.edu"] * 8
    assert service.wait_for_build() and counting.builds == 1


def test_a_new_release_is_read_from_the_source_until_its_graph_is_ready(folder: Path) -> None:
    counting = Counting(basic())
    service = follow(counting, folder)
    email(service)
    assert service.wait_for_build()
    old_file = graph_path(folder, VERSION, IDENTITY)
    counting.dataset_version, counting.identity_hash = "release-2", "identities-2"
    counting.contacts[0]["email"] = "new@example.edu"
    counting.gate = threading.Event()
    assert email(service, version="release-2", identity="identities-2") == "new@example.edu"
    assert service.serving == "memory (graph not ready)"  # The old graph is never served.
    with pytest.raises(DatasetChanged):  # A turn pinned to the old release is told so.
        email(service)
    counting.gate.set()
    assert service.wait_for_build()
    assert email(service, version="release-2", identity="identities-2") == "new@example.edu"
    assert service.serving == "graph" and not old_file.exists()
    assert graph_path(folder, "release-2", "identities-2").exists()


def test_rows_edited_in_place_in_the_active_release_reach_the_answers(folder: Path) -> None:
    ticks = [0.0]
    counting = Counting(basic())
    service = follow(counting, folder, clock=lambda: ticks[0])
    email(service)
    assert service.wait_for_build() and email(service) == "published@example.edu"
    assert service.serving == "graph"
    counting.contacts[0]["email"] = "edited@example.edu"
    counting.fingerprint = "rows-2"  # The release name and hash are unchanged.
    assert email(service) == "published@example.edu"  # Not yet noticed: inside the check window.
    ticks[0] = graph_store.FINGERPRINT_SECONDS + 1
    assert email(service) == "edited@example.edu"  # Noticed: answered from the source.
    assert service.serving == "memory (graph not ready)"
    assert service.wait_for_build() and email(service) == "edited@example.edu"
    assert service.serving == "graph" and counting.builds == 2


@pytest.mark.parametrize("error", [
    EvidenceUnavailable("Published campus evidence exceeded its read deadline."),
    GraphUnavailable("simulated"), OSError("disk full"), RuntimeError("engine crashed")])
def test_a_failed_build_never_costs_an_answer_and_is_retried_later(
        folder: Path, error: Exception) -> None:
    ticks = [0.0]
    counting = Counting(basic())
    counting.build_error = error
    service = follow(counting, folder, clock=lambda: ticks[0])
    assert email(service) == "published@example.edu"
    assert not service.wait_for_build()
    assert service.stats()["lastError"] == type(error).__name__ and counting.builds == 1
    for _ in range(5):  # Inside the pause nobody retries.
        assert email(service) == "published@example.edu"
    assert counting.builds == 1 and service.stats()["retryInSeconds"] > 0
    ticks[0] = graph_store.RETRY_SECONDS + 1
    counting.build_error = None
    assert email(service) == "published@example.edu"
    assert service.wait_for_build() and counting.builds == 2
    assert service.stats()["lastError"] is None


def test_questions_queued_behind_a_failing_build_do_not_each_rebuild(folder: Path) -> None:
    counting = Counting(basic())
    counting.gate = threading.Event()
    counting.build_error = GraphUnavailable("simulated")
    service = follow(counting, folder)
    gate, errors = threading.Barrier(8), []

    def ask() -> None:
        try:
            gate.wait()
            email(service)
        except Exception as error:  # noqa: BLE001 - the test reports whatever went wrong.
            errors.append(error)

    threads = [threading.Thread(target=ask) for _ in range(8)]
    for thread in threads:
        thread.start()
    counting.gate.set()
    for thread in threads:
        thread.join()
    service.wait_for_build()
    assert errors == [] and counting.builds == 1


def test_without_fallback_the_question_waits_for_the_graph_or_fails(folder: Path) -> None:
    counting = Counting(basic())
    waiting = follow(counting, folder, fallback=False)
    assert email(waiting) == "published@example.edu" and waiting.serving == "graph"
    broken = Counting(basic())
    broken.build_error = GraphUnavailable("simulated")
    with pytest.raises(EvidenceUnavailable, match="graph is unavailable"):
        email(follow(broken, folder / "other", fallback=False))


def test_a_graph_that_cannot_be_read_hands_the_question_to_the_source(
        folder: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ticks = [0.0]
    counting = Counting(basic())
    service = follow(counting, folder, clock=lambda: ticks[0])
    email(service)
    assert service.wait_for_build()
    real = GraphEntityFacts.snapshot
    broken = {"on": True}

    @contextmanager
    def snapshot(self: GraphEntityFacts) -> Iterator[Snapshot]:
        if broken["on"]:
            raise EvidenceUnavailable("The release graph could not be read.")
        with real(self) as inner:
            yield inner

    monkeypatch.setattr(GraphEntityFacts, "snapshot", snapshot)
    assert email(service) == "published@example.edu"  # Answered by the source.
    assert service.serving == "memory (graph not ready)"
    broken["on"] = False
    ticks[0] = graph_store.RETRY_SECONDS + 1
    email(service)
    assert service.wait_for_build() and email(service) == "published@example.edu"
    assert service.serving == "graph"


def test_a_source_outage_is_not_hidden_by_the_graph(folder: Path) -> None:
    counting = Counting(basic())
    service = follow(counting, folder)
    email(service)
    assert service.wait_for_build()
    counting.failure = EvidenceUnavailable("Published campus evidence is temporarily unavailable.")
    with pytest.raises(EvidenceUnavailable, match="temporarily unavailable"):
        email(service)
    assert service.readiness() == {"ready": False}


def test_a_damaged_file_is_rebuilt_not_trusted(folder: Path) -> None:
    folder.mkdir(parents=True)
    graph_path(folder, VERSION, IDENTITY).write_bytes(b"this is not a graph")
    service = follow(Counting(basic()), folder)
    email(service)
    assert service.wait_for_build() and service.stats()["builds"] == 1
    assert operations(service) == operations(basic())


def test_a_file_built_for_other_kinds_is_rebuilt(folder: Path) -> None:
    build_release_graph(basic(), folder, kinds=())
    counting = Counting(basic())
    service = follow(counting, folder)
    email(service)
    assert service.wait_for_build() and counting.builds == 1


def test_a_chat_turn_through_the_release_graph_answers_like_one_through_the_source(
        folder: Path) -> None:
    request = ChatRequest.model_validate(
        {"messages": [{"role": "user", "content": "What is the Registrar email?"}],
         "omittedMessages": 0})

    def answer(facts: EntityFacts) -> dict[str, Any]:
        gateway = Script(
            call("graph_lookup", {"requests": [{"query": "Registrar", "fields": ["email"]}]}),
            call("finish", {"parts": []}))
        result = asyncio.run(ChatEngine(gateway, facts).answer(intake(request, now=NOW), request))
        assert result.status_code == 200 and result.trace is not None
        return {"status": result.body["status"], "answer": result.body["answer"],
                "citations": result.body["citations"], "path": result.trace["lookups"][0]["path"]}

    graph = follow(Counting(basic()), folder)
    during_build = answer(graph)  # Answered by the source while the graph builds.
    assert graph.wait_for_build()
    assert answer(graph) == during_build == answer(basic())
    assert graph.serving == "graph"
    runtime = ChatEngine(Script(), graph).runtime()
    assert runtime["factsBackend"] == "graph" and runtime["factsGraph"]["builds"] == 1
    plain = ChatEngine(Script(), basic()).runtime()
    assert plain["factsBackend"] == "memory" and plain["factsGraph"] is None


def test_a_failed_build_removes_every_side_file_the_engine_made(
        folder: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: Any, **kwargs: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", refuse)
    with pytest.raises(GraphUnavailable, match="could not be built"):
        build_release_graph(basic(), folder)
    assert list(folder.iterdir()) == []
    # A killed build leaves the engine's side-files; removing the build removes all of them.
    base = folder / "release-x.lbug.1-ab.building"
    leftovers = [base, *(folder / f"{base.name}.{ext}" for ext in (
        "wal", "wal.checkpoint", "shadow", "checkpoint.apply.lock", "checkpoint.intent.lock"))]
    neighbour = folder / "release-x.lbug.1-abc.building"  # Another attempt, not a side-file.
    for path in (*leftovers, neighbour):
        path.write_bytes(b"x")
    graph_store._remove(base)  # noqa: SLF001 - the clean-up under test.
    assert [p.name for p in folder.iterdir()] == [neighbour.name]


def test_two_builders_in_one_directory_do_not_destroy_each_other(folder: Path) -> None:
    first, second = Counting(basic()), Counting(basic())
    first.gate, second.gate = threading.Event(), threading.Event()
    one, two = follow(first, folder), follow(second, folder)
    email(one)
    email(two)
    until(lambda: first.builds == 1 and second.builds == 1)  # Both are building at once.
    first.gate.set()
    second.gate.set()
    assert one.wait_for_build() and two.wait_for_build()
    assert one.stats()["lastError"] is None and two.stats()["lastError"] is None
    for service in (one, two):
        assert email(service) == "published@example.edu" and service.serving == "graph"
    assert [p.name for p in folder.iterdir()] == [graph_path(folder, VERSION, IDENTITY).name]


def test_only_a_dead_processs_unfinished_build_is_cleaned_up(folder: Path) -> None:
    folder.mkdir(parents=True)
    young = folder / "release-other.lbug.111-aa.building"
    old = folder / "release-other.lbug.222-bb.building"
    other_release = folder / "release-older.lbug"
    for path in (young, old, other_release):
        path.write_bytes(b"x")
    long_ago = time.time() - graph_store.STALE_BUILD_SECONDS - 10
    os.utime(old, (long_ago, long_ago))
    service = follow(Counting(basic()), folder)
    email(service)
    assert service.wait_for_build()
    # Another process may be building right now, so its young file stays.
    assert sorted(p.name for p in folder.iterdir()) == sorted(
        [young.name, graph_path(folder, VERSION, IDENTITY).name])


def test_a_graph_that_fails_a_read_is_dropped_and_the_source_takes_over(
        folder: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ticks = [0.0]
    counting = Counting(basic())
    service = follow(counting, folder, clock=lambda: ticks[0])
    email(service)
    assert service.wait_for_build() and email(service) == "published@example.edu"
    real = graph_store._loads  # noqa: SLF001

    def unreadable(text: str) -> Any:
        if '"canonical_url"' in text:  # Only evidence rows carry it, never entities.
            raise EvidenceUnavailable("The release graph could not be read.")
        return real(text)

    monkeypatch.setattr(graph_store, "_loads", unreadable)
    with pytest.raises(EvidenceUnavailable, match="could not be read"):
        email(service)  # The question that hits the damage fails once...
    monkeypatch.undo()
    assert email(service) == "published@example.edu"  # ...and the next is answered by the source.
    assert service.serving == "memory (graph not ready)"
    assert service.stats()["retryInSeconds"] > 0  # The graph is set aside for a while.
    for _ in range(3):
        email(service)
    assert counting.builds == 1  # Nobody rebuilds inside the pause.
    ticks[0] = graph_store.RETRY_SECONDS + 1
    email(service)
    assert service.wait_for_build() and email(service) == "published@example.edu"
    assert service.serving == "graph"


def test_a_file_holding_another_release_than_its_name_says_is_rebuilt(folder: Path) -> None:
    other = Counting(basic())
    other.dataset_version, other.identity_hash = "release-9", "identities-9"
    build_release_graph(other, folder).rename(graph_path(folder, VERSION, IDENTITY))
    counting = Counting(basic())
    service = follow(counting, folder)
    email(service)
    assert service.wait_for_build() and counting.builds == 1  # Rebuilt, not trusted by its name.
    assert email(service) == "published@example.edu" and service.serving == "graph"


def test_a_graph_names_the_release_it_was_built_from(folder: Path) -> None:
    graph = opened(basic(), folder)
    assert graph.active_release() == (VERSION, IDENTITY)
    assert graph.evidence_fingerprint() == ""  # The in-memory source has no digest to keep.


def test_the_graph_keeps_the_registry_order_not_alphabetical_order(folder: Path) -> None:
    memory = source([office("zeta", "Zeta"), office("alpha", "Alpha")],
                    [contact("z1", "zeta"), contact("a1", "alpha")])
    with memory.snapshot() as expected, opened(memory, folder).snapshot() as actual:
        assert [e["id"] for e in actual.entities] == [e["id"] for e in expected.entities]
        assert [e["id"] for e in actual.entities] == ["zeta", "alpha"]


def test_a_graph_built_for_more_kinds_holds_their_evidence(folder: Path) -> None:
    memory = aliases()
    graph = GraphEntityFacts(
        build_release_graph(memory, folder, kinds=("office", "person")), now=lambda: NOW)
    with memory.snapshot() as expected, graph.snapshot() as actual:
        person = next(e for e in actual.entities if e["kind"] == "person")
        rows = actual.contact_reader(person)
        assert [row["id"] for row in rows] == ["p1"] == [
            row["id"] for row in expected.contact_reader(person)]


def test_a_damaged_entity_document_is_refused_not_read(folder: Path) -> None:
    path = build_release_graph(basic(), folder)
    lb = graph_store.ladybug_module()
    database = lb.Database(str(path))
    connection = lb.Connection(database)
    connection.execute("MATCH (e:Entity {id: 'registrar'}) SET e.doc = $doc",
                       {"doc": '{"id":"registrar","kind":"office"}'})  # The name is missing.
    connection.close()
    database.close()
    with pytest.raises(EvidenceUnavailable, match="Invalid canonical identity"):
        with GraphEntityFacts(path).snapshot():
            pass


def test_an_evidence_query_that_fails_puts_the_graph_aside(folder: Path) -> None:
    graph = opened(basic(), folder)
    real = graph._lb  # noqa: SLF001 - swapped for a flaky engine.

    class Flaky:
        def __init__(self, inner: Any) -> None:
            self.inner = inner

        def execute(self, query: str, params: Any = None) -> Any:
            if "HAS_RECORD" in query:
                raise RuntimeError("disk error")
            return self.inner.execute(query, params) if params else self.inner.execute(query)

        def close(self) -> None:
            self.inner.close()

    class Engine:
        @staticmethod
        def Connection(database: Any) -> Flaky:  # noqa: N802 - mirrors the engine's API.
            return Flaky(real.Connection(database))

    graph._lb = Engine  # noqa: SLF001
    assert graph.broken is False
    with pytest.raises(EvidenceUnavailable, match="could not be read"):
        graph.get_office_facts("registrar", ["email"], VERSION)
    assert graph.broken is True


def test_a_build_thread_that_cannot_start_leaves_the_answers_alone(
        folder: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def cannot(self: threading.Thread) -> None:
        raise RuntimeError("can't start new thread")

    counting = Counting(basic())
    service = follow(counting, folder)
    monkeypatch.setattr(threading.Thread, "start", cannot)
    assert email(service) == "published@example.edu"
    stats = service.stats()
    assert stats["building"] is False and stats["lastError"] == "RuntimeError"
    assert stats["retryInSeconds"] > 0 and counting.builds == 0


# ---- Postgres: the probes and the build read ------------------------------------------------


class FakeDatabase:
    """Stands in for psycopg.connect and records what is asked of it."""

    def __init__(self) -> None:
        self.queries: list[tuple[str, Any]] = []
        self.kwargs: dict[str, Any] = {}
        self.release_rows: list[dict[str, Any]] = [{"dataset_version": "r1", "identity_hash": "h1"}]
        self.fingerprint = "digest-1"
        entity = office("registrar", "Registrar", schedules=["Regular"])
        self.registry = {"dataset_id": "dataset-uuid", "dataset_version": "r1",
                         "identity_hash": "h1", "registry": {"schema_version": 1,
                                                             "entities": [entity]},
                         "alias_sources": []}

    def connect(self, *args: Any, **kwargs: Any) -> "FakeDatabase":
        self.kwargs = kwargs
        return self

    def __enter__(self) -> "FakeDatabase":
        return self

    def __exit__(self, *args: Any) -> None:
        pass

    def execute(self, query: str, params: Any = None) -> "FakeDatabase":
        self.queries.append((query, params))
        self._query = query
        return self

    def fetchall(self) -> list[dict[str, Any]]:
        query = self._query
        if "AS fingerprint" in query:
            return [{"fingerprint": self.fingerprint}]
        if "a.payload AS registry" in query:
            return [deepcopy(self.registry)]
        if "campus_contacts c" in query:
            return [contact("r1")]
        if "campus_hours h" in query:
            return deepcopy(week("Regular", "a", "8am-5pm"))
        if "AS dataset_version" in query:
            return deepcopy(self.release_rows)
        return []


def test_postgres_names_its_active_release_without_reading_the_registry(
        monkeypatch: pytest.MonkeyPatch) -> None:
    database = FakeDatabase()
    monkeypatch.setattr(psycopg, "connect", database.connect)
    service = PostgresEntityFacts("postgresql://example.invalid/test")
    assert service.active_release() == ("r1", "h1")
    assert database.queries[0][0] == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
    assert len(database.queries) == 2
    assert "registry" not in database.queries[1][0]
    assert "campus_contacts" not in database.queries[1][0]
    database.release_rows.append(dict(database.release_rows[0]))
    with pytest.raises(EvidenceUnavailable, match="No unique active"):
        service.active_release()

    def unavailable(*args: Any, **kwargs: Any) -> Any:
        raise psycopg.OperationalError("password=private database details")

    monkeypatch.setattr(psycopg, "connect", unavailable)
    with pytest.raises(EvidenceUnavailable, match="temporarily unavailable") as failure:
        service.active_release()
    assert "private" not in str(failure.value)


def test_postgres_digests_the_evidence_rows_for_the_active_release(
        monkeypatch: pytest.MonkeyPatch) -> None:
    database = FakeDatabase()
    monkeypatch.setattr(psycopg, "connect", database.connect)
    service = PostgresEntityFacts("postgresql://example.invalid/test")
    assert service.evidence_fingerprint() == "digest-1"
    query, params = database.queries[1]
    assert params == {"dataset_id": None}  # No release named: the active one.
    assert "campus_contacts" in query and "campus_hours" in query and "to_jsonb" in query


def test_the_build_reads_the_release_in_one_transaction_with_its_own_limits(
        monkeypatch: pytest.MonkeyPatch) -> None:
    database = FakeDatabase()
    monkeypatch.setattr(psycopg, "connect", database.connect)
    service = PostgresEntityFacts("postgresql://example.invalid/test")
    inputs = service.release_inputs(("office",))
    assert (inputs.dataset_version, inputs.identity_hash, inputs.fingerprint) == (
        "r1", "h1", "digest-1")
    contacts, schedules = inputs.evidence["registrar"]
    assert len(contacts) == 1 and len(schedules) == 7
    assert database.kwargs["tcp_user_timeout"] == 60_000  # Not the 4 s a question gets.
    assert "statement_timeout=10000" in database.kwargs["options"]
    assert database.queries[0][0] == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
    asked = [query for query, _ in database.queries if "AS fingerprint" not in query]
    assert sum("campus_contacts c" in q for q in asked) == 1  # One read per office and kind,
    assert sum("campus_hours h" in q for q in asked) == 1  # in one connection.
    assert sum("AS fingerprint" in query for query, _ in database.queries) == 1
    # A question keeps its short limits.
    database.queries.clear()
    service.get_office_facts("registrar", ["email"], "r1")
    assert database.kwargs["tcp_user_timeout"] == 4_000


# ---- start-up wiring -------------------------------------------------------------------------


def configured(monkeypatch: pytest.MonkeyPatch, graph_dir: str | None) -> ChatEngine:
    monkeypatch.setattr(app_module, "ProviderSettings",
                        SimpleNamespace(from_env=lambda: SimpleNamespace(max_turn_nusd=1)))
    monkeypatch.setattr(app_module, "Gateway", lambda settings: object())
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/test")
    if graph_dir is None:
        monkeypatch.delenv("BRAIN_GRAPH_DIR", raising=False)
    else:
        monkeypatch.setenv("BRAIN_GRAPH_DIR", graph_dir)
    return app_module._configured_engine()  # noqa: SLF001 - the wiring under test.


def test_the_graph_is_off_unless_a_directory_is_named(
        monkeypatch: pytest.MonkeyPatch, folder: Path) -> None:
    assert isinstance(configured(monkeypatch, None).facts, PostgresEntityFacts)
    wired = configured(monkeypatch, str(folder)).facts
    assert isinstance(wired, ReleaseGraphFacts) and isinstance(wired.source, PostgresEntityFacts)


def test_naming_a_directory_without_ladybug_keeps_answering_from_postgres(
        monkeypatch: pytest.MonkeyPatch, folder: Path, caplog: pytest.LogCaptureFixture) -> None:
    def missing() -> Any:
        raise GraphUnavailable("not installed")

    monkeypatch.setattr(graph_store, "ladybug_module", missing)
    with caplog.at_level(logging.ERROR):
        engine = configured(monkeypatch, str(folder))
    assert isinstance(engine.facts, PostgresEntityFacts)  # A graph problem is not an outage.
    assert "brain_graph_store_not_installed" in caplog.text
