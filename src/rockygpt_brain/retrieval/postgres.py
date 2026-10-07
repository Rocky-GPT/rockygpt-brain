"""Bounded, read-only reads of the data publisher's existing release schema."""

import json
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row

from rockygpt_brain.retrieval.entity_facts import (
    MAX_CONTACTS,
    MAX_SCHEDULE_ROWS,
    EntityFacts,
    EvidenceUnavailable,
    ReleaseInputs,
    Snapshot,
    validate_entities,
)

# A derived copy is built from one read with its own, longer limits; questions keep short ones.
BUILD_SECONDS = 60.0
BUILD_STATEMENT_MS = 10_000

_RELEASE = """
SELECT v.id::text AS dataset_id, v.version AS dataset_version,
       a.content_hash AS identity_hash, a.payload AS registry,
       coverage.payload->'alias_sources' AS alias_sources
FROM rockygpt_v2.dataset_versions v
JOIN rockygpt_v2.release_artifacts a ON a.dataset_version_id = v.id
  AND a.artifact_key = 'campus-identities'
LEFT JOIN rockygpt_v2.release_artifacts coverage ON coverage.dataset_version_id = v.id
  AND coverage.artifact_key = 'campus-identity-coverage'
WHERE v.status = 'active' AND pg_column_size(a.payload) <= 16000000
  AND (coverage.payload IS NULL OR pg_column_size(coverage.payload) <= 16000000)
LIMIT 2
"""

# The same release as _RELEASE, without reading its registry: which publication is active.
_ACTIVE_RELEASE = """
SELECT v.version AS dataset_version, a.content_hash AS identity_hash
FROM rockygpt_v2.dataset_versions v
JOIN rockygpt_v2.release_artifacts a ON a.dataset_version_id = v.id
  AND a.artifact_key = 'campus-identities'
LEFT JOIN rockygpt_v2.release_artifacts coverage ON coverage.dataset_version_id = v.id
  AND coverage.artifact_key = 'campus-identity-coverage'
WHERE v.status = 'active' AND pg_column_size(a.payload) <= 16000000
  AND (coverage.payload IS NULL OR pg_column_size(coverage.payload) <= 16000000)
LIMIT 2
"""

# A digest of the evidence rows the readers return (whole contact and schedule rows of the release,
# and the source columns the readers use), so a copy can tell when rows were edited in place.
# One statement serves both a given release (inside a snapshot) and the active one.
_FINGERPRINT = """
SELECT md5(concat_ws('|',
  coalesce((SELECT md5(string_agg(to_jsonb(c)::text, ',' ORDER BY c.id))
            FROM rockygpt_v2.campus_contacts c WHERE c.dataset_version_id = v.id), ''),
  coalesce((SELECT md5(string_agg(to_jsonb(h)::text, ',' ORDER BY h.id))
            FROM rockygpt_v2.campus_hours h WHERE h.dataset_version_id = v.id), ''),
  coalesce((SELECT md5(string_agg(
              concat_ws(',', s.source_key, s.canonical_url, s.freshness_sla_hours), ';'
              ORDER BY s.source_key))
            FROM rockygpt_v2.sources s), ''),
  coalesce(coverage.content_hash, ''), coalesce(capture.content_hash, ''))) AS fingerprint
FROM rockygpt_v2.dataset_versions v
LEFT JOIN rockygpt_v2.release_artifacts coverage ON coverage.dataset_version_id = v.id
  AND coverage.artifact_key = 'campus-identity-coverage'
LEFT JOIN rockygpt_v2.release_artifacts capture ON capture.dataset_version_id = v.id
  AND capture.artifact_key = 'development-office-contact-evidence'
WHERE (%(dataset_id)s::uuid IS NOT NULL AND v.id = %(dataset_id)s::uuid)
   OR (%(dataset_id)s::uuid IS NULL AND v.status = 'active')
LIMIT 2
"""

_CONTACTS = """
WITH links AS (
  SELECT * FROM jsonb_to_recordset(%s::jsonb) AS link(
    source_key text, source_record_keys jsonb, source_record_ids jsonb)
)
SELECT c.id::text AS id, c.source_record_key, c.name, c.department, c.email,
       c.phone, c.phones, c.office, c.offices, c.prefers_email, c.preferred_contact,
       c.contact_note, c.normalization_metadata, c.collected_at, c.valid_from,
       c.valid_until, c.content_hash, s.source_key, s.canonical_url,
       s.freshness_sla_hours,
       contact_capture.content_hash AS contact_observation_artifact_hash
FROM rockygpt_v2.campus_contacts c
JOIN rockygpt_v2.sources s ON s.id = c.source_id
LEFT JOIN rockygpt_v2.release_artifacts contact_capture
  ON contact_capture.dataset_version_id = c.dataset_version_id
  AND contact_capture.artifact_key = 'development-office-contact-evidence'
WHERE c.dataset_version_id = %s::uuid AND EXISTS (
  SELECT 1 FROM links l WHERE l.source_key = s.source_key
    AND l.source_record_keys ? c.source_record_key
    AND (l.source_record_ids IS NULL OR l.source_record_ids ? c.id::text)
)
ORDER BY c.id
LIMIT %s
"""

# Optional columns are read through to_jsonb so a database that has not run a newer migration
# yet returns them empty instead of failing the whole read.
_SCHEDULES = """
WITH links AS (
  SELECT * FROM jsonb_to_recordset(%s::jsonb) AS link(source_key text, source_record_keys jsonb)
)
SELECT h.id::text AS id, h.source_record_key, h.name, h.day, h.schedule,
       to_jsonb(h) ->> 'notes' AS notes, to_jsonb(h) ->> 'source_url' AS source_url,
       to_jsonb(h) -> 'normalization_metadata' AS normalization_metadata,
       h.collected_at, (to_jsonb(h) ->> 'valid_from')::date AS valid_from,
       (to_jsonb(h) ->> 'valid_until')::date AS valid_until, h.content_hash,
       s.source_key, s.canonical_url, s.freshness_sla_hours
FROM rockygpt_v2.campus_hours h
JOIN rockygpt_v2.sources s ON s.id = h.source_id
WHERE h.dataset_version_id = %s::uuid AND EXISTS (
  SELECT 1 FROM links l WHERE l.source_key = s.source_key
    AND l.source_record_keys ? h.source_record_key
)
ORDER BY h.name, h.day, h.id
LIMIT %s
"""


class PostgresEntityFacts(EntityFacts):
    """Each operation pins one repeatable-read snapshot; mutations are prohibited."""

    backend = "postgres"

    def __init__(
        self,
        database_url: str,
        *,
        connect_timeout_seconds: int = 3,
        statement_timeout_ms: int = 2_000,
        operation_timeout_seconds: float = 4.0,
    ) -> None:
        super().__init__()
        if not database_url or not 1 <= connect_timeout_seconds <= 10:
            raise ValueError("A database URL and bounded connection timeout are required.")
        if not 100 <= statement_timeout_ms <= 10_000:
            raise ValueError("A bounded statement timeout is required.")
        if not 1 <= operation_timeout_seconds <= 10:
            raise ValueError("A bounded operation timeout is required.")
        self._database_url = database_url
        self._connect_timeout = connect_timeout_seconds
        self._statement_timeout = statement_timeout_ms
        self._operation_timeout = operation_timeout_seconds

    def _connect(self, operation_seconds: float,
                 statement_ms: int) -> psycopg.Connection[dict[str, Any]]:
        return psycopg.connect(
            self._database_url,
            connect_timeout=self._connect_timeout,
            row_factory=dict_row,
            tcp_user_timeout=int(operation_seconds * 1_000),
            keepalives_idle=3,
            keepalives_interval=1,
            keepalives_count=2,
            options=(
                f"-c statement_timeout={statement_ms} "
                "-c default_transaction_read_only=on "
                "-c idle_in_transaction_session_timeout=5000"
            ),
        )

    def active_release(self) -> tuple[str, str]:
        try:
            with self._connect(self._operation_timeout, self._statement_timeout) as connection:
                connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                rows = connection.execute(_ACTIVE_RELEASE).fetchall()
        except psycopg.Error as exc:
            raise EvidenceUnavailable(
                "Published campus evidence is temporarily unavailable."
            ) from exc
        if len(rows) != 1:
            raise EvidenceUnavailable("No unique active identity publication is available.")
        return str(rows[0]["dataset_version"]), str(rows[0]["identity_hash"])

    def evidence_fingerprint(self) -> str:
        try:
            with self._connect(self._operation_timeout, self._statement_timeout) as connection:
                connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                rows = connection.execute(_FINGERPRINT, {"dataset_id": None}).fetchall()
        except psycopg.Error as exc:
            raise EvidenceUnavailable(
                "Published campus evidence is temporarily unavailable."
            ) from exc
        if len(rows) != 1:
            raise EvidenceUnavailable("No unique active identity publication is available.")
        return str(rows[0]["fingerprint"])

    def release_inputs(self, kinds: Sequence[str]) -> ReleaseInputs:
        """One transaction with its own limits: the whole release for a derived copy."""
        with self._snapshot(BUILD_SECONDS, BUILD_STATEMENT_MS) as snapshot:
            evidence = {
                entity["id"]: (snapshot.contact_reader(entity), snapshot.schedule_reader(entity))
                for entity in snapshot.entities if entity["kind"] in kinds
            }
            return ReleaseInputs(
                snapshot.dataset_version, snapshot.identity_hash, list(snapshot.entities),
                list(snapshot.alias_sources), evidence, snapshot.fingerprint_reader())

    @contextmanager
    def snapshot(self) -> Iterator[Snapshot]:
        with self._snapshot(self._operation_timeout, self._statement_timeout) as snapshot:
            yield snapshot

    @contextmanager
    def _snapshot(self, operation_seconds: float, statement_ms: int) -> Iterator[Snapshot]:
        deadline = time.monotonic() + operation_seconds

        def remaining_ms() -> int:
            remaining = int((deadline - time.monotonic()) * 1_000)
            if remaining <= 0:
                raise EvidenceUnavailable("Published campus evidence exceeded its read deadline.")
            return min(statement_ms, remaining)

        try:
            with self._connect(operation_seconds, statement_ms) as connection:
                connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")

                def read(query: str, params: Any = None) -> list[dict[str, Any]]:
                    connection.execute(
                        "SELECT set_config('statement_timeout', %s, true)",
                        (f"{remaining_ms()}ms",),
                    )
                    remaining_ms()  # Configuring the session may itself consume the deadline.
                    return connection.execute(query, params).fetchall()

                rows = read(_RELEASE)
                if len(rows) != 1:
                    raise EvidenceUnavailable("No unique active identity publication is available.")
                release = rows[0]
                registry = release["registry"]
                if not isinstance(registry, dict) or registry.get("schema_version") != 1:
                    raise EvidenceUnavailable("Unsupported identity registry schema.")
                entities = validate_entities(registry.get("entities"))

                def contacts(entity: dict[str, Any]) -> list[dict[str, Any]]:
                    links = [
                        link for link in entity["links"] if link.get("collection") == "contacts"
                    ]
                    if not links:
                        return []
                    result = read(
                        _CONTACTS,
                        (json.dumps(links), release["dataset_id"], MAX_CONTACTS + 1),
                    )
                    if len(result) > MAX_CONTACTS:
                        raise EvidenceUnavailable("Office evidence exceeds the bounded read.")
                    return result

                def schedules(entity: dict[str, Any]) -> list[dict[str, Any]]:
                    links = [
                        link for link in entity["links"] if link.get("collection") == "campus_hours"
                    ]
                    if not links:
                        return []
                    result = read(
                        _SCHEDULES,
                        (json.dumps(links), release["dataset_id"], MAX_SCHEDULE_ROWS + 1),
                    )
                    if len(result) > MAX_SCHEDULE_ROWS:
                        raise EvidenceUnavailable(
                            "Office schedule evidence exceeds the bounded read.")
                    return result

                def fingerprint() -> str:
                    result = read(_FINGERPRINT, {"dataset_id": release["dataset_id"]})
                    if len(result) != 1:
                        raise EvidenceUnavailable("No unique active identity publication.")
                    return str(result[0]["fingerprint"])

                yield Snapshot(
                    release["dataset_version"],
                    release["identity_hash"],
                    entities,
                    contacts,
                    release["alias_sources"] or [],
                    schedules,
                    fingerprint,
                )
        except psycopg.Error as exc:
            # Database URLs, passwords, row contents, and provider diagnostics are private.
            raise EvidenceUnavailable(
                "Published campus evidence is temporarily unavailable."
            ) from exc
