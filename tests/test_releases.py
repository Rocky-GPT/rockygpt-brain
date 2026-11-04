"""Moving to a new release only once it is ready (api/releases.py).

On 11-04 the follower installed the new release first and read its lists after, while students
asked; questions then built their own copies and fell to the slow AI path. These tests hold the
Brain to reading a new release in full in an engine of its own while the old one answers, and to
never installing or serving one that fails to open or check."""

import gc
import json
import weakref
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import rockygpt_brain.api.app as app_module
from rockygpt_brain.api.releases import Releases
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


def test_a_new_release_is_read_in_full_before_the_brain_switches_to_it(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bucket, brain = Bucket(), tmp_path / "brain"
    with bucket.client() as client:
        publish(STORE, build(tmp_path, "release-1"), client=client)
        sync(STORE, brain, client=client)
        state = serving(brain)
        old = state.engine
        publish(STORE, build(tmp_path, "release-2", office_name="Registrar 2"), client=client)
        releases = Releases(state, brain, GraphFollower(STORE, brain, client=client))
        seen: list[tuple[object, str]] = []
        warm = ChatEngine.warm

        def watched(engine: ChatEngine) -> dict[str, float]:
            pointer = read_pointer(brain)
            seen.append((state.engine, pointer["dataset_version"] if pointer else ""))
            return warm(engine)

        monkeypatch.setattr(ChatEngine, "warm", watched)
        assert releases.check_once()
        assert not releases.check_once()  # Nothing newer: nothing is read again.
    # While the new release was read, the old engine answered and the old release was installed.
    assert seen == [(old, "release-1")]
    new = state.engine
    assert new is not old and new.facts.release["dataset_version"] == "release-2"
    assert read_pointer(brain)["dataset_version"] == "release-2"  # type: ignore[index]
    assert new._directory_cache is not None and new._labels_cache is not None  # Read already.
    # A question the old engine is still answering finishes on the release it began on.
    assert old.facts.active_release() == ("release-1", "identities-1")


def test_a_release_that_fails_its_checks_is_never_installed_or_served(tmp_path: Path) -> None:
    bucket, brain = Bucket(), tmp_path / "brain"
    with bucket.client() as client:
        publish(STORE, build(tmp_path, "release-1"), client=client)
        sync(STORE, brain, client=client)
        state = serving(brain)
        old = state.engine
        publish(STORE, damaged(tmp_path, "release-2"), client=client)
        releases = Releases(state, brain, GraphFollower(STORE, brain, client=client))
        assert not releases.check_once()
        assert state.engine is old and releases.refused is not None
        assert read_pointer(brain)["dataset_version"] == "release-1"  # type: ignore[index]
        bucket.requests.clear()
        assert not releases.check_once()  # Refused: not downloaded or read again.
        assert bucket.requests == [("GET", "brain-graph/active.json")]
        publish(STORE, build(tmp_path, "release-3", office_name="Registrar 3"), client=client)
        assert releases.check_once()  # The next release is read as usual.
    assert state.engine.facts.release["dataset_version"] == "release-3"


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


def test_memory_is_given_back_once_the_replaced_engine_is_gone(
        monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    import rockygpt_brain.api.releases as releases_module

    class Engine:
        pass

    held = [Engine()]
    gone = weakref.ref(held[0])
    given: list[bool] = []
    monkeypatch.setattr(releases_module, "give_back_memory", lambda: given.append(gone() is None))
    releases = Releases(SimpleNamespace(engine=None), Path("unused"))
    releases.replaced = gone  # type: ignore[assignment]
    threading.Timer(0.2, held.clear).start()  # Its last question finishes a moment later.
    releases.give_back()
    assert given == [True]  # Given back once, after the replaced engine was gone.
    releases_module.give_back_memory()  # The real call never fails, with or without glibc.
