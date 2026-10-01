"""Bounded, read-only reads of the data publisher's existing release schema."""

import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row

from rockygpt_brain.retrieval.entity_facts import (
    MAX_CONTACTS,
    EntityFacts,
    EvidenceUnavailable,
    Snapshot,
    validate_entities,
)

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

_CONTACTS = """
WITH links AS (
  SELECT * FROM jsonb_to_recordset(%s::jsonb) AS link(
    source_key text, source_record_keys jsonb, source_record_ids jsonb)
)
SELECT c.id::text AS id, c.source_record_key, c.name, c.department, c.email,
       c.phone, c.phones, c.office, c.offices, c.prefers_email, c.preferred_contact,
       c.contact_note, c.normalization_metadata, c.collected_at, c.valid_from,
       c.valid_until, c.content_hash, s.source_key, s.canonical_url,
       s.freshness_sla_hours
FROM rockygpt_v2.campus_contacts c
JOIN rockygpt_v2.sources s ON s.id = c.source_id
WHERE c.dataset_version_id = %s::uuid AND EXISTS (
  SELECT 1 FROM links l WHERE l.source_key = s.source_key
    AND l.source_record_keys ? c.source_record_key
    AND (l.source_record_ids IS NULL OR l.source_record_ids ? c.id::text)
)
ORDER BY c.id
LIMIT %s
"""


class PostgresEntityFacts(EntityFacts):
    """Each operation pins one repeatable-read snapshot; mutations are prohibited."""

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

    @contextmanager
    def snapshot(self) -> Iterator[Snapshot]:
        deadline = time.monotonic() + self._operation_timeout

        def remaining_ms() -> int:
            remaining = int((deadline - time.monotonic()) * 1_000)
            if remaining <= 0:
                raise EvidenceUnavailable("Published campus evidence exceeded its read deadline.")
            return min(self._statement_timeout, remaining)

        try:
            with psycopg.connect(
                self._database_url,
                connect_timeout=self._connect_timeout,
                row_factory=dict_row,
                tcp_user_timeout=int(self._operation_timeout * 1_000),
                keepalives_idle=3,
                keepalives_interval=1,
                keepalives_count=2,
                options=(
                    f"-c statement_timeout={self._statement_timeout} "
                    "-c default_transaction_read_only=on "
                    "-c idle_in_transaction_session_timeout=5000"
                ),
            ) as connection:
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

                yield Snapshot(
                    release["dataset_version"],
                    release["identity_hash"],
                    entities,
                    contacts,
                    release["alias_sources"] or [],
                )
        except psycopg.Error as exc:
            # Database URLs, passwords, row contents, and provider diagnostics are private.
            raise EvidenceUnavailable(
                "Published campus evidence is temporarily unavailable."
            ) from exc
