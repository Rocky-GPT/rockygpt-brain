"""A graph-database copy of one published release, read through the shared fact reader.

Postgres stays the source of truth. A graph file holds one published release (one
dataset_version and identity_hash): the registry's entities and the evidence rows each office
links to, joined by edges. It hands those entities and rows to the shared reader unchanged, so
the graph never chooses, merges, or normalizes a value; the reader still resolves conflicts,
unknowns, and date boundaries. A file is built once per release, never edited, and opened
read-only, so a turn's release pins mean what they mean on Postgres.

Only entity kinds the reader serves get evidence (see BUILT_KINDS). Asking the graph for the
evidence of any other kind fails loudly instead of answering "none".

The copy is built in the background. Until it is ready, and whenever it cannot be built or read,
questions are answered straight from the source, so a graph problem never costs an answer.
"""

import glob
import hashlib
import importlib
import json
import logging
import math
import os
import secrets
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from datetime import UTC, date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from rockygpt_brain.retrieval.entity_facts import (
    EntityFacts,
    EvidenceUnavailable,
    Snapshot,
    validate_entities,
)

LOG = logging.getLogger(__name__)

GRAPH_FORMAT = "2"
BUILT_KINDS = ("office",)
CONTACTS = "contacts"
SCHEDULES = "campus_hours"
RETRY_SECONDS = 60.0  # After a failed build or read, answer from the source for this long.
STALE_BUILD_SECONDS = 3600.0  # An unfinished build file this old belongs to a process that died.
STRICT_WAIT_SECONDS = 20.0  # In strict mode a question waits this long for a build, then errors.
POINTER = "active.json"  # Names the release file a graph-only Brain serves (written last).
KEEP_RELEASES = 3  # Published release files kept in a directory, newest first.
FINGERPRINT_SECONDS = 30.0  # How often the copy is checked against in-place edits of the rows.
# The service has 512 MB: keep the engine's buffer pool, threads and file size small.
BUFFER_POOL_BYTES = 64 * 1024 * 1024
MAX_THREADS = 1
MAX_DB_BYTES = 1024 * 1024 * 1024

_SCHEMA = (
    "CREATE NODE TABLE Meta(key STRING, value STRING, PRIMARY KEY(key))",
    "CREATE NODE TABLE Entity(id STRING, kind STRING, name STRING, ord INT64, doc STRING,"
    " PRIMARY KEY(id))",
    "CREATE NODE TABLE Record(id STRING, collection STRING, doc STRING, PRIMARY KEY(id))",
    "CREATE REL TABLE HAS_RECORD(FROM Entity TO Record, collection STRING, ord INT64)",
)


class GraphUnavailable(Exception):
    """The release graph cannot be built or opened; the source adapter still can be read."""


def ladybug_module() -> Any:
    try:
        return importlib.import_module("ladybug")
    except ImportError as exc:
        raise GraphUnavailable(
            "LadybugDB is not installed; install the 'graph' extra.") from exc


def _database(lb: Any, path: Path, *, read_only: bool) -> Any:
    return lb.Database(
        str(path), read_only=read_only, buffer_pool_size=BUFFER_POOL_BYTES,
        max_num_threads=MAX_THREADS, max_db_size=MAX_DB_BYTES)


def _encode(value: Any) -> Any:
    """JSON that keeps datetimes, time zones and dates, so the reader gets its original types."""
    if isinstance(value, datetime):
        zone = value.tzinfo
        if zone is None or type(zone) is timezone:
            return {"$datetime": value.isoformat()}
        key = getattr(zone, "key", None)
        if isinstance(key, str):
            return {"$datetime": value.isoformat(), "tz": key}
        raise GraphUnavailable("Evidence has an unsupported time zone.")
    if isinstance(value, date):
        return {"$date": value.isoformat()}
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise GraphUnavailable("Evidence has a non-text key.")
        if "$datetime" in value or "$date" in value:
            raise GraphUnavailable("Evidence uses a reserved key.")
        return {key: _encode(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_encode(item) for item in value]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise GraphUnavailable("Evidence has a non-finite number.")
        return value
    raise GraphUnavailable(f"Evidence has an unsupported {type(value).__name__} value.")


def _decode(value: Any) -> Any:
    if isinstance(value, dict):
        if "$datetime" in value and set(value) <= {"$datetime", "tz"}:
            moment = datetime.fromisoformat(value["$datetime"])
            return moment.astimezone(ZoneInfo(value["tz"])) if "tz" in value else moment
        if set(value) == {"$date"}:
            return date.fromisoformat(value["$date"])
        return {key: _decode(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_decode(item) for item in value]
    return value


def _dumps(value: Any) -> str:
    """Documents keep the source's own key order, because the reader's output follows it."""
    return json.dumps(_encode(value), ensure_ascii=False, separators=(",", ":"))


def _canonical(value: Any) -> str:
    return json.dumps(_encode(value), ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _loads(text: str) -> Any:
    try:
        return _decode(json.loads(text))
    except (ValueError, TypeError, KeyError) as exc:
        raise EvidenceUnavailable("The release graph could not be read.") from exc


def _record_id(collection: str, row: dict[str, Any]) -> str:
    row_id = row.get("id")
    if isinstance(row_id, str) and row_id:
        return f"{collection}:{row_id}"
    return f"{collection}:sha256:{hashlib.sha256(_canonical(row).encode()).hexdigest()}"


def graph_path(directory: Path, dataset_version: str, identity_hash: str) -> Path:
    key = hashlib.sha256(f"{dataset_version}\n{identity_hash}".encode()).hexdigest()[:16]
    return directory / f"release-{key}.lbug"


def _remove(path: Path) -> None:
    """Delete a file and every side-file the engine made beside it (.wal, .shadow, .lock...)."""
    try:
        for candidate in (path, *path.parent.glob(f"{glob.escape(path.name)}.*")):
            candidate.unlink(missing_ok=True)
    except OSError:
        LOG.warning("brain_graph_remove_failed")


def build_release_graph(
    source: EntityFacts, directory: Path | str, *, kinds: tuple[str, ...] = BUILT_KINDS,
) -> Path:
    """Write the source's active release as one graph file and return its path.

    The evidence rows are exactly what the source adapter's own readers return for each entity,
    so the graph can hold nothing the reader would not have been given. The file is written
    beside its destination and renamed into place, so a reader never sees a half-built file.
    Reading the source can raise EvidenceUnavailable; every graph or file problem is raised as
    GraphUnavailable.
    """
    lb = ladybug_module()
    folder = Path(directory)
    inputs = source.release_inputs(kinds)
    started = time.monotonic()
    destination = graph_path(folder, inputs.dataset_version, inputs.identity_hash)
    # One name per attempt, so two processes (or two builds) never touch each other's files.
    building = destination.with_name(
        f"{destination.name}.{os.getpid()}-{secrets.token_hex(4)}.building")
    records: dict[str, tuple[str, str, str]] = {}  # id -> (collection, canonical text, stored text)
    edges: list[dict[str, Any]] = []
    for entity_id, (contacts, schedules) in inputs.evidence.items():
        for collection, rows in ((CONTACTS, contacts), (SCHEDULES, schedules)):
            for position, row in enumerate(rows):
                record_id = _record_id(collection, row)
                canonical = _canonical(row)
                if records.setdefault(record_id, (collection, canonical, _dumps(row)))[1] \
                        != canonical:
                    raise GraphUnavailable("Two different evidence rows share one id.")
                edges.append({"entity": entity_id, "record": record_id,
                              "collection": collection, "ord": position})
    meta = {
        "format": GRAPH_FORMAT, "dataset_version": inputs.dataset_version,
        "identity_hash": inputs.identity_hash, "fingerprint": inputs.fingerprint,
        "kinds": json.dumps(list(kinds)), "alias_sources": _dumps(inputs.alias_sources),
        "entity_count": str(len(inputs.entities)), "record_count": str(len(records)),
        "built_at": datetime.now(UTC).isoformat(),
    }
    try:
        folder.mkdir(parents=True, exist_ok=True)
        _remove(building)
        database = _database(lb, building, read_only=False)
        try:
            connection = lb.Connection(database)
            for statement in _SCHEMA:
                connection.execute(statement)
            # An empty batch is not a valid UNWIND, so each load runs only when it has rows.
            loads = (
                ("UNWIND $rows AS r CREATE (:Meta {key: r.key, value: r.value})",
                 [{"key": key, "value": value} for key, value in meta.items()]),
                ("UNWIND $rows AS r CREATE (:Entity {id: r.id, kind: r.kind, name: r.name,"
                 " ord: r.ord, doc: r.doc})",
                 [{"id": entity["id"], "kind": entity["kind"],
                   "name": str(entity.get("name", "")), "ord": position, "doc": _dumps(entity)}
                  for position, entity in enumerate(inputs.entities)]),
                ("UNWIND $rows AS r CREATE (:Record {id: r.id, collection: r.collection,"
                 " doc: r.doc})",
                 [{"id": record_id, "collection": collection, "doc": doc}
                  for record_id, (collection, _, doc) in records.items()]),
                ("UNWIND $rows AS e MATCH (a:Entity {id: e.entity}), (b:Record {id: e.record})"
                 " CREATE (a)-[:HAS_RECORD {collection: e.collection, ord: e.ord}]->(b)",
                 edges),
            )
            for statement, rows in loads:
                if rows:
                    connection.execute(statement, {"rows": rows})
            connection.execute("CHECKPOINT")
            connection.close()
        finally:
            database.close()
        if Path(f"{building}.wal").exists():
            raise GraphUnavailable("The release graph was not fully written.")
        os.replace(building, destination)
        _remove(building)  # Any side-file the engine left beside the finished build.
    except GraphUnavailable:
        _remove(building)
        raise
    except Exception as exc:  # Disk, permission and engine errors are all "no graph", not a crash.
        _remove(building)
        raise GraphUnavailable("The release graph could not be built.") from exc
    LOG.info("brain_graph_built entities=%d records=%d edges=%d ms=%d", len(inputs.entities),
             len(records), len(edges), int((time.monotonic() - started) * 1_000))
    return destination


class GraphEntityFacts(EntityFacts):
    """One built release, read-only. Its pins are the release it was built from."""

    backend = "graph"

    def __init__(self, path: Path | str, *, now: Callable[[], datetime] | None = None) -> None:
        super().__init__(now=now)
        self._lb = ladybug_module()
        self.path = Path(path)
        self.broken = False  # Set when a read fails, so the owner stops serving from this file.
        try:
            self._database = _database(self._lb, self.path, read_only=True)
            connection = self._lb.Connection(self._database)
            try:
                meta = {row[0]: row[1] for row in connection.execute(
                    "MATCH (m:Meta) RETURN m.key, m.value")}
            finally:
                connection.close()
        except Exception as exc:
            raise GraphUnavailable("The release graph could not be opened.") from exc
        if meta.get("format") != GRAPH_FORMAT:
            raise GraphUnavailable("The release graph has an unsupported format.")
        try:
            self.dataset_version: str = meta["dataset_version"]
            self.identity_hash: str = meta["identity_hash"]
            self.fingerprint: str = meta["fingerprint"]
            self.kinds: tuple[str, ...] = tuple(json.loads(meta["kinds"]))
            self._alias_sources = meta["alias_sources"]
        except (KeyError, ValueError) as exc:
            raise GraphUnavailable("The release graph is missing its release details.") from exc

    def _read(self, connection: Any, query: str, params: dict[str, Any] | None = None
              ) -> list[Any]:
        try:
            return [row[0] for row in connection.execute(query, params or {})]
        except Exception as exc:
            self.broken = True
            raise EvidenceUnavailable("The release graph could not be read.") from exc

    def _evidence(self, connection: Any, entity: dict[str, Any], collection: str
                  ) -> list[dict[str, Any]]:
        if entity["kind"] not in self.kinds:
            raise EvidenceUnavailable("This release graph holds no evidence for that kind.")
        docs = self._read(
            connection,
            "MATCH (e:Entity {id: $id})-[h:HAS_RECORD]->(r:Record)"
            " WHERE h.collection = $collection RETURN r.doc ORDER BY h.ord",
            {"id": entity["id"], "collection": collection})
        try:
            rows: list[dict[str, Any]] = [_loads(doc) for doc in docs]
        except EvidenceUnavailable:
            self.broken = True
            raise
        return rows

    @contextmanager
    def snapshot(self) -> Iterator[Snapshot]:
        try:
            connection = self._lb.Connection(self._database)
        except Exception as exc:
            raise EvidenceUnavailable("The release graph could not be read.") from exc
        try:
            docs = self._read(connection, "MATCH (e:Entity) RETURN e.doc ORDER BY e.ord")
            entities = validate_entities([_loads(doc) for doc in docs])
            yield Snapshot(
                self.dataset_version,
                self.identity_hash,
                entities,
                lambda entity: self._evidence(connection, entity, CONTACTS),
                _loads(self._alias_sources),
                lambda entity: self._evidence(connection, entity, SCHEDULES),
                lambda: self.fingerprint,
            )
        finally:
            connection.close()

    def active_release(self) -> tuple[str, str]:
        return self.dataset_version, self.identity_hash

    def evidence_fingerprint(self) -> str:
        return self.fingerprint


class ReleaseGraphFacts(EntityFacts):
    """Serve the source's active release from a graph file built in the background.

    The source stays the authority. Each read first asks the source which release is active.
    If that release's graph is ready it answers the question; otherwise the question is answered
    straight from the source while one background thread builds the graph, so no student waits
    for a build. A failed build or read puts the graph aside for RETRY_SECONDS. With fallback=False
    (strict mode) the source never answers a question: the graph serves it, or the question fails
    with "The release graph is unavailable." (the source is still read to learn the active release
    and to build the graph). The rows are
    re-checked against the source every FINGERPRINT_SECONDS, so an in-place edit of the active
    release shows up within that time. The directory belongs to one Brain process.
    """

    backend = "graph"

    def __init__(
        self, source: EntityFacts, directory: Path | str, *,
        kinds: tuple[str, ...] = BUILT_KINDS, fallback: bool = True,
        wait_seconds: float = STRICT_WAIT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__(now=now)
        ladybug_module()  # Fail at startup, not on the first question, if it is not installed.
        self.source = source
        self.directory = Path(directory)
        self.kinds = kinds
        self.fallback = fallback  # False is strict mode: the graph answers or the question errors.
        self.wait_seconds = wait_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._current: tuple[tuple[str, str], GraphEntityFacts] | None = None
        self._checked_at = float("-inf")
        self._building: threading.Thread | None = None
        self._build_running = False  # Set before the thread starts, so two never run at once.
        self._retry_at = float("-inf")
        self._served_graph = 0
        self._served_source = 0
        self._builds = 0
        self._last_build_ms: int | None = None
        self._last_error: str | None = None
        self._last_served = "source"

    @property
    def serving(self) -> str:
        if self._last_served == "graph":
            return "graph"
        return f"{self.source.backend} (graph not ready)"

    def stats(self) -> dict[str, Any]:
        with self._lock:
            building = self._build_running
            wait = self._retry_at - self._clock()
            return {
                "servedFromGraph": self._served_graph, "servedFromSource": self._served_source,
                "builds": self._builds, "lastBuildMs": self._last_build_ms,
                "lastError": self._last_error, "building": building,
                "strict": not self.fallback,
                "retryInSeconds": int(wait) if wait > 0 else 0,
                "release": self._current[0][0] if self._current else None,
            }

    def wait_for_build(self, timeout: float = 60.0) -> bool:
        """Block until a running build ends; True when a graph is ready. For tests and warm-up."""
        thread = self._building
        if thread is not None:
            thread.join(timeout)
        return self._current is not None

    def active_release(self) -> tuple[str, str]:
        return self.source.active_release()

    def evidence_fingerprint(self) -> str:
        return self.source.evidence_fingerprint()

    def _ready(self, release: tuple[str, str]) -> GraphEntityFacts | None:
        with self._lock:
            current = self._current
        if current is None or current[0] != release:
            return None
        if current[1].broken:  # A read from this file failed: stop serving from it.
            self._drop(current[1], failed=True)
            return None
        now = self._clock()
        if now - self._checked_at >= FINGERPRINT_SECONDS:
            self._checked_at = now
            if self.source.evidence_fingerprint() != current[1].fingerprint:
                LOG.info("brain_graph_rows_changed")
                self._drop(current[1])
                return None
        return current[1]

    def _drop(self, graph: GraphEntityFacts, *, failed: bool = False) -> None:
        with self._lock:
            if self._current is not None and self._current[1] is graph:
                self._current = None
            if failed:
                self._retry_at = self._clock() + RETRY_SECONDS

    def _begin_build(self, release: tuple[str, str]) -> threading.Thread | None:
        with self._lock:
            if self._build_running or self._clock() < self._retry_at:
                return None
            self._build_running = True
            thread = threading.Thread(target=self._build, args=(release,), daemon=True,
                                      name="brain-graph-build")
            self._building = thread
        try:
            thread.start()
        except RuntimeError:  # No thread could be started: answer from the source and retry later.
            with self._lock:
                self._build_running = False
                self._retry_at = self._clock() + RETRY_SECONDS
                self._last_error = "RuntimeError"
            return None
        return thread

    def _open_existing(self, release: tuple[str, str]) -> GraphEntityFacts | None:
        path = graph_path(self.directory, *release)
        if not path.exists():
            return None
        try:
            graph = GraphEntityFacts(path)
        except GraphUnavailable:
            LOG.warning("brain_graph_unreadable_file_rebuilt")
            return None
        if ((graph.dataset_version, graph.identity_hash) != release
                or not set(self.kinds) <= set(graph.kinds)
                or graph.fingerprint != self.source.evidence_fingerprint()):
            return None
        return graph

    def _build(self, release: tuple[str, str]) -> None:
        started = time.monotonic()
        try:
            graph = self._open_existing(release)
            if graph is None:
                graph = GraphEntityFacts(
                    build_release_graph(self.source, self.directory, kinds=self.kinds))
        except Exception as error:  # noqa: BLE001 - whatever went wrong, answers go on without it.
            with self._lock:
                self._retry_at = self._clock() + RETRY_SECONDS
                self._last_error = type(error.__cause__ or error).__name__
                self._build_running = False
            LOG.warning("brain_graph_build_failed exception_type=%s",
                        type(error.__cause__ or error).__name__)
            return
        with self._lock:
            self._current = (release, graph)
            self._checked_at = float("-inf")  # Check the rows against the source on first use.
            self._builds += 1
            self._last_build_ms = int((time.monotonic() - started) * 1_000)
            self._last_error = None
            self._build_running = False
        self._prune(graph.path)

    def _prune(self, keep: Path) -> None:
        """Delete other releases' files, and unfinished builds only once their process is gone."""
        try:
            now = time.time()
            for stale in self.directory.glob("release-*.lbug*"):
                if stale.name.startswith(keep.name):
                    continue
                if ".building" in stale.name and now - stale.stat().st_mtime < STALE_BUILD_SECONDS:
                    continue  # Another process may be building right now.
                stale.unlink(missing_ok=True)
        except OSError:
            LOG.warning("brain_graph_prune_failed")

    @contextmanager
    def snapshot(self) -> Iterator[Snapshot]:
        release = self.source.active_release()  # A source outage is an outage, not a graph issue.
        graph = self._ready(release)
        if graph is None:
            thread = self._begin_build(release)
            if not self.fallback:  # Strict: wait a bounded time, never answer from the source.
                thread = thread or self._building
                if thread is not None:
                    thread.join(self.wait_seconds)
                graph = self._ready(release)
                if graph is None:
                    raise EvidenceUnavailable("The release graph is unavailable.")
        stack = ExitStack()
        snapshot: Snapshot | None = None
        if graph is not None:
            try:
                snapshot = stack.enter_context(graph.snapshot())
                self._last_served = "graph"
                self._served_graph += 1
            except EvidenceUnavailable:
                LOG.warning("brain_graph_read_failed")
                self._drop(graph, failed=True)
                if not self.fallback:
                    raise
        if snapshot is None:
            snapshot = stack.enter_context(self.source.snapshot())
            self._last_served = "source"
            self._served_source += 1
        with stack:
            yield snapshot


def publish_release_graph(
    source: EntityFacts, directory: Path | str, *, kinds: tuple[str, ...] = BUILT_KINDS,
) -> Path:
    """Build the source's active release and make it the one a graph-only Brain serves.

    This is the publisher's job and the only step that reads the campus database. The pointer
    file is written after the graph is complete and renamed into place, so a Brain sees either
    the old release or the whole new one. The newest KEEP_RELEASES files are kept.
    """
    folder = Path(directory)
    built = build_release_graph(source, folder, kinds=kinds)
    graph = GraphEntityFacts(built)
    pointer = {
        "file": built.name, "dataset_version": graph.dataset_version,
        "identity_hash": graph.identity_hash, "fingerprint": graph.fingerprint,
        "kinds": list(graph.kinds), "published_at": datetime.now(UTC).isoformat(),
    }
    temp = folder / f"{POINTER}.{os.getpid()}-{secrets.token_hex(4)}.tmp"
    try:
        temp.write_text(json.dumps(pointer, indent=2) + "\n")
        os.replace(temp, folder / POINTER)
        files = sorted((f for f in folder.glob("release-*.lbug") if f.name != built.name),
                       key=lambda f: f.stat().st_mtime, reverse=True)
        for old in files[KEEP_RELEASES - 1:]:
            _remove(old)
    except OSError as exc:
        _remove(temp)
        raise GraphUnavailable("The release pointer could not be written.") from exc
    return built


class GraphOnlyFacts(EntityFacts):
    """Serve the release a publisher put in a directory, from the graph file alone.

    This adapter never connects to Postgres. Which release is active comes from the pointer file
    (POINTER), and every fact comes from the graph file it names. There is no fallback: a missing
    pointer, a missing or damaged file, or a failed read is an error for the question. The pointer
    is checked on every read, so a newly published release is served from the next question on.
    """

    backend = "graph (only)"

    def __init__(self, directory: Path | str, *, now: Callable[[], datetime] | None = None) -> None:
        super().__init__(now=now)
        ladybug_module()  # Fail at startup, not on the first question, if it is not installed.
        self.directory = Path(directory)
        self._lock = threading.Lock()
        self._loaded: tuple[tuple[int, int], GraphEntityFacts] | None = None
        self._served = 0
        self._loads = 0
        self._last_error: str | None = None

    @property
    def serving(self) -> str:
        return "graph (only)"

    def stats(self) -> dict[str, Any]:
        loaded = self._loaded
        return {
            "mode": "graph-only", "servedFromGraph": self._served, "loads": self._loads,
            "lastError": self._last_error, "directory": str(self.directory.name),
            "release": loaded[1].dataset_version if loaded else None,
            "file": loaded[1].path.name if loaded else None,
        }

    def _fail(self, error: BaseException) -> EvidenceUnavailable:
        self._loaded = None
        self._last_error = type(error.__cause__ or error).__name__
        LOG.warning("brain_graph_only_unavailable exception_type=%s", self._last_error)
        return EvidenceUnavailable("The release graph is unavailable.")

    def _graph(self) -> GraphEntityFacts:
        pointer_path = self.directory / POINTER
        try:
            status = pointer_path.stat()
        except OSError as exc:
            raise self._fail(exc) from exc
        key = (status.st_mtime_ns, status.st_size)
        loaded = self._loaded
        if loaded is not None and loaded[0] == key and not loaded[1].broken:
            return loaded[1]
        with self._lock:
            loaded = self._loaded
            if loaded is not None and loaded[0] == key and not loaded[1].broken:
                return loaded[1]
            try:
                pointer = json.loads(pointer_path.read_text())
                name = pointer["file"]
                if not isinstance(name, str) or Path(name).name != name:
                    raise GraphUnavailable("The release pointer names a path, not a file.")
                graph = GraphEntityFacts(self.directory / name)
                if (graph.dataset_version, graph.identity_hash) != (
                        pointer["dataset_version"], pointer["identity_hash"]):
                    raise GraphUnavailable("The release pointer and its file disagree.")
            except (OSError, ValueError, KeyError, TypeError, GraphUnavailable) as exc:
                raise self._fail(exc) from exc
            self._loaded = (key, graph)
            self._loads += 1
            self._last_error = None
            return graph

    def active_release(self) -> tuple[str, str]:
        graph = self._graph()
        return graph.dataset_version, graph.identity_hash

    def evidence_fingerprint(self) -> str:
        return self._graph().fingerprint

    @contextmanager
    def snapshot(self) -> Iterator[Snapshot]:
        graph = self._graph()
        with graph.snapshot() as snapshot:
            self._served += 1
            yield snapshot
