"""Moving to a new release only once it is ready (api/releases.py).

On 11-04 the follower installed the new release first and read its lists after, while students
asked; questions then built their own copies and fell to the slow AI path. On 11-17 reading the
new release beside the old one was killed for memory 4 times in 4 in a box the size of Render's.
These tests hold the Brain to pause and swap: new questions wait, the old engine is dropped, the
new release is read in full before anyone is answered from it, and one that fails to open or
check is never installed or served (the Brain goes back to the release it served)."""

import asyncio
import gc
import json
import threading
import time
import weakref
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import rockygpt_brain.api.app as app_module
import rockygpt_brain.api.releases as releases_module
from rockygpt_brain.api.releases import Gate, Releases
from rockygpt_brain.engine import ChatEngine
from rockygpt_brain.retrieval.graph_store import (
    POINTER,
    GraphOnlyFacts,
    GraphUnavailable,
    checked_release,
    read_pointer,
)
from rockygpt_brain.retrieval.graph_sync import GraphFollower, publish, sync
from test_engine import ScriptedGateway
from test_graph_sync import STORE, Bucket, build


def serving(brain: Path) -> SimpleNamespace:
    """The app's state with an engine on the release the folder has now."""
    facts = GraphOnlyFacts(brain, release=read_pointer(brain))
    return SimpleNamespace(engine=ChatEngine(ScriptedGateway(), facts))


def damaged(tmp_path: Path, version: str) -> Path:
    """A release published intact (its sha256 matches) whose graph file does not open."""
    folder = build(tmp_path, version, office_name="Bursar")
    graph = folder / json.loads((folder / POINTER).read_text())["file"]
    graph.write_bytes(b"this is not a graph")
    return folder


def test_a_new_release_is_read_in_full_with_the_old_engine_gone_and_questions_held(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bucket, brain = Bucket(), tmp_path / "brain"
    with bucket.client() as client:
        publish(STORE, build(tmp_path, "release-1"), client=client)
        sync(STORE, brain, client=client)
        state = serving(brain)
        old = weakref.ref(state.engine)
        publish(STORE, build(tmp_path, "release-2", office_name="Registrar 2"), client=client)
        releases = Releases(state, brain, GraphFollower(STORE, brain, client=client))
        seen: list[tuple[object, str, bool, bool]] = []
        warm = ChatEngine.warm

        def watched(engine: ChatEngine) -> dict[str, float]:
            pointer = read_pointer(brain)
            seen.append((state.engine, pointer["dataset_version"] if pointer else "",
                         state.gate.closed, old() is None))
            return warm(engine)

        monkeypatch.setattr(ChatEngine, "warm", watched)
        assert releases.check_once()
        assert not releases.check_once()  # Nothing newer: nothing is read again.
    # While the new release was read: no engine served, questions waited at the closed gate, the
    # old engine was already gone, and the old release was still the one installed.
    assert seen == [(None, "release-1", True, True)]
    assert not state.gate.closed
    new = state.engine
    assert new.facts.release["dataset_version"] == "release-2"
    assert read_pointer(brain)["dataset_version"] == "release-2"  # type: ignore[index]
    assert new._directory_cache is not None and new._labels_cache is not None  # Read already.


def test_a_release_that_fails_its_checks_is_never_installed_or_served(tmp_path: Path) -> None:
    bucket, brain = Bucket(), tmp_path / "brain"
    with bucket.client() as client:
        publish(STORE, build(tmp_path, "release-1"), client=client)
        sync(STORE, brain, client=client)
        state = serving(brain)
        publish(STORE, damaged(tmp_path, "release-2"), client=client)
        releases = Releases(state, brain, GraphFollower(STORE, brain, client=client))
        assert not releases.check_once()
        # Back on the release it served, read in full again, and the gate open.
        assert state.engine.facts.release["dataset_version"] == "release-1"
        assert state.engine._directory_cache is not None
        assert releases.refused is not None and not state.gate.closed
        assert read_pointer(brain)["dataset_version"] == "release-1"  # type: ignore[index]
        bucket.requests.clear()
        assert not releases.check_once()  # Refused: not downloaded or read again.
        assert bucket.requests == [("GET", "brain-graph/active.json")]
        publish(STORE, build(tmp_path, "release-3", office_name="Registrar 3"), client=client)
        assert releases.check_once()  # The next release is read as usual.
    assert state.engine.facts.release["dataset_version"] == "release-3"


def test_a_switch_waits_for_the_answers_in_flight_and_is_put_off_when_they_run_long(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bucket, brain = Bucket(), tmp_path / "brain"
    with bucket.client() as client:
        publish(STORE, build(tmp_path, "release-1"), client=client)
        sync(STORE, brain, client=client)
        state = serving(brain)
        publish(STORE, build(tmp_path, "release-2", office_name="Registrar 2"), client=client)
        releases = Releases(state, brain, GraphFollower(STORE, brain, client=client))
        monkeypatch.setattr(releases_module, "PAUSE_LIMIT_SECONDS", 0.2)
        with state.gate.passing_now() as passed:  # An answer still being written.
            assert passed
            assert not releases.check_once()
            first = state.engine
        # Put off: the same engine serves and new questions were let in again.
        assert first.facts.release["dataset_version"] == "release-1"
        assert not state.gate.closed
        del first

        def answer_for(seconds: float) -> None:
            with state.gate.passing_now():
                time.sleep(seconds)

        writing = threading.Thread(target=answer_for, args=(0.1,))
        writing.start()
        time.sleep(0.02)
        assert releases.check_once()  # It waited for that answer, then switched.
        writing.join()
    assert state.engine.facts.release["dataset_version"] == "release-2"


def test_questions_wait_at_a_closed_gate_and_the_emergency_floor_never_does() -> None:
    gate = Gate()

    async def scene() -> list[str]:
        order: list[str] = []
        assert gate.close(0.1)

        async def question() -> None:
            async with gate.passing():
                order.append("answered")

        asked = asyncio.create_task(question())
        await asyncio.sleep(0.2)
        with gate.passing_now() as passed:
            order.append(f"floor passed={passed}")
        order.append("switched")
        gate.open()
        await asked
        return order

    assert asyncio.run(scene()) == ["floor passed=False", "switched", "answered"]
    with gate.passing_now() as passed:
        assert passed
        assert not gate.close(0.05)  # Someone is past the gate: the close gives up, and opens.
    assert not gate.closed and gate.close(0.05)


def test_a_damaged_release_is_not_installed_before_the_first_question_either(
        tmp_path: Path) -> None:
    bucket, brain = Bucket(), tmp_path / "brain"
    with bucket.client() as client:
        publish(STORE, build(tmp_path, "release-1"), client=client)
        sync(STORE, brain, client=client)
        publish(STORE, damaged(tmp_path, "release-2"), client=client)
        releases = Releases(SimpleNamespace(engine=None), brain,
                            GraphFollower(STORE, brain, client=client))
        releases.first()  # A restart: the store's release is downloaded, then refused.
    assert read_pointer(brain)["dataset_version"] == "release-1"  # type: ignore[index]
    assert releases.refused is not None


def test_the_laptop_brain_moves_to_a_copy_synced_by_hand(tmp_path: Path) -> None:
    bucket, brain = Bucket(), tmp_path / "brain"
    with bucket.client() as client:
        publish(STORE, build(tmp_path, "release-1"), client=client)
        sync(STORE, brain, client=client)
        state = serving(brain)
        releases = Releases(state, brain)  # No store: the folder changes only by hand.
        assert not releases.check_once()
        publish(STORE, build(tmp_path, "release-2", office_name="Registrar 2"), client=client)
        sync(STORE, brain, force=True, client=client)  # What run-local.sh sync-prod does.
    assert releases.check_once()
    assert state.engine.facts.release["dataset_version"] == "release-2"
    assert not releases.check_once()


def test_a_reader_given_its_release_never_follows_the_pointer(tmp_path: Path) -> None:
    bucket, brain = Bucket(), tmp_path / "brain"
    with bucket.client() as client:
        publish(STORE, build(tmp_path, "release-1"), client=client)
        sync(STORE, brain, client=client)
        pinned = GraphOnlyFacts(brain, release=read_pointer(brain))
        following = GraphOnlyFacts(brain)
        publish(STORE, build(tmp_path, "release-2", office_name="Registrar 2"), client=client)
        sync(STORE, brain, client=client)
    assert pinned.active_release() == ("release-1", "identities-1")
    assert following.active_release() == ("release-2", "identities-1")


def test_a_release_whose_pointer_and_file_disagree_is_refused(tmp_path: Path) -> None:
    bucket, brain = Bucket(), tmp_path / "brain"
    with bucket.client() as client:
        publish(STORE, build(tmp_path, "release-1"), client=client)
        sync(STORE, brain, client=client)
    pointer = read_pointer(brain)
    assert pointer is not None
    assert checked_release(brain, pointer).release == pointer
    with pytest.raises(GraphUnavailable, match="does not open or pass its checks"):
        checked_release(brain, {**pointer, "identity_hash": "someone-else"})


def test_an_engine_for_another_release_keeps_every_setting_and_reads_nothing_yet(
        tmp_path: Path) -> None:
    bucket, brain = Bucket(), tmp_path / "brain"
    with bucket.client() as client:
        publish(STORE, build(tmp_path, "release-1"), client=client)
        sync(STORE, brain, client=client)
    engine = ChatEngine(ScriptedGateway(), GraphOnlyFacts(brain), output="written", reads=3,
                        picks=2, read_first=False, howto_one_call=True, lookup_writer=False,
                        turn_seconds=9.0, max_turn_nusd=7)
    engine.warm()
    facts = GraphOnlyFacts(brain, release=read_pointer(brain))
    other = engine.for_release(facts)

    def settings(e: ChatEngine) -> dict[str, Any]:
        return {k: v for k, v in vars(e).items() if not k.startswith("_") and k != "facts"}

    assert settings(other) == settings(engine) and other.gateway is engine.gateway
    assert other.facts is facts and other._directory_cache is None
    assert other._builders == {} and other._builders_lock is not engine._builders_lock


def test_production_downloads_and_checks_before_the_engine_starts_then_follows(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    calls: list[str] = []

    class Engine:
        gateway = object()

    class Fake:
        follower = object()

        def __init__(self, state: Any) -> None:
            self.state = state

        def first(self) -> None:
            calls.append("first")

        def start(self) -> None:
            calls.append("start")

        def stop(self) -> None:
            calls.append("stop")

    def engine() -> Engine:
        calls.append("engine")
        return Engine()

    monkeypatch.setattr(app_module, "_configured_releases", Fake)
    monkeypatch.setattr(app_module, "_configured_engine", engine)
    monkeypatch.setenv("BRAIN_WARM_AT_START", "0")
    with TestClient(app_module.create_app(environment="development")):
        pass
    assert calls == ["first", "engine", "start", "stop"]


def test_the_app_holds_no_engine_a_new_release_replaced(monkeypatch: pytest.MonkeyPatch) -> None:
    """The old engine and its release must be free to go once a new release's engine serves;
    holding it would keep two releases in memory for good (Render gives the Brain 512 MB)."""
    from fastapi.testclient import TestClient

    class Engine:
        gateway = object()

    first = Engine()
    gone = weakref.ref(first)

    class Fake:
        follower = None

        def __init__(self, state: Any) -> None:
            self.state = state

        def start(self) -> None:
            self.state.engine = Engine()  # A new release's engine replaces the first.

        def stop(self) -> None:
            pass

    made = [first]
    monkeypatch.setattr(app_module, "_configured_releases", Fake)
    monkeypatch.setattr(app_module, "_configured_engine", made.pop)
    monkeypatch.setenv("BRAIN_WARM_AT_START", "0")
    del first
    with TestClient(app_module.create_app(environment="development")):
        gc.collect()
        assert gone() is None


def test_an_engine_blueprint_holds_none_of_the_engine_or_its_release(tmp_path: Path) -> None:
    bucket, brain = Bucket(), tmp_path / "brain"
    with bucket.client() as client:
        publish(STORE, build(tmp_path, "release-1"), client=client)
        sync(STORE, brain, client=client)
    engine = ChatEngine(ScriptedGateway(), GraphOnlyFacts(brain), output="written", reads=3)
    gateway = engine.gateway
    make = engine.blueprint()
    gone = weakref.ref(engine)
    del engine
    gc.collect()
    assert gone() is None  # The switch can drop the serving engine and still build the next.
    made = make(GraphOnlyFacts(brain, release=read_pointer(brain)))
    assert made.gateway is gateway and made.output == "written" and made.reads == 3
