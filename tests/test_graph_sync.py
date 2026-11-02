"""The shared release graph: the data run puts one copy in the file store, and every graph-only
Brain (production's, the laptop's) downloads that same copy.

A fake S3 bucket stands in for the store; requests must carry a SigV4 Authorization header."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

import rockygpt_brain.api.app as app_module
from rockygpt_brain.retrieval import MemoryEntityFacts
from rockygpt_brain.retrieval.graph_store import GraphOnlyFacts, publish_release_graph
from rockygpt_brain.retrieval.graph_sync import (
    MARKER,
    POINTER,
    GraphFollower,
    GraphStore,
    SyncError,
    publish,
    sync,
)
from rockygpt_brain.settings import ConfigurationError
from test_graph_store import NOW, contact, office, operations


class Bucket:
    """An in-memory S3 bucket behind httpx.MockTransport."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.requests: list[tuple[str, str]] = []
        self.damage: set[str] = set()

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"].startswith("AWS4-HMAC-SHA256 Credential=key/")
        assert request.headers["x-amz-content-sha256"]
        assert request.url.path.startswith("/campus/")
        key = request.url.path.removeprefix("/campus/")
        self.requests.append((request.method, key))
        if request.method == "PUT":
            self.objects[key] = request.content
            return httpx.Response(200)
        if request.method == "DELETE":
            self.objects.pop(key, None)
            return httpx.Response(204)
        if key not in self.objects:
            return httpx.Response(404)
        body = self.objects[key]
        return httpx.Response(200, content=b"x" + body[1:] if key in self.damage else body)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))

    def names(self) -> set[str]:
        return {key.removeprefix("brain-graph/") for key in self.objects}


STORE = GraphStore(bucket="campus", access_key_id="key", secret_access_key="secret",  # noqa: S106
                   endpoint="https://store.example.com")


def release(version: str, office_name: str = "Registrar") -> MemoryEntityFacts:
    return MemoryEntityFacts(
        dataset_version=version, identity_hash="identities-1",
        entities=[office("registrar", office_name)], contacts=[contact("r1")], now=lambda: NOW)


def build(tmp_path: Path, version: str, office_name: str = "Registrar") -> Path:
    """A data-run output folder: one fresh release graph plus a fake meaning index beside it."""
    folder = tmp_path / f"built-{version}"
    built = publish_release_graph(release(version, office_name), folder)
    built.with_suffix(".meaning.json").write_text(json.dumps({"release": version}))
    built.with_suffix(".meaning.f32").write_bytes(b"\0" * 64)
    return folder


def test_a_brain_serves_the_exact_copy_the_data_run_shared(tmp_path: Path) -> None:
    bucket = Bucket()
    built = build(tmp_path, "release-1")
    with bucket.client() as client:
        publish(STORE, built, client=client)
        changed, message = sync(STORE, tmp_path / "brain", client=client)
    assert changed and "release-1" in message
    shared = json.loads((built / POINTER).read_text())
    for name in [shared["file"], *(p.name for p in built.glob("release-*.meaning.*"))]:
        assert (tmp_path / "brain" / name).read_bytes() == (built / name).read_bytes()
    pointer = json.loads((tmp_path / "brain" / POINTER).read_text())
    assert pointer["file"] == shared["file"] and len(pointer["files"]) == 3
    assert json.loads((tmp_path / "brain" / MARKER).read_text())["created_at"]
    # The same reader answers from the downloaded copy as from the data run's own folder.
    assert operations(GraphOnlyFacts(tmp_path / "brain", now=lambda: NOW)) == operations(
        GraphOnlyFacts(built, now=lambda: NOW))
    assert not list((tmp_path / "brain").glob(".*"))


def test_an_unchanged_release_is_not_downloaded_again(tmp_path: Path) -> None:
    bucket = Bucket()
    with bucket.client() as client:
        publish(STORE, build(tmp_path, "release-1"), client=client)
        sync(STORE, tmp_path / "brain", client=client)
        bucket.requests.clear()
        changed, message = sync(STORE, tmp_path / "brain", client=client)
    assert not changed and message.startswith("up to date")
    assert bucket.requests == [("GET", "brain-graph/active.json")]


def test_a_new_release_replaces_the_old_and_the_previous_one_is_kept(tmp_path: Path) -> None:
    bucket = Bucket()
    brain = tmp_path / "brain"
    with bucket.client() as client:
        for version in ("release-1", "release-2", "release-3"):
            publish(STORE, build(tmp_path, version, office_name=f"Registrar {version}"),
                    client=client)
            sync(STORE, brain, client=client)
    pointer = json.loads((brain / POINTER).read_text())
    assert pointer["dataset_version"] == "release-3"
    stems = {name.split(".")[0] for name in bucket.names() if name != POINTER}
    assert len(stems) == 2  # the store keeps this release and the one before it
    assert {p.name.split(".")[0] for p in brain.glob("release-*")} == stems
    facts = GraphOnlyFacts(brain, now=lambda: NOW)
    assert facts.active_release() == ("release-3", "identities-1")


def test_a_damaged_download_leaves_the_served_release_alone(tmp_path: Path) -> None:
    bucket = Bucket()
    brain = tmp_path / "brain"
    with bucket.client() as client:
        publish(STORE, build(tmp_path, "release-1"), client=client)
        sync(STORE, brain, client=client)
        before = (brain / POINTER).read_text()
        publish(STORE, build(tmp_path, "release-2", office_name="Bursar"), client=client)
        graph = json.loads(bucket.objects["brain-graph/active.json"])["file"]
        bucket.damage.add(f"brain-graph/{graph}")
        with pytest.raises(SyncError, match="damaged"):
            sync(STORE, brain, client=client)
    assert (brain / POINTER).read_text() == before
    assert not (brain / graph).exists() and not list(brain.glob(".*.part"))


def test_a_store_without_a_shared_release_is_an_error(tmp_path: Path) -> None:
    with Bucket().client() as client, pytest.raises(SyncError, match="no shared"):
        sync(STORE, tmp_path / "brain", client=client)


def test_the_follower_reports_a_failure_and_keeps_the_brain_on_its_copy(tmp_path: Path) -> None:
    follower = GraphFollower(STORE, tmp_path / "brain")
    follower.store = GraphStore(bucket="campus", access_key_id="key", secret_access_key="secret",  # noqa: S106
                                endpoint="http://127.0.0.1:9")
    assert follower.sync_once() is False
    assert follower.last_error is not None


def test_store_settings_come_from_the_environment() -> None:
    assert GraphStore.from_env({}) is None
    with pytest.raises(SyncError, match="access keys"):
        GraphStore.from_env({"BRAIN_GRAPH_STORE_BUCKET": "campus"})
    store = GraphStore.from_env({
        "BRAIN_GRAPH_STORE_BUCKET": "campus", "BRAIN_GRAPH_STORE_ACCESS_KEY_ID": "k",
        "BRAIN_GRAPH_STORE_SECRET_ACCESS_KEY": "s", "BRAIN_GRAPH_STORE_PREFIX": "graphs",
        "BRAIN_GRAPH_STORE_ENDPOINT": "https://acct.r2.cloudflarestorage.com/"})
    assert store is not None
    assert store.prefix == "graphs/" and store.region == "auto"
    url, host, path = store._url("active.json")
    assert url == "https://acct.r2.cloudflarestorage.com/campus/graphs/active.json"
    assert host == "acct.r2.cloudflarestorage.com" and path == "/campus/graphs/active.json"
    aws = GraphStore(bucket="campus", access_key_id="k", secret_access_key="s",  # noqa: S106
                     region="us-east-2")
    assert aws._url("active.json")[0] == (
        "https://campus.s3.us-east-2.amazonaws.com/brain-graph/active.json")


def test_only_a_brain_with_a_graph_folder_and_a_store_follows_the_store(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for key in ("BRAIN_GRAPH_ONLY_DIR", "BRAIN_GRAPH_STORE_BUCKET",
                "BRAIN_GRAPH_STORE_ACCESS_KEY_ID", "BRAIN_GRAPH_STORE_SECRET_ACCESS_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("BRAIN_GRAPH_ONLY_DIR", str(tmp_path))
    assert app_module._configured_follower() is None  # the laptop: its copy changes by hand
    monkeypatch.setenv("BRAIN_GRAPH_STORE_BUCKET", "campus")
    with pytest.raises(ConfigurationError):
        app_module._configured_follower()
    monkeypatch.setenv("BRAIN_GRAPH_STORE_ACCESS_KEY_ID", "k")
    monkeypatch.setenv("BRAIN_GRAPH_STORE_SECRET_ACCESS_KEY", "s")
    follower = app_module._configured_follower()
    assert follower is not None and follower.directory == tmp_path




def test_production_downloads_before_the_engine_starts_then_follows(
        monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    calls: list[str] = []

    class Engine:
        gateway = object()

        def warm(self) -> None:
            calls.append("warm")

    class Follower:
        def sync_once(self) -> bool:
            calls.append("sync")
            return True

        def start(self, on_new_release: Any) -> None:
            calls.append("follow")
            on_new_release()

        def stop(self) -> None:
            calls.append("stop")

    def engine() -> Engine:
        calls.append("engine")
        return Engine()

    monkeypatch.setattr(app_module, "_configured_follower", lambda: Follower())
    monkeypatch.setattr(app_module, "_configured_engine", engine)
    monkeypatch.setenv("BRAIN_WARM_AT_START", "0")
    with TestClient(app_module.create_app(environment="development")):
        pass
    assert calls == ["sync", "engine", "follow", "warm", "stop"]


def test_the_publish_script_needs_a_store(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
        capsys: pytest.CaptureFixture[str]) -> None:
    import publish_graph

    monkeypatch.delenv("BRAIN_GRAPH_STORE_BUCKET", raising=False)
    assert publish_graph.main(["--dir", str(tmp_path)]) == 1
    assert "BRAIN_GRAPH_STORE_BUCKET is not set" in capsys.readouterr().err
