"""A graph-database copy of one published release, read through the shared fact reader.

Postgres stays the source of truth. A graph file holds one published release (one
dataset_version and identity_hash): the registry's entities and the evidence rows each office
links to, joined by edges. It hands those entities and rows to the shared reader unchanged, so
the graph never chooses, merges, or normalizes a value; the reader still resolves conflicts,
unknowns, and date boundaries. A file is built once per release, never edited, and opened
read-only, so a turn's release pins mean what they mean on Postgres.

Only entity kinds the reader serves get evidence (see BUILT_KINDS). Asking the graph for the
evidence of any other kind fails loudly instead of answering "none".
"""

import hashlib
import importlib
import json
import logging
import math
import os
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from rockygpt_brain.retrieval.entity_facts import (
    EntityFacts,
    EvidenceUnavailable,
    Snapshot,
    validate_entities,
)

LOG = logging.getLogger(__name__)

GRAPH_FORMAT = "1"
BUILT_KINDS = ("office",)
CONTACTS = "contacts"
SCHEDULES = "campus_hours"
RETRY_SECONDS = 60.0  # After a failed build or open, serve from the source for this long.

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


def _encode(value: Any) -> Any:
    """JSON that keeps datetimes and dates distinct, so the reader sees the types it was given."""
    if isinstance(value, datetime):
        return {"$datetime": value.isoformat()}
    if isinstance(value, date):
        return {"$date": value.isoformat()}
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise GraphUnavailable("Evidence has a non-text key.")
        if len(value) == 1 and next(iter(value)) in ("$datetime", "$date"):
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
        if len(value) == 1:
            ((key, item),) = value.items()
            if key == "$datetime":
                return datetime.fromisoformat(item)
            if key == "$date":
                return date.fromisoformat(item)
        return {key: _decode(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_decode(item) for item in value]
    return value


def _dumps(value: Any) -> str:
    return json.dumps(_encode(value), ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _loads(text: str) -> Any:
    return _decode(json.loads(text))


def _record_id(collection: str, row: dict[str, Any], doc: str) -> str:
    row_id = row.get("id")
    if isinstance(row_id, str) and row_id:
        return f"{collection}:{row_id}"
    return f"{collection}:sha256:{hashlib.sha256(doc.encode()).hexdigest()}"


def graph_path(directory: Path, dataset_version: str, identity_hash: str) -> Path:
    key = hashlib.sha256(f"{dataset_version}\n{identity_hash}".encode()).hexdigest()[:16]
    return directory / f"release-{key}.lbug"


def _remove(path: Path) -> None:
    for candidate in (path, Path(f"{path}.wal")):
        candidate.unlink(missing_ok=True)


def build_release_graph(
    source: EntityFacts, directory: Path | str, *, kinds: tuple[str, ...] = BUILT_KINDS,
) -> Path:
    """Write the source's active release as one graph file and return its path.

    The evidence rows are exactly what the source adapter's own readers return for each entity,
    so the graph can hold nothing the reader would not have been given. The file is written
    beside its destination and renamed into place, so a reader never sees a half-built file.
    """
    lb = ladybug_module()
    folder = Path(directory)
    folder.mkdir(parents=True, exist_ok=True)
    with source.snapshot() as snapshot:
        version, identity_hash = snapshot.dataset_version, snapshot.identity_hash
        entities = list(snapshot.entities)
        alias_sources = list(snapshot.alias_sources)
        evidence = {
            entity["id"]: (snapshot.contact_reader(entity), snapshot.schedule_reader(entity))
            for entity in entities if entity["kind"] in kinds
        }
    started = time.monotonic()
    destination = graph_path(folder, version, identity_hash)
    building = destination.with_name(f"{destination.name}.building")
    _remove(building)
    records: dict[str, tuple[str, str]] = {}
    edges: list[dict[str, Any]] = []
    for entity_id, (contacts, schedules) in evidence.items():
        for collection, rows in ((CONTACTS, contacts), (SCHEDULES, schedules)):
            for position, row in enumerate(rows):
                doc = _dumps(row)
                record_id = _record_id(collection, row, doc)
                if records.setdefault(record_id, (collection, doc))[1] != doc:
                    raise GraphUnavailable("Two different evidence rows share one id.")
                edges.append({"entity": entity_id, "record": record_id,
                              "collection": collection, "ord": position})
    meta = {
        "format": GRAPH_FORMAT, "dataset_version": version, "identity_hash": identity_hash,
        "kinds": json.dumps(list(kinds)), "alias_sources": _dumps(alias_sources),
        "entity_count": str(len(entities)), "record_count": str(len(records)),
        "built_at": datetime.now(UTC).isoformat(),
    }
    try:
        database = lb.Database(str(building))
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
                  for position, entity in enumerate(entities)]),
                ("UNWIND $rows AS r CREATE (:Record {id: r.id, collection: r.collection,"
                 " doc: r.doc})",
                 [{"id": record_id, "collection": collection, "doc": doc}
                  for record_id, (collection, doc) in records.items()]),
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
    except GraphUnavailable:
        _remove(building)
        raise
    except Exception as exc:
        _remove(building)
        raise GraphUnavailable("The release graph could not be built.") from exc
    LOG.info("brain_graph_built entities=%d records=%d edges=%d ms=%d", len(entities),
             len(records), len(edges), int((time.monotonic() - started) * 1_000))
    return destination


class GraphEntityFacts(EntityFacts):
    """One built release, read-only. Its pins are the release it was built from."""

    backend = "graph"

    def __init__(self, path: Path | str, *, now: Callable[[], datetime] | None = None) -> None:
        super().__init__(now=now)
        self._lb = ladybug_module()
        self.path = Path(path)
        try:
            self._database = self._lb.Database(str(self.path), read_only=True)
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
            self.kinds: tuple[str, ...] = tuple(json.loads(meta["kinds"]))
            self._alias_sources = meta["alias_sources"]
        except (KeyError, ValueError) as exc:
            raise GraphUnavailable("The release graph is missing its release details.") from exc

    def _read(self, connection: Any, query: str, params: dict[str, Any] | None = None
              ) -> list[Any]:
        try:
            return [row[0] for row in connection.execute(query, params or {})]
        except Exception as exc:
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
        rows: list[dict[str, Any]] = [_loads(doc) for doc in docs]
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
            )
        finally:
            connection.close()

    def active_release(self) -> tuple[str, str]:
        return self.dataset_version, self.identity_hash


class ReleaseGraphFacts(EntityFacts):
    """Serve the source's active release from a graph file, building it on first use.

    The source stays the authority. Each read first asks the source which release is active,
    then reads that release's graph, building it once if it is not on disk. If the graph cannot
    be built or opened, reads fall back to the source for RETRY_SECONDS, so a graph problem never
    takes campus answers down. The directory belongs to one Brain process.
    """

    backend = "graph"

    def __init__(
        self, source: EntityFacts, directory: Path | str, *,
        kinds: tuple[str, ...] = BUILT_KINDS, fallback: bool = True,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__(now=now)
        ladybug_module()  # Fail at startup, not on the first question, if it is not installed.
        self.source = source
        self.directory = Path(directory)
        self.kinds = kinds
        self.fallback = fallback
        self._clock = clock
        self._lock = threading.Lock()
        self._current: tuple[tuple[str, str], GraphEntityFacts] | None = None
        self._retry_at = 0.0

    @property
    def serving(self) -> str:
        return "graph" if self._clock() >= self._retry_at else f"{self.source.backend} (fallback)"

    def active_release(self) -> tuple[str, str]:
        return self.source.active_release()

    def _open(self, path: Path, release: tuple[str, str]) -> GraphEntityFacts | None:
        if not path.exists():
            return None
        try:
            graph = GraphEntityFacts(path)
        except GraphUnavailable:
            LOG.warning("brain_graph_unreadable_file_rebuilt")
            return None
        if (graph.dataset_version, graph.identity_hash) != release or not set(
                self.kinds) <= set(graph.kinds):
            return None
        return graph

    def _graph(self) -> GraphEntityFacts:
        release = self.source.active_release()
        current = self._current
        if current is not None and current[0] == release:
            return current[1]
        with self._lock:
            current = self._current
            if current is not None and current[0] == release:
                return current[1]
            path = graph_path(self.directory, *release)
            graph = self._open(path, release)
            if graph is None:
                built = build_release_graph(self.source, self.directory, kinds=self.kinds)
                try:
                    graph = GraphEntityFacts(built)
                except GraphUnavailable:
                    _remove(built)
                    raise
            # The source may have moved on while the file was built; serve what was built.
            self._current = ((graph.dataset_version, graph.identity_hash), graph)
            self._prune(graph.path)
            return graph

    def _prune(self, keep: Path) -> None:
        try:
            for stale in self.directory.glob("release-*.lbug*"):
                if stale.name != keep.name:
                    stale.unlink(missing_ok=True)
        except OSError:
            LOG.warning("brain_graph_prune_failed")

    @contextmanager
    def snapshot(self) -> Iterator[Snapshot]:
        graph: GraphEntityFacts | None = None
        if self._clock() >= self._retry_at:
            try:
                graph = self._graph()
            except GraphUnavailable as error:
                if not self.fallback:
                    raise EvidenceUnavailable("The release graph is unavailable.") from error
                self._retry_at = self._clock() + RETRY_SECONDS
                LOG.warning("brain_graph_fallback exception_type=%s",
                            type(error.__cause__ or error).__name__)
        if graph is None:
            with self.source.snapshot() as snapshot:
                yield snapshot
            return
        with graph.snapshot() as snapshot:
            yield snapshot
