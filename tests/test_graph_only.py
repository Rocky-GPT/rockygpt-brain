"""A graph-only Brain: it serves a published release from the graph file and never uses Postgres.

The publisher (scripts/build_graph.py) is the only step that reads the campus database. These tests
hold the Brain side to that, then cover releases changing, a missing or damaged graph, and the
build script.
"""

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import build_graph
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
    graph_store,
)
from rockygpt_brain.retrieval.graph_store import (
    KEEP_RELEASES,
    POINTER,
    GraphOnlyFacts,
    GraphUnavailable,
    publish_release_graph,
)
from rockygpt_brain.settings import ConfigurationError
from rockygpt_brain.turn import intake
from test_campus_graph import Script, call
from test_graph_store import (
    IDENTITY,
    NOW,
    SCENARIOS,
    VERSION,
    Counting,
    basic,
    email,
    operations,
)


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    return tmp_path / "published"


def serve(memory: MemoryEntityFacts, folder: Path) -> GraphOnlyFacts:
    publish_release_graph(memory, folder)
    return GraphOnlyFacts(folder, now=lambda: NOW)


def never_postgres(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Any attempt to open a Postgres connection fails the test."""
    attempts: list[int] = []

    def refuse(*args: Any, **kwargs: Any) -> Any:
        attempts.append(1)
        raise AssertionError("The graph-only Brain tried to connect to Postgres.")

    monkeypatch.setattr(psycopg, "connect", refuse)
    return attempts


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_a_graph_only_brain_gives_the_reader_the_same_answers_as_the_published_source(
        name: str, folder: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    memory = SCENARIOS[name]()
    expected = operations(memory)
    service = serve(memory, folder)
    attempts = never_postgres(monkeypatch)
    assert operations(service) == expected
    assert attempts == []


def test_a_whole_chat_turn_is_answered_without_any_postgres_connection(
        folder: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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

    expected = answer(basic())
    service = serve(basic(), folder)
    attempts = never_postgres(monkeypatch)
    assert answer(service) == expected
    assert attempts == []
    runtime = ChatEngine(Script(), service).runtime()
    assert runtime["factsBackend"] == "graph (only)"
    assert runtime["factsGraph"]["mode"] == "graph-only" and runtime["factsGraph"]["loads"] == 1
    assert runtime["factsGraph"]["servedFromGraph"] >= 1


def test_a_new_release_is_served_from_the_next_question(folder: Path) -> None:
    counting = Counting(basic())
    service = serve(counting, folder)
    assert email(service) == "published@example.edu"
    counting.dataset_version, counting.identity_hash = "release-2", "identities-2"
    counting.contacts[0]["email"] = "new@example.edu"
    publish_release_graph(counting, folder)
    assert email(service, version="release-2", identity="identities-2") == "new@example.edu"
    with pytest.raises(DatasetChanged):  # A turn pinned to the old release is told so.
        email(service)
    assert service.stats()["loads"] == 2 and service.stats()["release"] == "release-2"


def test_publishing_keeps_the_newest_releases_and_leaves_no_temporary_files(folder: Path) -> None:
    counting = Counting(basic())
    for number in range(1, 6):
        counting.dataset_version = f"release-{number}"
        counting.identity_hash = f"identities-{number}"
        publish_release_graph(counting, folder)
        time.sleep(0.01)
    names = sorted(p.name for p in folder.iterdir())
    assert POINTER in names and len([n for n in names if n.endswith(".lbug")]) == KEEP_RELEASES
    assert not [n for n in names if n.endswith((".tmp", ".building", ".wal"))]
    pointer = json.loads((folder / POINTER).read_text())
    assert pointer["dataset_version"] == "release-5" and (folder / pointer["file"]).exists()


def test_nothing_published_is_an_error_not_a_fallback(folder: Path) -> None:
    folder.mkdir(parents=True)
    service = GraphOnlyFacts(folder, now=lambda: NOW)
    with pytest.raises(EvidenceUnavailable, match="release graph is unavailable"):
        email(service)
    assert service.readiness() == {"ready": False}
    assert service.stats()["lastError"] == "FileNotFoundError"


def pointer_of(folder: Path) -> dict[str, Any]:
    pointer: dict[str, Any] = json.loads((folder / POINTER).read_text())
    return pointer


@pytest.mark.parametrize("damage", ["garbage file", "pointer is not json", "pointer names a path",
                                    "pointer and file disagree", "file is missing"])
def test_a_damaged_publication_is_an_error_not_a_fallback(folder: Path, damage: str) -> None:
    publish_release_graph(basic(), folder)
    pointer = pointer_of(folder)
    if damage == "garbage file":
        (folder / pointer["file"]).write_bytes(b"this is not a graph")
    elif damage == "pointer is not json":
        (folder / POINTER).write_text("{not json")
    elif damage == "pointer names a path":
        # A perfectly good graph outside the directory must still not be served.
        (folder.parent / "escape.lbug").write_bytes((folder / pointer["file"]).read_bytes())
        (folder / POINTER).write_text(json.dumps({**pointer, "file": "../escape.lbug"}))
    elif damage == "pointer and file disagree":
        (folder / POINTER).write_text(json.dumps({**pointer, "identity_hash": "someone-else"}))
    else:
        (folder / pointer["file"]).unlink()
    service = GraphOnlyFacts(folder, now=lambda: NOW)
    with pytest.raises(EvidenceUnavailable, match="release graph is unavailable"):
        email(service)
    assert service.readiness() == {"ready": False}


def test_a_graph_that_fails_a_read_is_reopened_for_the_next_question(
        folder: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = serve(basic(), folder)
    assert email(service) == "published@example.edu"
    real = graph_store._loads  # noqa: SLF001

    def unreadable(text: str) -> Any:
        if '"canonical_url"' in text:
            raise EvidenceUnavailable("The release graph could not be read.")
        return real(text)

    monkeypatch.setattr(graph_store, "_loads", unreadable)
    with pytest.raises(EvidenceUnavailable, match="could not be read"):
        email(service)  # The question that hits the damage fails; nothing else answers it.
    monkeypatch.undo()
    assert email(service) == "published@example.edu"  # The file is opened afresh and works.
    assert service.stats()["loads"] == 2


def configured(monkeypatch: pytest.MonkeyPatch, folder: Path) -> ChatEngine:
    monkeypatch.setattr(app_module, "ProviderSettings",
                        type("S", (), {"from_env": staticmethod(
                            lambda: type("P", (), {"max_turn_nusd": 1})())}))
    monkeypatch.setattr(app_module, "Gateway", lambda settings: object())
    monkeypatch.delenv("DATABASE_URL", raising=False)  # The campus database is not even named.
    monkeypatch.delenv("BRAIN_GRAPH_DIR", raising=False)
    monkeypatch.setenv("BRAIN_GRAPH_ONLY_DIR", str(folder))
    return app_module._configured_engine()  # noqa: SLF001 - the wiring under test.


def test_the_brain_starts_graph_only_without_any_campus_database_setting(
        folder: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = never_postgres(monkeypatch)
    engine = configured(monkeypatch, folder)
    assert isinstance(engine.facts, GraphOnlyFacts) and attempts == []


def test_graph_only_without_ladybug_stops_startup(
        folder: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def missing() -> Any:
        raise GraphUnavailable("not installed")

    monkeypatch.setattr(graph_store, "ladybug_module", missing)
    with pytest.raises(ConfigurationError, match="LadybugDB is missing"):
        configured(monkeypatch, folder)


def test_the_build_script_publishes_the_active_release(
        folder: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    seen: list[str] = []

    def fake_source(url: str) -> MemoryEntityFacts:
        seen.append(url)
        return basic()

    monkeypatch.setattr(build_graph, "PostgresEntityFacts", fake_source)
    monkeypatch.setenv("DATABASE_URL", "host=127.0.0.1 port=55434 dbname=old user=reader")
    assert build_graph.main(["--out", str(folder), "--dbname", "newer"]) == 0
    assert "dbname=newer" in seen[0] and "old" not in seen[0]
    assert pointer_of(folder)["dataset_version"] == VERSION
    out = capsys.readouterr().out
    assert VERSION in out and IDENTITY[:12] in out and "reader" not in out  # No connection details.


def test_the_build_script_reports_failures_without_a_traceback(
        folder: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert build_graph.main(["--out", str(folder)]) == 2
    monkeypatch.setenv("DATABASE_URL", "host=127.0.0.1 dbname=x")

    def down(*args: Any, **kwargs: Any) -> Any:
        raise EvidenceUnavailable("Published campus evidence is temporarily unavailable.")

    monkeypatch.setattr(build_graph, "publish_release_graph", down)
    assert build_graph.main(["--out", str(folder)]) == 1
    assert "temporarily unavailable" in capsys.readouterr().err
    assert os.listdir(folder) == [] if folder.exists() else True
