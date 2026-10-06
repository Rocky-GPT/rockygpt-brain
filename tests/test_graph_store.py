"""The release graph: a LadybugDB copy of one published release, read through the shared reader.

The graph may only hand the reader what the source adapter handed it. These tests hold it to that
by comparing every reader operation against the in-memory source, then cover the build, the
release changes, and the failures.
"""

import asyncio
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

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
from rockygpt_brain.retrieval.entity_facts import Snapshot
from rockygpt_brain.retrieval.graph_store import (
    GraphEntityFacts,
    GraphUnavailable,
    ReleaseGraphFacts,
    build_release_graph,
    graph_path,
)
from rockygpt_brain.retrieval.projection import OFFICE_FIELDS
from rockygpt_brain.settings import ConfigurationError
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


SCENARIOS: dict[str, Callable[[], MemoryEntityFacts]] = {
    "basic": basic, "conflicts": conflicts, "hours": hours, "aliases": aliases}
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


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_the_graph_gives_the_reader_the_same_answers_as_its_source(
        name: str, folder: Path) -> None:
    memory = SCENARIOS[name]()
    expected = operations(memory)
    assert any(key.startswith(("registrar", "library", "a ")) for key in expected)
    assert operations(opened(memory, folder)) == expected


def test_evidence_keeps_the_types_the_reader_was_given(folder: Path) -> None:
    memory = conflicts()
    with memory.snapshot() as expected, opened(memory, folder).snapshot() as actual:
        entity = next(e for e in expected.entities if e["id"] == "registrar")
        before, after = expected.contact_reader(entity), actual.contact_reader(entity)
    assert after == before
    typed = next(row for row in after if row["id"] == "r4")
    assert type(typed["collected_at"]) is datetime and type(typed["valid_from"]) is date
    assert typed["valid_until"] is None and typed["phones"] == ["201-555-0101", "201-555-0102"]


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


def test_values_the_graph_cannot_keep_exactly_stop_the_build(folder: Path) -> None:
    for bad in (contact("r1", email=Decimal("1.5")), contact("r1", email=("a", "b"))):
        with pytest.raises(GraphUnavailable):
            build_release_graph(source([office("registrar", "Registrar")], [bad]), folder)
    clash = source([office("registrar", "Registrar")], [contact("r1"), contact("r1", email="x")])
    with pytest.raises(GraphUnavailable, match="share one id"):
        build_release_graph(clash, folder)
    assert list(folder.iterdir()) == []  # No half-built file is left behind.


def test_a_file_in_another_format_is_refused(folder: Path, monkeypatch: pytest.MonkeyPatch,
                                              ) -> None:
    path = build_release_graph(basic(), folder)
    monkeypatch.setattr(graph_store, "GRAPH_FORMAT", "2")
    with pytest.raises(GraphUnavailable, match="unsupported format"):
        GraphEntityFacts(path)


class Counting(MemoryEntityFacts):
    """A source that counts how often the graph has to read it."""

    def __init__(self, memory: MemoryEntityFacts) -> None:
        super().__init__(
            dataset_version=memory.dataset_version, identity_hash=memory.identity_hash,
            entities=memory.entities, contacts=memory.contacts, schedules=memory.schedules,
            alias_sources=memory.alias_sources, now=lambda: NOW)
        self.reads = 0
        self.failure: Exception | None = None

    @contextmanager
    def snapshot(self) -> Iterator[Snapshot]:
        self.reads += 1
        with super().snapshot() as snapshot:
            yield snapshot

    def active_release(self) -> tuple[str, str]:
        if self.failure is not None:
            raise self.failure
        return self.dataset_version, self.identity_hash


def email(service: EntityFacts, *, version: str = VERSION, identity: str = IDENTITY) -> str:
    result = service.get_office_facts("registrar", ["email"], version, identity_hash=identity)
    return str(result["properties"][0]["values"][0]["value"])


def test_the_release_graph_is_built_once_and_then_reused(folder: Path) -> None:
    counting = Counting(basic())
    service = ReleaseGraphFacts(counting, folder, now=lambda: NOW)
    assert service.serving == "graph" and service.backend == "graph"
    assert email(service) == "published@example.edu"
    assert counting.reads == 1  # The build read the source's release once.
    for _ in range(3):
        email(service)
    assert counting.reads == 1
    again = ReleaseGraphFacts(counting, folder, now=lambda: NOW)  # A restart reuses the file.
    assert email(again) == "published@example.edu" and counting.reads == 1
    assert [p.name for p in folder.iterdir()] == [graph_path(folder, VERSION, IDENTITY).name]


def test_eight_simultaneous_questions_build_the_graph_once(folder: Path) -> None:
    counting = Counting(hours())
    service = ReleaseGraphFacts(counting, folder, now=lambda: NOW)
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
    assert counting.reads == 1


def test_a_new_release_gets_its_own_graph_and_the_old_one_goes(folder: Path) -> None:
    counting = Counting(basic())
    service = ReleaseGraphFacts(counting, folder, now=lambda: NOW)
    assert email(service) == "published@example.edu"
    old_file = graph_path(folder, VERSION, IDENTITY)
    counting.dataset_version, counting.identity_hash = "release-2", "identities-2"
    counting.contacts[0]["email"] = "new@example.edu"
    assert email(service, version="release-2", identity="identities-2") == "new@example.edu"
    assert counting.reads == 2 and not old_file.exists()
    assert graph_path(folder, "release-2", "identities-2").exists()
    with pytest.raises(DatasetChanged):  # A turn pinned to the old release is told so.
        email(service)


def test_a_graph_problem_falls_back_to_the_source_and_retries_later(
        folder: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    builds = []

    def failing(*args: Any, **kwargs: Any) -> Path:
        builds.append(1)
        raise GraphUnavailable("simulated")

    monkeypatch.setattr(graph_store, "build_release_graph", failing)
    ticks = [0.0]
    service = ReleaseGraphFacts(Counting(basic()), folder, clock=lambda: ticks[0],
                                now=lambda: NOW)
    assert email(service) == "published@example.edu"  # Same answer, from the source.
    assert service.serving == "postgres (fallback)" or service.serving == "memory (fallback)"
    email(service)
    assert len(builds) == 1  # No new attempt inside the pause.
    ticks[0] = graph_store.RETRY_SECONDS + 1
    monkeypatch.undo()
    assert email(service) == "published@example.edu"
    assert service.serving == "graph" and graph_path(folder, VERSION, IDENTITY).exists()


def test_without_fallback_a_graph_problem_is_an_unavailable_answer(
        folder: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def failing(*args: Any, **kwargs: Any) -> Path:
        raise GraphUnavailable("simulated")

    monkeypatch.setattr(graph_store, "build_release_graph", failing)
    service = ReleaseGraphFacts(basic(), folder, fallback=False, now=lambda: NOW)
    with pytest.raises(EvidenceUnavailable, match="graph is unavailable"):
        email(service)


def test_a_source_outage_is_not_hidden_by_the_graph(folder: Path) -> None:
    counting = Counting(basic())
    service = ReleaseGraphFacts(counting, folder, now=lambda: NOW)
    email(service)
    counting.failure = EvidenceUnavailable("Published campus evidence is temporarily unavailable.")
    with pytest.raises(EvidenceUnavailable, match="temporarily unavailable"):
        email(service)
    assert service.readiness() == {"ready": False}


def test_a_damaged_file_is_rebuilt_not_trusted(folder: Path) -> None:
    folder.mkdir(parents=True)
    graph_path(folder, VERSION, IDENTITY).write_bytes(b"this is not a graph")
    service = ReleaseGraphFacts(Counting(basic()), folder, now=lambda: NOW)
    assert email(service) == "published@example.edu"
    assert operations(service) == operations(basic())


def test_a_file_built_for_other_kinds_is_rebuilt(folder: Path) -> None:
    build_release_graph(basic(), folder, kinds=())
    counting = Counting(basic())
    assert email(ReleaseGraphFacts(counting, folder, now=lambda: NOW)) == "published@example.edu"
    assert counting.reads == 1


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

    graph = ReleaseGraphFacts(basic(), folder, now=lambda: NOW)
    assert answer(graph) == answer(basic())
    assert ChatEngine(Script(), graph).runtime()["factsBackend"] == "graph"
    assert ChatEngine(Script(), basic()).runtime()["factsBackend"] == "memory"


def test_postgres_names_its_active_release_without_reading_the_registry(
        monkeypatch: pytest.MonkeyPatch) -> None:
    queries: list[str] = []
    rows: list[dict[str, Any]] = [{"dataset_version": "r1", "identity_hash": "h1"}]

    class Cursor:
        def fetchall(self) -> list[dict[str, Any]]:
            return deepcopy(rows)

    class Connection:
        def __enter__(self) -> "Connection":
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def execute(self, query: str, params: Any = None) -> Cursor:
            queries.append(query)
            return Cursor()

    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: Connection())
    service = PostgresEntityFacts("postgresql://example.invalid/test")
    assert service.active_release() == ("r1", "h1")
    assert queries[0] == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
    assert len(queries) == 2
    assert "registry" not in queries[1] and "campus_contacts" not in queries[1]
    rows.append(dict(rows[0]))
    with pytest.raises(EvidenceUnavailable, match="No unique active"):
        service.active_release()

    def unavailable(*args: Any, **kwargs: Any) -> Any:
        raise psycopg.OperationalError("password=private database details")

    monkeypatch.setattr(psycopg, "connect", unavailable)
    with pytest.raises(EvidenceUnavailable, match="temporarily unavailable") as failure:
        service.active_release()
    assert "private" not in str(failure.value)


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


def test_naming_a_directory_without_ladybug_stops_startup(
        monkeypatch: pytest.MonkeyPatch, folder: Path) -> None:
    def missing() -> Any:
        raise GraphUnavailable("not installed")

    monkeypatch.setattr(graph_store, "ladybug_module", missing)
    with pytest.raises(ConfigurationError, match="LadybugDB is missing"):
        configured(monkeypatch, str(folder))
