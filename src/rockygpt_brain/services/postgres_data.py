from __future__ import annotations

import asyncio
import json
import logging
import ssl
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import asyncpg

from rockygpt_brain.services.artifacts import PublishedArtifact
from rockygpt_brain.services.data import DataUnavailable
from rockygpt_brain.services.directory_query import (
    build_directory_all_contacts,
    course_credits,
    load_course_subjects,
    load_map_locations,
)
from rockygpt_brain.services.program_search import (
    parse_program_search,
    program_matches_criteria,
)
from rockygpt_brain.services.rag.client import Passage
from rockygpt_brain.services.schedule_status import schedule_status_at
from rockygpt_brain.services.search_terms import (
    TermFrequencies,
    build_term_frequencies,
    search_terms_for,
)
from rockygpt_brain.services.shuttle_query import (
    CAMPUS_TIME_ZONE,
    DatedTrip,
    ShuttleTripRecord,
    campus_date,
    execute_shuttle_query,
    previous_date,
    service_day_for_date,
)

logger = logging.getLogger(__name__)


def _use_system_ca_store(database_url: str) -> ssl.SSLContext | bool | None:
    ssl_mode = parse_qs(urlsplit(database_url).query).get("sslmode", [""])[-1].casefold()
    if ssl_mode in {"verify-ca", "verify-full", "require"}:
        return ssl.create_default_context()
    if ssl_mode == "disable":
        return False
    if "ssl=true" in database_url.lower() or "sslmode=" in database_url.lower():
        return ssl.create_default_context()
    return None


def _format_iso(val: Any) -> str | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.isoformat().replace("+00:00", "Z")
    s = str(val).strip()
    if not s:
        return None
    if s.endswith("+00"):
        s = s[:-3] + "Z"
    return s.replace(" ", "T")


async def _register_json_codecs(connection: asyncpg.Connection) -> None:
    """Decode json/jsonb into Python objects instead of leaving them as text.

    asyncpg hands back both types as `str` unless a codec says otherwise. Four
    query methods used to work around that with a local `json.loads` and an
    `isinstance(..., str)` guard, but `artifact()` did not — so every
    artifact-backed endpoint received a JSON string where it expected an object.
    The published payload then serialised a second time on the way out, which is
    why `/v1/data/programs` answered 2.5MB against the data service's 2.16MB.

    Registering the codec once per pooled connection fixes both directions, and
    is the reason the call sites below no longer decode by hand.
    """
    for type_name in ("json", "jsonb"):
        await connection.set_type_codec(
            type_name,
            encoder=json.dumps,
            decoder=json.loads,
            schema="pg_catalog",
        )


class PostgresData:
    """Direct PostgreSQL reader implementing the DataPort protocol."""

    def __init__(
        self,
        database_url: str,
        *,
        pool_min_size: int = 1,
        pool_max_size: int = 5,
    ) -> None:
        if not database_url:
            raise ValueError("database_url must not be empty")
        self._database_url = database_url.strip().strip('"').strip("'")
        self._pool_min_size = pool_min_size
        self._pool_max_size = pool_max_size
        self._pool: asyncpg.Pool | None = None
        self._initialize_lock = asyncio.Lock()

    async def initialize(self) -> None:
        async with self._initialize_lock:
            if self._pool is not None:
                return
            self._pool = await asyncpg.create_pool(
                dsn=self._database_url,
                min_size=self._pool_min_size,
                max_size=self._pool_max_size,
                command_timeout=15,
                ssl=_use_system_ca_store(self._database_url),
                init=_register_json_codecs,
            )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def _ready_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            await self.initialize()
        if self._pool is None:
            raise RuntimeError("PostgresData did not initialize")
        return self._pool

    async def _active_dataset_id(self) -> str:
        pool = await self._ready_pool()
        row = await pool.fetchrow(
            """
            SELECT id::text
              FROM rockygpt_v2.dataset_versions
             WHERE status = 'active'
             ORDER BY activated_at DESC
             LIMIT 1
            """
        )
        if not row:
            raise DataUnavailable("No active dataset version found in database")
        return str(row["id"])

    async def ready(self) -> None:
        await self._active_dataset_id()

    async def artifact(self, key: str) -> PublishedArtifact:
        pool = await self._ready_pool()
        row = await pool.fetchrow(
            """
            SELECT v.version, v.activated_at::text, a.payload, a.content_hash
              FROM rockygpt_v2.dataset_versions v
              JOIN rockygpt_v2.release_artifacts a
                ON a.dataset_version_id = v.id
             WHERE v.status = 'active'
               AND a.artifact_key = $1
             ORDER BY v.activated_at DESC
             LIMIT 1
            """,
            key,
        )
        if not row:
            raise DataUnavailable(f"The active dataset has no {key!r} artifact")
        return PublishedArtifact(
            payload=row["payload"],
            release_version=str(row["version"]),
            activated_at=_format_iso(row["activated_at"]),
            content_hash=str(row["content_hash"]) if row["content_hash"] else None,
        )

    async def retrieve(self, topic: str, limit: int) -> list[Passage]:
        dataset_id = await self._active_dataset_id()
        pool = await self._ready_pool()
        rows = await pool.fetch(
            """
            WITH q AS (
              SELECT replace(plainto_tsquery('english', $2)::text, '&', '|')::tsquery AS loose,
                     string_to_array(
                       replace(replace(plainto_tsquery('english', $2)::text, '''', ''),
                               ' & ', ','),
                       ','
                     ) AS terms
            ),
            scored AS (
              SELECT coalesce(c.metadata->>'headingPath', d.title) AS title,
                     coalesce(c.metadata->>'canonicalUrl', s.canonical_url) AS url,
                     c.content, s.domain,
                     power(
                       (SELECT count(*) FROM unnest(q.terms) AS t
                         WHERE c.lexical_vector @@ plainto_tsquery('english', t))::float
                       / greatest(array_length(q.terms, 1), 1),
                       2
                     ) * ts_rank_cd(c.lexical_vector, q.loose, 1) AS relevance,
                     CASE s.trust_tier WHEN 'official_primary' THEN 1.05
                                       WHEN 'official_secondary' THEN 1.02 ELSE 1 END AS trust
                FROM rockygpt_v2.document_chunks c
                JOIN rockygpt_v2.documents d ON d.id = c.document_id
                JOIN rockygpt_v2.sources s ON s.id = d.source_id
                CROSS JOIN q
               WHERE d.dataset_version_id = $1::uuid
                 AND c.lexical_vector @@ q.loose
            )
            SELECT title, url, content, domain, (relevance * trust) AS score
              FROM scored
             WHERE relevance >= 0.005
             ORDER BY score DESC, url
             LIMIT $3
            """,
            dataset_id,
            topic,
            limit,
        )
        return [
            Passage(
                content=str(row["content"]),
                domain=str(row["domain"]),
                title=str(row["title"]),
                url=str(row["url"]),
            )
            for row in rows
        ]

    async def list_shuttle_trips(
        self, dataset_id: str, service_day: str
    ) -> list[ShuttleTripRecord]:
        pool = await self._ready_pool()
        rows = await pool.fetch(
            """
            SELECT r.name AS route, t.departure, t.arrival, t.stops,
                   s.id::text AS source_id
              FROM rockygpt_v2.shuttle_trips t
              JOIN rockygpt_v2.shuttle_routes r ON r.id = t.route_id
              JOIN rockygpt_v2.sources s ON s.id = t.source_id
             WHERE t.dataset_version_id = $1::uuid
               AND (
                 r.service_day = $2::text
                 OR (
                   r.service_day IS NULL
                   AND CASE $2::text
                     WHEN 'weekday' THEN lower(r.name) NOT LIKE '%saturday%'
                                       AND lower(r.name) NOT LIKE '%sunday%'
                     WHEN 'saturday' THEN lower(r.name) LIKE '%saturday%'
                     WHEN 'sunday' THEN lower(r.name) LIKE '%sunday%'
                     ELSE false
                   END
                 )
               )
             ORDER BY t.sequence, r.name
            """,
            dataset_id,
            service_day,
        )
        records: list[ShuttleTripRecord] = []
        for row in rows:
            raw_stops = row["stops"]
            stops_list: list[dict[str, str]] = []
            if isinstance(raw_stops, list):
                for s in raw_stops:
                    if isinstance(s, dict) and "location" in s and "time" in s:
                        stops_list.append({"location": str(s["location"]), "time": str(s["time"])})
            records.append(
                ShuttleTripRecord(
                    route=str(row["route"]),
                    departure=str(row["departure"]),
                    arrival=str(row["arrival"]),
                    stops=stops_list,
                    source_id=str(row["source_id"]),
                )
            )
        return records

    async def shuttle(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        dataset_id = await self._active_dataset_id()
        as_of_str = query.get("asOf")
        if as_of_str:
            try:
                as_of = datetime.fromisoformat(as_of_str)
            except Exception:
                as_of = datetime.now(ZoneInfo(CAMPUS_TIME_ZONE))
        else:
            as_of = datetime.now(ZoneInfo(CAMPUS_TIME_ZONE))

        service_date = query.get("serviceDate") or campus_date(as_of)
        time_scope = query.get("timeScope", "remaining")
        if service_date == campus_date(as_of) and time_scope in ("at_time", "remaining"):
            dates_considered = [service_date, previous_date(service_date)]
        else:
            dates_considered = [service_date]

        all_trips: list[DatedTrip] = []
        for s_date in dates_considered:
            s_day = service_day_for_date(s_date)
            trips = await self.list_shuttle_trips(dataset_id, s_day)
            for t in trips:
                all_trips.append(DatedTrip(trip=t, service_date=s_date, service_day=s_day))

        return execute_shuttle_query(all_trips, query, as_of)

    async def transportation(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        return await self.shuttle(query)

    async def _find_hours(
        self,
        table: str,
        query_text: str,
        day: str,
        at: datetime | None,
    ) -> list[dict[str, Any]]:
        dataset_id = await self._active_dataset_id()
        on_date = at.date() if at else None
        pool = await self._ready_pool()
        rows = await pool.fetch(
            f"""
            WITH eligible_hours AS (
              SELECT h.*,
                     ROW_NUMBER() OVER (
                       PARTITION BY lower(h.name), lower(h.day)
                       ORDER BY
                         (h.valid_from IS NOT NULL) DESC,
                         h.valid_from ASC NULLS LAST,
                         h.valid_until ASC NULLS LAST,
                         h.source_record_key ASC
                     ) AS precedence_rank
                FROM rockygpt_v2.{table} h
               WHERE h.dataset_version_id = $1::uuid
                 AND lower(h.day) = lower($2)
                 AND (
                   (h.valid_from IS NULL AND h.valid_until IS NULL)
                   OR ($4::date IS NOT NULL AND $4::date BETWEEN h.valid_from AND h.valid_until)
                 )
                 AND ($3::text = '' OR to_tsvector('english', h.name) @@ plainto_tsquery('english', $3))
            )
            SELECT h.name, h.day, h.schedule,
                   s.id::text AS source_id, s.title AS source_title, s.canonical_url AS source_url,
                   h.collected_at::text
              FROM eligible_hours h JOIN rockygpt_v2.sources s ON s.id = h.source_id
             WHERE h.precedence_rank = 1
             ORDER BY
               CASE WHEN $3::text = '' THEN 0 ELSE
                 ts_rank(to_tsvector('english', h.name), plainto_tsquery('english', $3))
               END DESC,
               h.name
             LIMIT 5000
            """,
            dataset_id,
            day,
            query_text,
            on_date,
        )
        records: list[dict[str, Any]] = []
        for r in rows:
            source_dict: dict[str, Any] = {
                "sourceId": str(r["source_id"]),
                "title": str(r["source_title"]),
                "url": str(r["source_url"]),
            }
            if r["collected_at"] is not None:
                source_dict["collectedAt"] = str(r["collected_at"])
            rec: dict[str, Any] = {
                "name": str(r["name"]),
                "day": str(r["day"]),
                "schedule": str(r["schedule"]),
                "source": source_dict,
            }
            if at and day.lower() == at.strftime("%A").lower():
                minutes = at.hour * 60 + at.minute
                status = schedule_status_at(str(r["schedule"]), minutes)
                rec.update(status.to_dict())
            records.append(rec)
        return records

    async def campus_hours(self, query: dict[str, str]) -> list[dict[str, Any]]:
        at_str = query.get("at")
        at = datetime.fromisoformat(at_str) if at_str else datetime.now(ZoneInfo(CAMPUS_TIME_ZONE))
        day = query.get("day") or at.strftime("%A")
        q = query.get("q", "")
        return await self._find_hours("campus_hours", q, day, at)

    async def dining_hours(self, query: dict[str, str]) -> list[dict[str, Any]]:
        at_str = query.get("at")
        at = datetime.fromisoformat(at_str) if at_str else datetime.now(ZoneInfo(CAMPUS_TIME_ZONE))
        day = query.get("day") or at.strftime("%A")
        q = query.get("q", "")
        return await self._find_hours("dining_hours", q, day, at)

    async def dining(self, query: dict[str, str]) -> list[dict[str, Any]]:
        dataset_id = await self._active_dataset_id()
        q = query.get("q", "")
        meal = query.get("meal")
        pool = await self._ready_pool()
        rows = await pool.fetch(
            """
            SELECT m.valid_from::text AS date, m.meal, m.station, m.name, m.calories,
                   m.vegan, m.vegetarian, m.allergens,
                   s.id::text AS source_id, s.title AS source_title, s.canonical_url AS source_url,
                   m.collected_at::text
              FROM rockygpt_v2.menu_items m
              JOIN rockygpt_v2.sources s ON s.id = m.source_id
             WHERE m.dataset_version_id = $1::uuid
               AND ($2::text IS NULL OR lower(m.meal) = lower($2))
               AND ($3::text = '' OR to_tsvector('english',
                      m.meal || ' ' || m.station || ' ' || m.name
                      || CASE WHEN m.vegan THEN ' vegan' ELSE '' END
                      || CASE WHEN m.vegetarian THEN ' vegetarian' ELSE '' END)
                    @@ plainto_tsquery('english', $3))
             ORDER BY m.valid_from, m.meal, m.station, m.name
             LIMIT 5000
            """,
            dataset_id,
            meal,
            q,
        )
        records: list[dict[str, Any]] = []
        for r in rows:
            allergens = r["allergens"]
            rec: dict[str, Any] = {
                "meal": str(r["meal"]),
                "station": str(r["station"]),
                "name": str(r["name"]),
                "vegan": bool(r["vegan"]),
                "vegetarian": bool(r["vegetarian"]),
                "allergens": allergens if isinstance(allergens, list) else [],
                "source": {
                    "sourceId": str(r["source_id"]),
                    "title": str(r["source_title"]),
                    "url": str(r["source_url"]),
                    **(
                        {"collectedAt": _format_iso(r["collected_at"])} if r["collected_at"] else {}
                    ),
                },
            }
            if r["date"]:
                rec["date"] = str(r["date"])
            if r["calories"]:
                rec["calories"] = str(r["calories"])
            records.append(rec)
        return records

    async def _frequencies_for(self, frequencies_key: str, text_sql: str) -> TermFrequencies:
        pool = await self._ready_pool()
        dataset_id = await self._active_dataset_id()
        rows = await pool.fetch(text_sql, dataset_id)
        texts = [str(r["text"]) for r in rows if r["text"]]
        return build_term_frequencies(texts)

    async def _search_with_pruned_terms(
        self,
        query: str,
        frequencies_key: str,
        text_sql: str,
        runner: Callable[[str], Awaitable[list[dict[str, Any]]]],
        domain_words: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        if not query.strip():
            return await runner(query)
        try:
            freq = await self._frequencies_for(frequencies_key, text_sql)
            terms = search_terms_for(query, freq, domain_words)
        except Exception:
            return await runner(query)

        if not terms.primary:
            return await runner("")

        primary_results = await runner(terms.primary)
        if primary_results or not terms.fallback:
            return primary_results
        return await runner(terms.fallback)

    async def events(self, query: dict[str, str]) -> list[dict[str, Any]]:
        q = query.get("q", "")
        at_str = query.get("at")
        at = datetime.fromisoformat(at_str) if at_str else datetime.now(ZoneInfo(CAMPUS_TIME_ZONE))

        async def _find(query_text: str) -> list[dict[str, Any]]:
            dataset_id = await self._active_dataset_id()
            pool = await self._ready_pool()
            rows = await pool.fetch(
                """
                SELECT e.title, e.date_label, e.start_time, e.end_time, e.organizer,
                       e.description, e.event_url,
                       s.id::text AS source_id, s.title AS source_title, s.canonical_url AS source_url,
                       e.collected_at::text
                  FROM rockygpt_v2.campus_events e
                  JOIN rockygpt_v2.sources s ON s.id = e.source_id
                 WHERE e.dataset_version_id = $1::uuid
                   AND (e.starts_at IS NULL OR e.starts_at >= $2::timestamptz - interval '1 day')
                   AND ($3::text = '' OR to_tsvector('english', e.title || ' ' || coalesce(e.organizer, '') || ' ' || coalesce(e.description, ''))
                        @@ plainto_tsquery('english', $3))
                 ORDER BY e.starts_at NULLS LAST, e.title
                 LIMIT 5000
                """,
                dataset_id,
                at,
                query_text,
            )
            records: list[dict[str, Any]] = []
            for r in rows:
                rec: dict[str, Any] = {
                    "title": str(r["title"]),
                    "date": str(r["date_label"]),
                    "source": {
                        "sourceId": str(r["source_id"]),
                        "title": str(r["source_title"]),
                        "url": str(r["source_url"]),
                        **(
                            {"collectedAt": _format_iso(r["collected_at"])}
                            if r["collected_at"]
                            else {}
                        ),
                    },
                }
                if r["start_time"]:
                    rec["startTime"] = str(r["start_time"])
                if r["end_time"]:
                    rec["endTime"] = str(r["end_time"])
                if r["organizer"]:
                    rec["organizer"] = str(r["organizer"])
                if r["description"]:
                    rec["description"] = str(r["description"])
                if r["event_url"]:
                    rec["eventUrl"] = str(r["event_url"])
                records.append(rec)
            return records

        return await self._search_with_pruned_terms(
            q,
            "campus_events",
            "SELECT e.title || ' ' || coalesce(e.organizer, '') AS text FROM rockygpt_v2.campus_events e WHERE e.dataset_version_id = $1::uuid",
            _find,
            {"event", "events", "happening", "happenings", "going"},
        )

    async def courses(self, query: dict[str, str]) -> list[dict[str, Any]]:
        dataset_id = await self._active_dataset_id()
        q = query.get("q", "").strip()
        compact_q = q.replace(" ", "").lower()
        pool = await self._ready_pool()
        rows = await pool.fetch(
            """
            SELECT course.value->>'code' AS code,
                   course.value->>'name' AS name,
                   course.value->>'description' AS description,
                   course.value->'credits' AS credits,
                   course.value->'attributes' AS attributes
              FROM rockygpt_v2.release_artifacts artifact
              CROSS JOIN LATERAL jsonb_each(artifact.payload) AS course(key, value)
             WHERE artifact.dataset_version_id = $1::uuid
               AND artifact.artifact_key = 'courses'
               AND (
                 $2::text = ''
                 OR lower(regexp_replace(course.value->>'code', '\\s+', '', 'g')) = $3
                 OR to_tsvector(
                      'english',
                      coalesce(course.value->>'code', '') || ' ' ||
                      coalesce(course.value->>'name', '') || ' ' ||
                      coalesce(course.value->>'description', '') || ' ' ||
                      coalesce(course.value->>'attributes', '')
                    ) @@ plainto_tsquery('english', $2)
               )
             ORDER BY
               (lower(regexp_replace(course.value->>'code', '\\s+', '', 'g')) = $3) DESC,
               course.value->>'code'
             LIMIT 5000
            """,
            dataset_id,
            q,
            compact_q,
        )
        records: list[dict[str, Any]] = []
        for r in rows:
            code = str(r["code"])
            rec: dict[str, Any] = {
                "code": code,
                "name": str(r["name"]),
                "courseUrl": f"https://catalog.ramapo.edu/courses/{code.replace(' ', '')}",
                "source": {
                    "sourceId": "academic-programs",
                    "title": f"{code} - Ramapo Course Catalog",
                    "url": "https://www.ramapo.edu/majors-minors/",
                },
            }
            if r["description"]:
                rec["description"] = str(r["description"])
            credits_str = course_credits(r["credits"])
            if credits_str is not None:
                rec["credits"] = credits_str
            attrs = r["attributes"]
            rec["attributes"] = attrs if isinstance(attrs, list) else []
            records.append(rec)
        return records

    async def course_subjects(self, query: dict[str, str]) -> list[dict[str, Any]]:
        return load_course_subjects()

    async def calendar(self, query: dict[str, str]) -> list[dict[str, Any]]:
        dataset_id = await self._active_dataset_id()
        q = query.get("q", "")
        family = query.get("family", "").lower()
        kind = query.get("kind", "").lower()
        term_id = query.get("termId", "").lower()
        session_id = query.get("sessionId", "").lower()
        wanted_date = query.get("date", "")
        starts_after = query.get("startsAfter", "")[:10]
        starts_before = query.get("startsBefore", "")[:10]

        pool = await self._ready_pool()
        rows = await pool.fetch(
            """
            SELECT a.family, a.kind, a.term, a.term_id, a.session, a.session_id,
                   a.date_label, a.starts_at::text, a.title, a.description,
                   s.id::text AS source_id, s.title AS source_title, s.canonical_url AS source_url,
                   a.collected_at::text
              FROM rockygpt_v2.academic_dates a
              JOIN rockygpt_v2.sources s ON s.id = a.source_id
             WHERE a.dataset_version_id = $1::uuid
               AND ($2::text = '' OR
                    to_tsvector('english', a.term || ' ' || a.title || ' ' || coalesce(a.description, ''))
                    @@ plainto_tsquery('english', $2))
             ORDER BY ts_rank(
               to_tsvector('english', a.term || ' ' || a.title || ' ' || coalesce(a.description, '')),
               plainto_tsquery('english', $2)
             ) DESC
             LIMIT 5000
            """,
            dataset_id,
            q,
        )
        records: list[dict[str, Any]] = []
        for r in rows:
            rec_family = str(r["family"]) if r["family"] else ""
            rec_kind = str(r["kind"]) if r["kind"] else ""
            rec_term_id = str(r["term_id"]) if r["term_id"] else ""
            rec_session_id = str(r["session_id"]) if r["session_id"] else ""
            rec_starts_at = str(r["starts_at"]) if r["starts_at"] else ""
            rec_date = rec_starts_at[:10] if rec_starts_at else ""

            if family and rec_family.lower() != family:
                continue
            if kind and rec_kind.lower() != kind:
                continue
            if term_id and rec_term_id.lower() != term_id:
                continue
            if session_id and rec_session_id.lower() != session_id:
                continue
            if wanted_date and rec_date != wanted_date:
                continue
            if starts_after and (not rec_date or rec_date < starts_after):
                continue
            if starts_before and (not rec_date or rec_date >= starts_before):
                continue

            rec: dict[str, Any] = {
                "term": str(r["term"]),
                "date": str(r["date_label"]),
                "title": str(r["title"]),
                "source": {
                    "sourceId": str(r["source_id"]),
                    "title": str(r["source_title"]),
                    "url": str(r["source_url"]),
                    **(
                        {"collectedAt": _format_iso(r["collected_at"])} if r["collected_at"] else {}
                    ),
                },
            }
            if rec_family:
                rec["family"] = rec_family
            if rec_kind:
                rec["kind"] = rec_kind
            if rec_term_id:
                rec["termId"] = rec_term_id
            if r["session"]:
                rec["session"] = str(r["session"])
            if rec_session_id:
                rec["sessionId"] = rec_session_id
            if rec_starts_at:
                rec["startsAt"] = rec_starts_at
            if r["description"]:
                rec["description"] = str(r["description"])

            records.append(rec)
        return records

    async def clubs(self, query: dict[str, str]) -> list[dict[str, Any]]:
        dataset_id = await self._active_dataset_id()
        q = query.get("q", "")
        pool = await self._ready_pool()
        rows = await pool.fetch(
            """
            SELECT c.name, c.category, c.website_url,
                   s.id::text AS source_id, s.title AS source_title, s.canonical_url AS source_url,
                   c.collected_at::text
              FROM rockygpt_v2.clubs c
              JOIN rockygpt_v2.sources s ON s.id = c.source_id
             WHERE c.dataset_version_id = $1::uuid
               AND ($2::text = '' OR to_tsvector('english', c.name || ' ' || coalesce(c.category, ''))
                    @@ plainto_tsquery('english', $2))
             ORDER BY c.name
             LIMIT 5000
            """,
            dataset_id,
            q,
        )
        records: list[dict[str, Any]] = []
        for r in rows:
            rec: dict[str, Any] = {
                "name": str(r["name"]),
                "source": {
                    "sourceId": str(r["source_id"]),
                    "title": str(r["source_title"]),
                    "url": str(r["source_url"]),
                    **(
                        {"collectedAt": _format_iso(r["collected_at"])} if r["collected_at"] else {}
                    ),
                },
            }
            if r["category"]:
                rec["category"] = str(r["category"])
            if r["website_url"]:
                rec["websiteUrl"] = str(r["website_url"])
            records.append(rec)
        return records

    async def directory(self, query: dict[str, str]) -> list[dict[str, Any]]:
        dataset_id = await self._active_dataset_id()
        pool = await self._ready_pool()
        row = await pool.fetchrow(
            """
            SELECT payload
              FROM rockygpt_v2.release_artifacts
             WHERE dataset_version_id = $1::uuid
               AND artifact_key = 'faculty'
            """,
            dataset_id,
        )
        faculty_payload: Any = []
        if row and row["payload"]:
            faculty_payload = row["payload"]
        return build_directory_all_contacts(faculty_payload)

    async def locations(self, query: dict[str, str]) -> list[dict[str, Any]]:
        return load_map_locations()

    async def programs(self, query: dict[str, str]) -> list[dict[str, Any]]:
        dataset_id = await self._active_dataset_id()
        q = query.get("q", "")
        criteria = parse_program_search(q)
        req_kind = criteria.requested_kind or ""
        pool = await self._ready_pool()
        rows = await pool.fetch(
            """
            SELECT p.name, p.degree, p.program_kind, p.school, p.description, p.program_url,
                   s.id::text AS source_id, s.title AS source_title, s.canonical_url AS source_url,
                   p.collected_at::text
              FROM rockygpt_v2.programs p
              JOIN rockygpt_v2.sources s ON s.id = p.source_id
             WHERE p.dataset_version_id = $1::uuid
               AND ($2::text = '' OR to_tsvector('english', p.name) @@ plainto_tsquery('english', $2))
               AND ($3::text = '' OR
                 coalesce(
                   p.program_kind,
                   CASE
                     WHEN p.name ~* '\\m4\\s*\\+\\s*1\\M' THEN 'special'
                     WHEN coalesce(p.degree, p.name) ~* 'certificate' THEN 'certificate'
                     WHEN coalesce(p.degree, p.name) ~* '\\mminor\\M' THEN 'minor'
                     WHEN p.name ~* 'undeclared|non-degree' THEN 'undeclared'
                     ELSE 'major'
                   END
                 ) = $3
               )
             ORDER BY
               CASE WHEN $2::text = '' THEN 0 ELSE
                 ts_rank(to_tsvector('english', p.name), plainto_tsquery('english', $2))
               END DESC,
               CASE coalesce(
                 p.program_kind,
                 CASE
                   WHEN p.name ~* '\\m4\\s*\\+\\s*1\\M' THEN 'special'
                   WHEN coalesce(p.degree, p.name) ~* 'certificate' THEN 'certificate'
                   WHEN coalesce(p.degree, p.name) ~* '\\mminor\\M' THEN 'minor'
                   WHEN p.name ~* 'undeclared|non-degree' THEN 'undeclared'
                   ELSE 'major'
                 END
               )
                 WHEN 'major' THEN 0
                 WHEN 'minor' THEN 1
                 WHEN 'certificate' THEN 2
                 WHEN 'special' THEN 3
                 ELSE 4
               END,
               p.name
             LIMIT 5000
            """,
            dataset_id,
            criteria.subject,
            req_kind,
        )
        records: list[dict[str, Any]] = []
        for r in rows:
            name = str(r["name"])
            degree = str(r["degree"]) if r["degree"] else None
            p_kind = str(r["program_kind"]) if r["program_kind"] else None
            if not program_matches_criteria(name, degree, p_kind, criteria):
                continue
            rec: dict[str, Any] = {
                "name": name,
                "source": {
                    "sourceId": str(r["source_id"]),
                    "title": str(r["source_title"]),
                    "url": str(r["source_url"]),
                    **(
                        {"collectedAt": _format_iso(r["collected_at"])} if r["collected_at"] else {}
                    ),
                },
            }
            if degree:
                rec["degree"] = degree
            if p_kind:
                rec["programKind"] = p_kind
            if r["school"]:
                rec["school"] = str(r["school"])
            if r["description"]:
                rec["description"] = str(r["description"])
            if r["program_url"]:
                rec["programUrl"] = str(r["program_url"])
            records.append(rec)
        return records
