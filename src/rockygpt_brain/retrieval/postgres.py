"""Bounded, read-only reads of the data publisher's existing release schema."""

import json
import logging
import os
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager, suppress
from threading import Lock
from typing import Any
from urllib.parse import quote

import certifi
import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from rockygpt_brain.retrieval.entity_facts import (
    MAX_CONTACTS,
    MAX_SCHEDULE_ROWS,
    RECORD_COLLECTIONS,
    EntityFacts,
    EvidenceUnavailable,
    ReleaseInputs,
    Snapshot,
    linked_records,
    validate_entities,
)

LOG = logging.getLogger(__name__)

# A derived copy is built from one read with its own, longer limits; questions keep short ones.
BUILD_SECONDS = 60.0
BUILD_STATEMENT_MS = 10_000
# Opening a connection to a remote database (TCP, TLS, then the pooler's sign-in) took about a
# second from Render to Neon, and one question reads the campus data about ten times. Question
# reads reuse their connections, as the ledger's pool does; idle ones are dropped long before the
# server would drop them, and one the server closed anyway is replaced on its first statement.
IDLE_SECONDS = 120.0
MAX_IDLE_CONNECTIONS = 4
_idle_clock = time.monotonic  # Its own clock: tests replace `time` to move read deadlines.

# The registry itself (2.4 MB of JSON, 3,686 entities on 10-22) is read once per publication by
# _REGISTRY and kept: every question reads the release about ten times, and its hash names it.
_RELEASE = """
SELECT v.id::text AS dataset_id, v.version AS dataset_version,
       a.content_hash AS identity_hash,
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

# As text: each snapshot decodes its own copy, so no caller can change the kept registry.
_REGISTRY = """
SELECT a.payload::text AS registry_text
FROM rockygpt_v2.release_artifacts a
WHERE a.dataset_version_id = %s::uuid AND a.artifact_key = 'campus-identities'
  AND a.content_hash = %s AND pg_column_size(a.payload) <= 16000000
"""

# Which publication is active, without its alias sources.
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
  coalesce((SELECT md5(string_agg(to_jsonb(d)::text, ',' ORDER BY d.id))
            FROM rockygpt_v2.dining_hours d WHERE d.dataset_version_id = v.id), ''),
  coalesce((SELECT md5(string_agg(to_jsonb(m)::text, ',' ORDER BY m.id))
            FROM rockygpt_v2.menu_items m WHERE m.dataset_version_id = v.id), ''),
  coalesce((SELECT md5(string_agg(to_jsonb(t)::text, ',' ORDER BY t.id))
            FROM rockygpt_v2.shuttle_trips t WHERE t.dataset_version_id = v.id), ''),
  coalesce((SELECT md5(string_agg(to_jsonb(r)::text, ',' ORDER BY r.id))
            FROM rockygpt_v2.shuttle_routes r WHERE r.dataset_version_id = v.id), ''),
  coalesce((SELECT md5(string_agg(
              concat_ws(',', s.source_key, s.canonical_url, s.freshness_sla_hours), ';'
              ORDER BY s.source_key))
            FROM rockygpt_v2.sources s), ''),
  coalesce((SELECT md5(string_agg(to_jsonb(p)::text, ',' ORDER BY p.id))
            FROM rockygpt_v2.programs p WHERE p.dataset_version_id = v.id), ''),
  coalesce((SELECT string_agg(a.content_hash, ',' ORDER BY a.artifact_key)
            FROM rockygpt_v2.release_artifacts a WHERE a.dataset_version_id = v.id
              AND a.artifact_key IN ('programs', 'program-requirement-groups', 'courses',
                                     'catalog-course-identities', 'graduation-plans',
                                     'events', 'clubs')), ''),
  coalesce((SELECT md5(string_agg(to_jsonb(e)::text, ',' ORDER BY e.id))
            FROM rockygpt_v2.campus_events e WHERE e.dataset_version_id = v.id), ''),
  coalesce((SELECT md5(string_agg(to_jsonb(c)::text, ',' ORDER BY c.id))
            FROM rockygpt_v2.clubs c WHERE c.dataset_version_id = v.id), ''),
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


_DINING = """
WITH links AS (
  SELECT * FROM jsonb_to_recordset(%s::jsonb) AS link(source_key text, source_record_keys jsonb)
)
SELECT d.id::text AS id, d.source_record_key, d.name, d.day, d.schedule, d.collected_at,
       d.valid_from, d.valid_until, d.content_hash,
       s.source_key, s.canonical_url, s.freshness_sla_hours
FROM rockygpt_v2.dining_hours d
JOIN rockygpt_v2.sources s ON s.id = d.source_id
WHERE d.dataset_version_id = %s::uuid AND EXISTS (
  SELECT 1 FROM links l WHERE l.source_key = s.source_key
    AND l.source_record_keys ? d.source_record_key
)
ORDER BY d.name, d.day, d.id
LIMIT %s
"""

_MENU = """
WITH links AS (
  SELECT * FROM jsonb_to_recordset(%s::jsonb) AS link(source_key text, source_record_keys jsonb)
)
SELECT m.id::text AS id, m.source_record_key, m.meal, m.station, m.name, m.calories,
       m.portion_size, m.vegan, m.vegetarian, m.label_coverage, m.allergens, m.collected_at,
       m.valid_from, m.valid_until, m.content_hash,
       s.source_key, s.canonical_url, s.freshness_sla_hours
FROM rockygpt_v2.menu_items m
JOIN rockygpt_v2.sources s ON s.id = m.source_id
WHERE m.dataset_version_id = %s::uuid AND EXISTS (
  SELECT 1 FROM links l WHERE l.source_key = s.source_key
    AND l.source_record_keys ? m.source_record_key
)
ORDER BY m.valid_from, m.meal, m.station, m.name, m.id
LIMIT %s
"""

_TRIPS = """
WITH links AS (
  SELECT * FROM jsonb_to_recordset(%s::jsonb) AS link(source_key text, source_record_keys jsonb)
)
SELECT t.id::text AS id, t.source_record_key, r.name AS route_name, r.service_day,
       t.sequence, t.departure, t.arrival, t.stops,
       -- A release published before migration 027 has no trains column; it reads as none.
       to_jsonb(t) -> 'trains' AS trains, t.collected_at, t.valid_from,
       t.valid_until, t.content_hash, s.source_key, s.canonical_url, s.freshness_sla_hours
FROM rockygpt_v2.shuttle_trips t
JOIN rockygpt_v2.shuttle_routes r ON r.id = t.route_id
JOIN rockygpt_v2.sources s ON s.id = t.source_id
WHERE t.dataset_version_id = %s::uuid AND EXISTS (
  SELECT 1 FROM links l WHERE l.source_key = s.source_key
    AND l.source_record_keys ? t.source_record_key
)
ORDER BY r.name, t.sequence, t.id
LIMIT %s
"""

_MENU_DOCUMENTS = """
WITH links AS (
  SELECT * FROM jsonb_to_recordset(%s::jsonb) AS link(source_key text, source_record_keys jsonb)
)
SELECT d.id::text AS id, d.source_record_key, d.name, d.title, d.url, d.mime_type, d.collected_at,
       d.content_hash, s.source_key, s.canonical_url, s.freshness_sla_hours
FROM rockygpt_v2.menu_documents d
JOIN rockygpt_v2.sources s ON s.id = d.source_id
WHERE d.dataset_version_id = %s::uuid AND EXISTS (
  SELECT 1 FROM links l WHERE l.source_key = s.source_key
    AND l.source_record_keys ? d.source_record_key
)
ORDER BY d.title, d.id
LIMIT %s
"""

_SHUTTLE_STOPS = """
WITH links AS (
  SELECT * FROM jsonb_to_recordset(%s::jsonb) AS link(source_key text, source_record_keys jsonb)
)
SELECT p.id::text AS id, p.source_record_key, p.name, p.label, p.description, p.pickup,
       p.collected_at, p.content_hash, s.source_key, s.canonical_url, s.freshness_sla_hours
FROM rockygpt_v2.shuttle_stops p
JOIN rockygpt_v2.sources s ON s.id = p.source_id
WHERE p.dataset_version_id = %s::uuid AND EXISTS (
  SELECT 1 FROM links l WHERE l.source_key = s.source_key
    AND l.source_record_keys ? p.source_record_key
)
ORDER BY p.name, p.id
LIMIT %s
"""

_MENU_LISTINGS = """
WITH links AS (
  SELECT * FROM jsonb_to_recordset(%s::jsonb) AS link(source_key text, source_record_keys jsonb)
)
SELECT m.id::text AS id, m.source_record_key, m.name, m.dated, m.menus, m.collected_at,
       m.content_hash, s.source_key, s.canonical_url, s.freshness_sla_hours
FROM rockygpt_v2.menu_listings m
JOIN rockygpt_v2.sources s ON s.id = m.source_id
WHERE m.dataset_version_id = %s::uuid AND EXISTS (
  SELECT 1 FROM links l WHERE l.source_key = s.source_key
    AND l.source_record_keys ? m.source_record_key
)
ORDER BY m.name, m.id
LIMIT %s
"""

_CAMPUS_TOPICS = """
WITH links AS (
  SELECT * FROM jsonb_to_recordset(%s::jsonb) AS link(source_key text, source_record_keys jsonb)
)
SELECT t.id::text AS id, t.source_record_key, t.name, t.status, t.handled_by, t.url, t.section,
       t.quote, t.refer_url, t.checked_at, t.collected_at, t.content_hash, s.source_key,
       s.freshness_sla_hours
FROM rockygpt_v2.campus_topics t
JOIN rockygpt_v2.sources s ON s.id = t.source_id
WHERE t.dataset_version_id = %s::uuid AND EXISTS (
  SELECT 1 FROM links l WHERE l.source_key = s.source_key
    AND l.source_record_keys ? t.source_record_key
)
ORDER BY t.name, t.id
LIMIT %s
"""

_OFFICE_SECTIONS = """
WITH links AS (
  SELECT * FROM jsonb_to_recordset(%s::jsonb) AS link(source_key text, source_record_keys jsonb)
)
SELECT o.id::text AS id, o.source_record_key, o.site_folder, o.url, o.sections, o.collected_at,
       o.content_hash, o.howto_entries, s.source_key, s.freshness_sla_hours
FROM rockygpt_v2.office_sections o
JOIN rockygpt_v2.sources s ON s.id = o.source_id
WHERE o.dataset_version_id = %s::uuid AND EXISTS (
  SELECT 1 FROM links l WHERE l.source_key = s.source_key
    AND l.source_record_keys ? o.source_record_key
)
ORDER BY o.source_record_key, o.id
LIMIT %s
"""

_PROGRAMS = """
WITH links AS (
  SELECT * FROM jsonb_to_recordset(%s::jsonb) AS link(source_key text, source_record_keys jsonb)
)
SELECT p.id::text AS id, p.source_record_key, p.name, p.degree, p.program_kind, p.school,
       p.description, p.program_url, p.collected_at, p.content_hash, s.source_key,
       s.freshness_sla_hours
FROM rockygpt_v2.programs p
JOIN rockygpt_v2.sources s ON s.id = p.source_id
WHERE p.dataset_version_id = %s::uuid AND EXISTS (
  SELECT 1 FROM links l WHERE l.source_key = s.source_key
    AND l.source_record_keys ? p.source_record_key
)
ORDER BY p.source_record_key, p.id
LIMIT %s
"""

# A requirement group is a record of the program-requirement-groups artifact, read in the order the
# program's link lists it; it was compiled from the programs artifact, whose capture time it keeps.
_REQUIREMENT_GROUPS = """
WITH links AS (
  SELECT * FROM jsonb_to_recordset(%s::jsonb) AS link(source_key text, source_record_keys jsonb)
), wanted AS (
  SELECT k.value AS key, k.ordinality AS ord
  FROM links l, jsonb_array_elements_text(l.source_record_keys) WITH ORDINALITY AS k
  WHERE l.source_key = 'program-requirement-groups'
), release AS (SELECT %s::uuid AS id)
SELECT g->>'id' AS id, g->>'id' AS source_record_key,
       'program-requirement-groups' AS source_key, g AS requirement,
       a.content_hash, (p.payload->>'generatedAt')::timestamptz AS collected_at,
       s.freshness_sla_hours
FROM release
JOIN rockygpt_v2.release_artifacts a ON a.dataset_version_id = release.id
  AND a.artifact_key = 'program-requirement-groups'
JOIN rockygpt_v2.release_artifacts p ON p.dataset_version_id = release.id
  AND p.artifact_key = 'programs'
LEFT JOIN rockygpt_v2.sources s ON s.source_key = 'academic-programs'
CROSS JOIN LATERAL jsonb_array_elements(a.payload->'groups') AS g
JOIN wanted w ON w.key = g->>'id'
ORDER BY w.ord
LIMIT %s
"""

# The academic-programs capture a catalog course comes from: the courses and the course
# identities (each course's own catalog page), the capture time, and the source's freshness.
_COURSE_CATALOG = """
SELECT c.payload AS courses, i.payload AS identities,
       (p.payload->>'generatedAt')::timestamptz AS collected_at,
       (SELECT s.freshness_sla_hours FROM rockygpt_v2.sources s
        WHERE s.source_key = 'academic-programs') AS freshness_sla_hours
FROM rockygpt_v2.release_artifacts c
JOIN rockygpt_v2.release_artifacts i ON i.dataset_version_id = c.dataset_version_id
  AND i.artifact_key = 'catalog-course-identities'
JOIN rockygpt_v2.release_artifacts p ON p.dataset_version_id = c.dataset_version_id
  AND p.artifact_key = 'programs'
WHERE c.dataset_version_id = %s::uuid AND c.artifact_key = 'courses'
"""

_GRADUATION_PLANS = """
SELECT a.payload, a.content_hash,
       (SELECT s.freshness_sla_hours FROM rockygpt_v2.sources s
        WHERE s.source_key = 'graduation-plans') AS freshness_sla_hours
FROM rockygpt_v2.release_artifacts a
WHERE a.dataset_version_id = %s::uuid AND a.artifact_key = 'graduation-plans'
"""

# A calendar event's row, with the location, tags and ticket status its Archway listing entry
# (the events artifact, keyed by date and title) publishes beside it.
_EVENTS = """
WITH links AS (
  SELECT * FROM jsonb_to_recordset(%s::jsonb) AS link(
    source_key text, source_record_keys jsonb, source_record_ids jsonb)
), release AS (SELECT %s::uuid AS id)
SELECT e.id::text AS id, e.source_record_key, e.title, e.date_label, e.starts_at,
       e.start_time, e.end_time, e.organizer, e.description, e.event_url, e.collected_at,
       e.valid_from, e.valid_until, e.content_hash, s.source_key, s.freshness_sla_hours,
       listing.item AS listing
FROM release
JOIN rockygpt_v2.campus_events e ON e.dataset_version_id = release.id
JOIN rockygpt_v2.sources s ON s.id = e.source_id
LEFT JOIN LATERAL (
  SELECT item FROM rockygpt_v2.release_artifacts a,
       jsonb_array_elements(a.payload) AS item
  WHERE a.dataset_version_id = release.id AND a.artifact_key = 'events'
    AND item->>'date' || ':' || (item->>'title') = e.source_record_key
  LIMIT 1
) listing ON true
WHERE EXISTS (
  SELECT 1 FROM links l WHERE l.source_key = s.source_key
    AND l.source_record_keys ? e.source_record_key
    -- Two events of one title on one day share a key; the link pins its own row.
    AND (l.source_record_ids IS NULL OR l.source_record_ids ? e.id::text)
)
ORDER BY e.starts_at, e.id
LIMIT %s
"""

# A club's directory row, with the membership note its Archway listing entry publishes.
_CLUBS = """
WITH links AS (
  SELECT * FROM jsonb_to_recordset(%s::jsonb) AS link(
    source_key text, source_record_keys jsonb, source_record_ids jsonb)
), release AS (SELECT %s::uuid AS id)
SELECT c.id::text AS id, c.source_record_key, c.name, c.category, c.website_url,
       c.collected_at, c.valid_from, c.valid_until, c.content_hash, s.source_key,
       s.freshness_sla_hours, listing.item AS listing
FROM release
JOIN rockygpt_v2.clubs c ON c.dataset_version_id = release.id
JOIN rockygpt_v2.sources s ON s.id = c.source_id
LEFT JOIN LATERAL (
  SELECT item FROM rockygpt_v2.release_artifacts a,
       jsonb_array_elements(a.payload) AS item
  WHERE a.dataset_version_id = release.id AND a.artifact_key = 'clubs'
    AND item->>'name' = c.source_record_key
  LIMIT 1
) listing ON true
WHERE EXISTS (
  SELECT 1 FROM links l WHERE l.source_key = s.source_key
    AND l.source_record_keys ? c.source_record_key
    AND (l.source_record_ids IS NULL OR l.source_record_ids ? c.id::text)
)
ORDER BY c.source_record_key, c.id
LIMIT %s
"""

# The faculty profiles, with the faculty source's capture time (its directory rows') and freshness.
_FACULTY = """
SELECT a.payload, a.content_hash,
       (SELECT max(c.collected_at) FROM rockygpt_v2.campus_contacts c
        JOIN rockygpt_v2.sources s ON s.id = c.source_id
        WHERE c.dataset_version_id = a.dataset_version_id AND s.source_key = 'faculty')
         AS collected_at,
       (SELECT s.freshness_sla_hours FROM rockygpt_v2.sources s
        WHERE s.source_key = 'faculty') AS freshness_sla_hours
FROM rockygpt_v2.release_artifacts a
WHERE a.dataset_version_id = %s::uuid AND a.artifact_key = 'faculty'
"""

_RECORD_SQL = {"programs": _PROGRAMS, "events": _EVENTS, "clubs": _CLUBS,
               "requirement_groups": _REQUIREMENT_GROUPS,
               "dining_hours": _DINING, "menu": _MENU, "shuttle_trips": _TRIPS,
               "menu_documents": _MENU_DOCUMENTS, "menu_listings": _MENU_LISTINGS,
               "shuttle_stops": _SHUTTLE_STOPS, "campus_topics": _CAMPUS_TOPICS,
               "office_sections": _OFFICE_SECTIONS}


# A failed connection has no SQLSTATE, and libpq's message names hosts; these name the cause alone.
_CAUSES = (
    ("certificate", "tls_certificate"),
    ("ssl", "tls"),
    ("password authentication", "authentication"),
    ("channel binding", "authentication"),
    ("unsupported startup parameter", "startup_parameter"),
    ("could not translate host name", "dns"),
    ("timeout", "timeout"),
    ("timed out", "timeout"),
    ("connection refused", "refused"),
    ("too many", "too_many_connections"),
)


def failure_cause(error: Exception) -> str:
    message = str(error).lower()
    return next((cause for needle, cause in _CAUSES if needle in message), "other")


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
        self._idle: list[tuple[psycopg.Connection[dict[str, Any]], float]] = []
        self._idle_lock = Lock()
        self._registry: tuple[str, str, str] | None = None  # dataset id, hash, registry JSON

    def _open(self, operation_seconds: float) -> psycopg.Connection[dict[str, Any]]:
        # A URL that asks for certificate checks (Neon's sslmode=verify-full) needs trusted roots;
        # the slim image has no ~/.postgresql/root.crt, so libpq would refuse to connect.
        tls: dict[str, Any] = {}
        if (not os.getenv("PGSSLROOTCERT")
                and "sslrootcert" not in conninfo_to_dict(self._database_url)):
            tls["sslrootcert"] = certifi.where()
        return psycopg.connect(
            self._database_url,
            connect_timeout=self._connect_timeout,
            row_factory=dict_row,
            # A kept connection would otherwise prepare repeated reads on the server, and a
            # transaction pooler (PgBouncer) may run the next transaction on another backend.
            prepare_threshold=None,
            tcp_user_timeout=int(operation_seconds * 1_000),
            keepalives_idle=3,
            keepalives_interval=1,
            keepalives_count=2,
            **tls,
        )

    @staticmethod
    def _begin(connection: psycopg.Connection[dict[str, Any]], statement_ms: int) -> None:
        """One repeatable-read, read-only transaction with its limits set inside it. Startup
        options would be simpler, but a pooled Neon connection (PgBouncer) refuses them."""
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        connection.execute(
            "SELECT set_config('statement_timeout', %s, true), "
            "set_config('default_transaction_read_only', 'on', true), "
            "set_config('idle_in_transaction_session_timeout', '5000', true)",
            (f"{statement_ms}ms",),
        )

    def _kept(self) -> psycopg.Connection[dict[str, Any]] | None:
        now, stale = _idle_clock(), []
        with self._idle_lock:
            while self._idle:
                connection, since = self._idle.pop()
                if now - since < IDLE_SECONDS and not connection.closed:
                    break
                stale.append(connection)
            else:
                connection = None
        for old in stale:
            self._discard(old)
        return connection

    def _keep(self, connection: psycopg.Connection[dict[str, Any]], reuse: bool) -> None:
        try:
            connection.rollback()  # Ends the read-only transaction; nothing was written.
        except psycopg.Error:
            self._discard(connection)
            return
        with self._idle_lock:
            if reuse and not connection.closed and len(self._idle) < MAX_IDLE_CONNECTIONS:
                self._idle.append((connection, _idle_clock()))
                return
        self._discard(connection)

    @staticmethod
    def _discard(connection: psycopg.Connection[dict[str, Any]]) -> None:
        with suppress(Exception):
            connection.close()

    @contextmanager
    def _connect(self, operation_seconds: float,
                 statement_ms: int) -> Iterator[psycopg.Connection[dict[str, Any]]]:
        # Only question reads are kept: a build's connection has its own, longer TCP limit.
        reuse = operation_seconds == self._operation_timeout
        connection = self._kept() if reuse else None
        if connection is not None:
            try:
                self._begin(connection, statement_ms)
            except psycopg.Error:  # The server closed it while it sat idle: open a new one.
                self._discard(connection)
                connection = None
        if connection is None:
            connection = self._open(operation_seconds)
            try:
                self._begin(connection, statement_ms)
            except BaseException:
                self._discard(connection)
                raise
        try:
            yield connection
        except BaseException as error:
            if isinstance(error, psycopg.Error):
                self._discard(connection)
            else:
                self._keep(connection, reuse)
            raise
        self._keep(connection, reuse)

    def active_release(self) -> tuple[str, str]:
        try:
            with self._connect(self._operation_timeout, self._statement_timeout) as connection:
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
            wanted = [entity for entity in snapshot.entities if entity["kind"] in kinds]
            evidence = {
                entity["id"]: (snapshot.contact_reader(entity), snapshot.schedule_reader(entity))
                for entity in wanted
            }
            return ReleaseInputs(
                snapshot.dataset_version, snapshot.identity_hash, list(snapshot.entities),
                list(snapshot.alias_sources), evidence, snapshot.fingerprint_reader(),
                linked_records(snapshot, wanted))

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
                kept = self._registry
                if kept is None or kept[:2] != (release["dataset_id"], release["identity_hash"]):
                    found = read(_REGISTRY, (release["dataset_id"], release["identity_hash"]))
                    if len(found) != 1 or not isinstance(found[0]["registry_text"], str):
                        raise EvidenceUnavailable("No unique active identity registry.")
                    kept = (release["dataset_id"], release["identity_hash"],
                            found[0]["registry_text"])
                try:
                    registry = json.loads(kept[2])
                except ValueError as exc:
                    raise EvidenceUnavailable("Unreadable identity registry.") from exc
                if not isinstance(registry, dict) or registry.get("schema_version") != 1:
                    raise EvidenceUnavailable("Unsupported identity registry schema.")
                entities = validate_entities(registry.get("entities"))
                self._registry = kept  # Kept only once it has been read whole and checked.

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

                # The course catalog is read once per snapshot: 2,658 courses are one 3 MB payload.
                catalog: dict[str, Any] = {}

                def courses(entity: dict[str, Any]) -> list[dict[str, Any]]:
                    keys = [key for link in entity["links"]
                            if link.get("collection") == "courses"
                            and link.get("source_key") == "academic-programs"
                            for key in link["source_record_keys"]]
                    if not keys:
                        return []
                    if not catalog:
                        rows = read(_COURSE_CATALOG, (release["dataset_id"],))
                        if len(rows) != 1 or not isinstance(rows[0]["courses"], dict):
                            raise EvidenceUnavailable("The course catalog is not published.")
                        catalog.update(rows[0], urls={
                            c.get("source_record_key"): c.get("url")
                            for c in (rows[0]["identities"] or {}).get("courses", [])})
                    return [{"id": f"course:{key}", "source_key": "academic-programs",
                             "source_record_key": key, "course": catalog["courses"][key],
                             "url": catalog["urls"].get(key),
                             "collected_at": catalog["collected_at"],
                             "freshness_sla_hours": catalog["freshness_sla_hours"]}
                            for key in keys[:RECORD_COLLECTIONS["courses"] + 1]
                            if isinstance(catalog["courses"].get(key), dict)]

                # The recommended graduation plans are one 8 MB payload, read once per snapshot.
                plans_read: dict[str, Any] = {}

                def plans(entity: dict[str, Any]) -> list[dict[str, Any]]:
                    keys = [key for link in entity["links"]
                            if link.get("collection") == "graduation_plans"
                            and link.get("source_key") == "graduation-plans"
                            for key in link["source_record_keys"]]
                    if not keys:
                        return []
                    if not plans_read:
                        rows = read(_GRADUATION_PLANS, (release["dataset_id"],))
                        if len(rows) != 1 or not isinstance(rows[0]["payload"], dict):
                            raise EvidenceUnavailable("The graduation plans are not published.")
                        plans_read.update(rows[0], by_id={
                            p.get("id"): p for p in rows[0]["payload"].get("plans", [])
                            if isinstance(p, dict)})
                    return [{"id": f"plan:{key}", "source_key": "graduation-plans",
                             "source_record_key": key, "plan": plans_read["by_id"][key],
                             "collected_at": plans_read["payload"].get("captured_at"),
                             "freshness_sla_hours": plans_read["freshness_sla_hours"],
                             "content_hash": plans_read["content_hash"]}
                            for key in keys[:RECORD_COLLECTIONS["graduation_plans"] + 1]
                            if key in plans_read["by_id"]]

                # The faculty profiles are one payload, read once per snapshot; a profile is
                # keyed by its page and email (or name, where none is published).
                profiles: dict[str, Any] = {}

                def faculty(entity: dict[str, Any]) -> list[dict[str, Any]]:
                    keys = [key for link in entity["links"]
                            if link.get("collection") == "faculty"
                            and link.get("source_key") == "faculty"
                            for key in link["source_record_keys"]]
                    if not keys:
                        return []
                    if not profiles:
                        rows = read(_FACULTY, (release["dataset_id"],))
                        if len(rows) != 1 or not isinstance(rows[0]["payload"], list):
                            raise EvidenceUnavailable("The faculty profiles are not published.")
                        by_key: dict[str, Any] = {}
                        for item in rows[0]["payload"]:
                            page = str(item.get("profileUrl") or "").rstrip("/")
                            if item.get("email"):
                                by_key[f"{page}#email={item['email']}"] = item
                            if item.get("name"):
                                by_key[f"{page}#name={quote(str(item['name']))}"] = item
                        profiles.update(rows[0], by_key=by_key)
                    return [{"id": f"faculty:{key}", "source_key": "faculty",
                             "source_record_key": key, "profile": profiles["by_key"][key],
                             "collected_at": profiles["collected_at"],
                             "freshness_sla_hours": profiles["freshness_sla_hours"],
                             "content_hash": profiles["content_hash"]}
                            for key in keys[:RECORD_COLLECTIONS["faculty"] + 1]
                            if key in profiles["by_key"]]

                def records(entity: dict[str, Any], collection: str) -> list[dict[str, Any]]:
                    if collection == "faculty":
                        return faculty(entity)
                    if collection == "courses":
                        return courses(entity)
                    if collection == "graduation_plans":
                        return plans(entity)
                    query = _RECORD_SQL.get(collection)
                    links = [link for link in entity["links"]
                             if link.get("collection") == collection]
                    if query is None or not links:
                        return []
                    limit = RECORD_COLLECTIONS[collection]
                    result = read(query, (json.dumps(links), release["dataset_id"], limit + 1))
                    if len(result) > limit:
                        raise EvidenceUnavailable(
                            "Campus record evidence exceeds the bounded read.")
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
                    records,
                )
        except psycopg.Error as exc:
            # Database URLs, passwords, row contents, and provider diagnostics are private; the
            # error class and SQLSTATE say which (timeout, permission, missing table) without them.
            LOG.warning("campus_evidence_read_failed error=%s sqlstate=%s cause=%s",
                        type(exc).__name__, exc.sqlstate, failure_cause(exc))
            raise EvidenceUnavailable(
                "Published campus evidence is temporarily unavailable."
            ) from exc
