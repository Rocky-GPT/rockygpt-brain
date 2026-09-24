"""Exact, paginated navigation over published campus records, without model calls."""

from __future__ import annotations

import json
from collections import Counter
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from psycopg import sql

from rockygpt_brain.retrieval.data import CampusData
from rockygpt_brain.retrieval.menu_artifacts import (
    MenuIndex,
    menu_artifact_index,
    menu_occurrence,
    supplement_menu,
)
from rockygpt_brain.retrieval.models import COLLECTIONS, TABLES
from rockygpt_brain.retrieval.profiles import IdentityRegistry

LABELS = {
    "documents": "Documents", "document_chunks": "Document passages",
    "critical_facts": "Campus facts", "contacts": "Contact records",
    "campus_hours": "Operating hours", "dining_hours": "Dining schedules",
    "menu": "Menu offerings", "calendar": "Academic calendar", "events": "Events",
    "clubs": "Organizations directory", "programs": "Academic programs",
    "program_requirements": "Program requirements", "courses": "Catalog courses",
    "faculty": "Faculty profiles", "shuttle": "Shuttle trips",
    "shuttle_routes": "Shuttle routes", "artifacts": "Published source artifacts",
    "buildings": "Campus buildings", "schools": "Schools", "subjects": "Course subjects",
    "graduation_plans": "Graduation plans",
}
GROUP_FIELDS = {
    "contacts": ("department", "type"), "campus_hours": ("name", "day"),
    "dining_hours": ("name", "day"), "menu": ("date", "meal", "station"),
    "calendar": ("term", "session", "date"), "events": ("date", "organizer"),
    "clubs": ("category",), "programs": ("school", "degree", "program_kind"),
    "program_requirements": ("program",), "courses": (), "faculty": ("school",),
    "shuttle": ("route", "service_day"), "shuttle_routes": ("service_day",),
    "documents": ("source_key",), "document_chunks": ("document_id",),
    "critical_facts": (), "artifacts": (), "buildings": ("category",), "schools": (),
    "subjects": (), "graduation_plans": ("cohort",),
}
ARTIFACT_COLLECTIONS = {"faculty", "courses", "program_requirements", "buildings", "schools",
                        "subjects", "graduation_plans"}
GRAPH_COLLECTIONS = (*COLLECTIONS, "document_chunks", "shuttle_routes", "artifacts")
META_FIELDS = {
    "id", "source_id", "source_record_key", "dataset_version_id", "collected_at",
    "valid_from", "valid_until", "content_hash", "lexical_vector",
}


def _title(row: dict[str, Any], collection: str) -> str:
    if collection == "document_chunks":
        return f"Passage {int(row.get('chunk_index', 0)) + 1}"
    return str(next((row[key] for key in (
        "name", "title", "code", "fact_key", "route", "artifact_key", "source_record_key", "id"
    ) if row.get(key) is not None), collection))


def _page(items: list[Any], total: int, offset: int, limit: int) -> dict[str, Any]:
    return {
        "total": total, "returned": len(items), "offset": offset, "limit": limit,
        "next_offset": offset + len(items) if offset + len(items) < total else None,
    }


class GraphData:
    def __init__(
        self, data: CampusData, entity_id: UUID | None = None,
        registry: IdentityRegistry | None = None,
    ) -> None:
        """`registry`, when given, is this release's already validated identity registry."""
        self.data = data
        self.entity = None
        self.diagnostics: list[dict[str, Any]] = []
        self._artifact_cache: dict[str, list[dict[str, Any]]] = {}
        self._menu_cache: MenuIndex | None = None
        self._archway_cache: dict[str, dict[str, tuple[int, dict[str, Any]]]] = {}
        self._catalog_programs: dict[str, tuple[list[str], dict[str, Any]]] | None = None
        if entity_id is not None:
            if registry is None:
                try:
                    registry = IdentityRegistry.model_validate(
                        data._artifact("campus-identities"))
                except ValueError:
                    raise HTTPException(503, "Campus identity registry is unavailable") from None
            self.entity = next((e for e in registry.entities if e.id == entity_id), None)
            if self.entity is None:
                raise HTTPException(404, "Campus identity was not found")

    @staticmethod
    def validate(collection: str, filters: dict[str, Any], group_by: str | None = None) -> None:
        if collection not in LABELS:
            raise HTTPException(422, "Unknown campus collection")
        allowed = {*GROUP_FIELDS[collection], "source_key"} if collection != "artifacts" else set()
        if collection == "shuttle":
            allowed.add("route_id")
        if not filters.keys() <= allowed or (group_by is not None and group_by not in allowed):
            raise HTTPException(422, "Unsupported collection filter or grouping")
        if any(value is not None and not isinstance(value, (str, int, float, bool))
               for value in filters.values()):
            raise HTTPException(422, "Filters must contain scalar values or null")

    def _links(self, collection: str) -> list[Any]:
        if self.entity is None:
            return []
        links = [link for link in self.entity.links if link.collection == collection]
        if not links:
            diagnostic = {"reason": "no_identity_link", "collection": collection}
            if diagnostic not in self.diagnostics:
                self.diagnostics.append(diagnostic)
        return links

    def _table_scope(self, collection: str) -> tuple[sql.Composable, list[Any]]:
        table = (TABLES[collection][0] if collection in TABLES else collection)
        if collection == "document_chunks":
            base: sql.Composable = sql.SQL(
                "FROM rockygpt_v2.document_chunks t "
                "JOIN rockygpt_v2.documents d ON d.id=t.document_id "
                "JOIN rockygpt_v2.sources s ON s.id=d.source_id "
                "WHERE d.dataset_version_id=%s::uuid"
            )
        else:
            join = (sql.SQL(" JOIN rockygpt_v2.shuttle_routes r ON r.id=t.route_id "
                            "AND r.dataset_version_id=t.dataset_version_id")
                    if collection == "shuttle" else sql.SQL(""))
            base = sql.SQL(
                "FROM rockygpt_v2.{table} t JOIN rockygpt_v2.sources s ON s.id=t.source_id "
                "{join} WHERE t.dataset_version_id=%s::uuid"
            ).format(table=sql.Identifier(table), join=join)
        params: list[Any] = [self.data.dataset["id"]]
        if self.entity is not None:
            alternatives = []
            for link in self._links(collection):
                condition: sql.Composable = sql.SQL(
                    "(s.source_key=%s AND t.source_record_key=ANY(%s)"
                )
                values: list[Any] = [link.source_key, link.source_record_keys]
                if link.source_record_ids:
                    condition += sql.SQL(" AND t.id::text=ANY(%s)")
                    values.append(link.source_record_ids)
                elif collection == "events":
                    condition += sql.SQL(
                        " AND (SELECT count(*) FROM rockygpt_v2.campus_events duplicate "
                        "WHERE duplicate.dataset_version_id=t.dataset_version_id "
                        "AND duplicate.source_id=t.source_id "
                        "AND duplicate.source_record_key=t.source_record_key)=1"
                    )
                alternatives.append(condition + sql.SQL(")"))
                params.extend(values)
            base += sql.SQL(" AND ({})").format(
                sql.SQL(" OR ").join(alternatives) if alternatives else sql.SQL("FALSE")
            )
        return base, params

    @staticmethod
    def _expression(collection: str, key: str) -> sql.Composable:
        if key == "source_key":
            return sql.SQL("to_jsonb(s.source_key)")
        if key == "date":
            if collection in {"events", "calendar"}:
                return sql.SQL("to_jsonb((t.starts_at AT TIME ZONE 'America/New_York')::date)")
            return sql.SQL("to_jsonb(t.valid_from)")
        if collection == "shuttle" and key in {"route", "service_day"}:
            return sql.SQL("to_jsonb(r)->{}").format(
                sql.Literal("name" if key == "route" else key)
            )
        return sql.SQL("to_jsonb(t)->{}").format(sql.Literal(key))

    def _filtered_scope(
        self, collection: str, filters: dict[str, Any],
    ) -> tuple[sql.Composable, list[Any]]:
        base, params = self._table_scope(collection)
        for key, value in filters.items():
            expression = self._expression(collection, key)
            base += sql.SQL(" AND nullif({}, 'null'::jsonb) IS NOT DISTINCT FROM %s::jsonb").format(
                expression
            )
            params.append(json.dumps(value) if value is not None else None)
        return base, params

    def _diagnose(self, collection: str) -> None:
        if self.entity is None:
            return
        for link in self._links(collection):
            if collection in ARTIFACT_COLLECTIONS:
                rows = [r for r in self._artifact_records(collection)
                        if r["source_key"] == link.source_key]
                found = {r.get("source_record_key") for r in rows}
                missing = [key for key in link.source_record_keys if key not in found]
                ambiguous: list[str] = []
                missing_ids: list[str] = []
            else:
                table = TABLES[collection][0]
                rows = self.data._fetch(sql.SQL(
                    "SELECT t.id::text AS id, t.source_record_key "
                    "FROM rockygpt_v2.{table} t JOIN rockygpt_v2.sources s ON s.id=t.source_id "
                    "WHERE t.dataset_version_id=%s::uuid AND s.source_key=%s "
                    "AND t.source_record_key=ANY(%s)"
                ).format(table=sql.Identifier(table)),
                    (self.data.dataset["id"], link.source_key, link.source_record_keys))
                eligible = [r for r in rows if not link.source_record_ids
                            or r["id"] in link.source_record_ids]
                found = {r["source_record_key"] for r in eligible}
                missing = [key for key in link.source_record_keys if key not in found]
                missing_ids = [key for key in (link.source_record_ids or [])
                               if key not in {r["id"] for r in eligible}]
                counts = Counter(r["source_record_key"] for r in rows)
                ambiguous = [key for key, count in counts.items() if count > 1]
                if link.source_record_ids or collection != "events":
                    ambiguous = []
            for reason, values in (("broken_identity_link", missing),
                                   ("missing_record_id", missing_ids),
                                   ("ambiguous_record_key", ambiguous)):
                if values:
                    self.diagnostics.append({"reason": reason, "collection": collection,
                                             "source_key": link.source_key,
                                             "count": len(values), "references": values[:50],
                                             "references_truncated": len(values) > 50})

    def _artifact_records(self, collection: str) -> list[dict[str, Any]]:
        # Parsed once per request: a page read also diagnoses the same links.
        if collection not in self._artifact_cache:
            try:
                self._artifact_cache[collection] = self.data._load_artifact_records(collection)
            except (AttributeError, KeyError, TypeError, ValueError):
                diagnostic = {"reason": "artifact_projection_unavailable",
                              "collection": collection,
                              "message": "Inspect the original published source artifact instead."}
                if diagnostic not in self.diagnostics:
                    self.diagnostics.append(diagnostic)
                return []
        records = self._artifact_cache[collection]
        if self.entity is not None:
            links = self._links(collection)
            records = [record for record in records if any(
                record["source_key"] == link.source_key
                and record.get("source_record_key") in link.source_record_keys for link in links
            )]
        return records

    def _record(self, collection: str, row: dict[str, Any]) -> dict[str, Any]:
        original = row["record"]
        source = row.get("source") or {}
        inherited = row.get("document") or {}
        fields = {key: value for key, value in original.items() if key not in META_FIELDS}
        result = {
            "id": f"{collection}:{original['id']}", "collection": collection,
            "source_record_id": str(original["id"]),
            "source_record_key": original.get("source_record_key"),
            "title": _title(original, collection), "fields": fields,
            "source_key": source.get("source_key"), "source_id": source.get("id"),
            "source_title": source.get("title"), "trust_tier": source.get("trust_tier"),
            "url": (original.get("source_url") if collection == "campus_hours"
                    and str(original.get("source_url", "")).startswith("https://")
                    else source.get("canonical_url")),
            "collected_at": original.get("collected_at", inherited.get("collected_at")),
            "valid_from": original.get("valid_from"), "valid_until": original.get("valid_until"),
            "content_hash": original.get("content_hash"),
            "raw_record": original,
        }
        url = fields.get("website_url" if collection == "clubs" else "event_url")
        if collection in {"clubs", "events"} and isinstance(url, str) and url:
            from rockygpt_brain.retrieval.processing import ARCHWAY_FIELDS, archway_artifact_index

            if collection not in self._archway_cache:
                self._archway_cache[collection] = archway_artifact_index(
                    collection, self.data._artifact(collection))
            matched = self._archway_cache[collection].get(url)
            if matched:
                index, item = matched
                # Keep the SQL row untouched; the exact published artifact item
                # supplies fields the database's compact table does not store.
                supplemental = {key: item[key] for key in ARCHWAY_FIELDS[collection] if key in item}
                fields.update(supplemental)
                result.update(artifact_key=collection, artifact_path=[str(index)],
                              supplemental_fields=supplemental)
        if collection == "programs":
            from rockygpt_brain.retrieval.processing import (
                CATALOG_PROGRAM_FIELDS,
                catalog_program_index,
            )

            if self._catalog_programs is None:
                self._catalog_programs = catalog_program_index(self.data._artifact("programs"))
            entry = self._catalog_programs.get(str(original.get("source_record_key") or ""))
            if entry:
                path, item = entry
                # The published catalog entry for this exact code supplies the page's
                # displayed fields the program table does not store. An entry that lists
                # its displayed sections shows that a missing field is not published.
                listed = isinstance(item.get("catalogSections"), list)
                supplemental = {key: item.get(key) for key in CATALOG_PROGRAM_FIELDS
                                if listed or key in item}
                if supplemental:
                    fields.update(supplemental)
                    result.update(artifact_key="programs", artifact_path=path,
                                  supplemental_fields=supplemental)
        if collection == "menu" and menu_occurrence(result):
            if self._menu_cache is None:
                self._menu_cache = menu_artifact_index(self.data._artifact("menu-week"))
            supplemental = supplement_menu(result, self._menu_cache)
            fields.update(supplemental)
            result["supplemental_fields"] = supplemental
        if collection == "shuttle":
            result["route"] = row.get("route")
        if collection == "documents":
            result["navigation"] = [{"label": "Document passages", "collection": "document_chunks",
                                     "filters": {"document_id": str(original["id"])}}]
        if collection == "shuttle_routes":
            result["navigation"] = [{"label": "Shuttle trips", "collection": "shuttle",
                                     "filters": {"route_id": str(original["id"])}}]
        return result

    def _selection(self, collection: str, *, full: bool) -> sql.Composable:
        original: sql.Composable = sql.SQL("to_jsonb(t)")
        if collection == "document_chunks":
            # Avoid serializing the generated search vector or repeatedly copying the
            # complete parent document just to display a page of passage titles.
            original = sql.SQL(
                "jsonb_build_object('id',t.id,'document_id',t.document_id,"
                "'chunk_index',t.chunk_index,'metadata',t.metadata,'content_hash',t.content_hash)"
            )
            if full:
                original += sql.SQL(" || jsonb_build_object('content',t.content)")
        elif not full and collection == "documents":
            original = sql.SQL(
                "jsonb_build_object('id',t.id,'source_id',t.source_id,"
                "'dataset_version_id',t.dataset_version_id,'title',t.title,"
                "'metadata',t.metadata,'collected_at',t.collected_at)"
            )
        extras = (sql.SQL(", jsonb_build_object('collected_at',d.collected_at) AS document")
                  if collection == "document_chunks" else (
            sql.SQL(", to_jsonb(r) AS route") if collection == "shuttle" else sql.SQL("")
        ))
        return sql.SQL("SELECT {} AS record, to_jsonb(s) AS source {} ").format(original, extras)

    def browse(
        self, collection: str, filters: dict[str, Any], group_by: str | None,
        offset: int, limit: int,
    ) -> dict[str, Any]:
        self.validate(collection, filters, group_by)
        self._diagnose(collection)
        if collection == "artifacts":
            if self.entity is not None:
                raise HTTPException(422, "Artifacts do not have direct identity links")
            rows = self.data._fetch(
                "SELECT artifact_key, content_hash, created_at FROM rockygpt_v2.release_artifacts "
                "WHERE dataset_version_id=%s::uuid ORDER BY artifact_key LIMIT %s OFFSET %s",
                (self.data.dataset["id"], limit, offset),
            )
            total = self.data._fetch(
                "SELECT count(*) AS total FROM rockygpt_v2.release_artifacts "
                "WHERE dataset_version_id=%s::uuid", (self.data.dataset["id"],),
            )[0]["total"]
            records = [{"id": f"artifacts:{r['artifact_key']}", "title": r["artifact_key"],
                        "collection": "artifacts", **r} for r in rows]
            return {"mode": "records", "records": records, **_page(records, total, offset, limit),
                    "diagnostics": self.diagnostics}
        if collection in ARTIFACT_COLLECTIONS:
            records = self._artifact_matches(collection, filters)
            if group_by:
                counts = Counter(json.dumps(
                    r.get(group_by) if group_by == "source_key" else r["fields"].get(group_by),
                    sort_keys=True,
                ) for r in records)
                groups = [{"value": json.loads(key), "label": str(json.loads(key))
                           if json.loads(key) is not None else "Not published", "count": count}
                          for key, count in sorted(counts.items())]
                selected = groups[offset:offset + limit]
                return {"mode": "groups", "groups": selected,
                        **_page(selected, len(groups), offset, limit),
                        "diagnostics": self.diagnostics}
            selected = [self.summary(r) for r in records[offset:offset + limit]]
            return {"mode": "records", "records": selected,
                    **_page(selected, len(records), offset, limit),
                    "diagnostics": self.diagnostics}
        base, params = self._filtered_scope(collection, filters)
        if group_by:
            expression = self._expression(collection, group_by)
            groups = self.data._fetch(sql.SQL(
                "SELECT value, count AS count, count(*) OVER() AS total FROM "
                "(SELECT nullif({expression}, 'null'::jsonb) AS value, count(*) AS count {base} "
                "GROUP BY 1) grouped ORDER BY {ordering} LIMIT %s OFFSET %s"
            ).format(expression=expression, base=base,
                     ordering=sql.SQL(
                         "CASE value#>>'{}' WHEN 'Breakfast' THEN 0 WHEN 'Brunch' THEN 1 "
                         "WHEN 'Lunch' THEN 2 WHEN 'Dinner' THEN 3 ELSE 4 END, value NULLS LAST"
                     ) if group_by == "meal" else sql.SQL("value NULLS LAST")),
                (*params, limit, offset))
            total = int(groups[0]["total"]) if groups else self.data._fetch(sql.SQL(
                "SELECT count(*) AS total FROM (SELECT {expression} {base} GROUP BY 1) grouped"
            ).format(expression=expression, base=base), tuple(params))[0]["total"]
            output = [{"value": r["value"], "label": str(r["value"])
                       if r["value"] is not None else "Not published", "count": r["count"]}
                      for r in groups]
            return {"mode": "groups", "groups": output, **_page(output, total, offset, limit),
                    "diagnostics": self.diagnostics}
        total = self.data._fetch(sql.SQL("SELECT count(*) AS total ") + base,
                                tuple(params))[0]["total"]
        rows = self.data._fetch(self._selection(collection, full=False) + base
                               + sql.SQL(" ORDER BY {} LIMIT %s OFFSET %s").format(
                                   self._ordering(collection)), (*params, limit, offset))
        records = [self.summary(self._record(collection, row)) for row in rows]
        return {"mode": "records", "records": records, **_page(records, total, offset, limit),
                "diagnostics": self.diagnostics}

    @staticmethod
    def _ordering(collection: str) -> sql.Composable:
        """Deterministic within an immutable release: title, then original row ID."""
        return (
            sql.SQL("t.document_id, t.chunk_index, t.id") if collection == "document_chunks"
            else sql.SQL("t.route_id, t.sequence, t.id") if collection == "shuttle"
            else sql.SQL("coalesce(to_jsonb(t)->>'name', to_jsonb(t)->>'title', "
                         "to_jsonb(t)->>'fact_key', to_jsonb(t)->>'source_record_key', "
                         "t.id::text), t.id")
        )

    def _artifact_matches(
        self, collection: str, filters: dict[str, Any],
    ) -> list[dict[str, Any]]:
        records = [r for r in self._artifact_records(collection) if all(
            (r.get(key) if key == "source_key" else r["fields"].get(key)) == value
            for key, value in filters.items()
        )]
        return sorted(records, key=lambda r: (r["title"].casefold(), r["id"]))

    def records(
        self, collection: str, filters: dict[str, Any], offset: int, limit: int,
    ) -> dict[str, Any]:
        """One page of complete records in browse order: one query, not one per row."""
        self.validate(collection, filters)
        if collection == "artifacts":
            raise HTTPException(422, "Artifacts are browsed, not read as records")
        self._diagnose(collection)
        if collection in ARTIFACT_COLLECTIONS:
            matches = self._artifact_matches(collection, filters)
            page = [self._artifact_record(collection, r) for r in matches[offset:offset + limit]]
            return {"records": page, **_page(page, len(matches), offset, limit)}
        base, params = self._filtered_scope(collection, filters)
        total = self.data._fetch(sql.SQL("SELECT count(*) AS total ") + base,
                                tuple(params))[0]["total"]
        rows = self.data._fetch(self._selection(collection, full=True) + base
                               + sql.SQL(" ORDER BY {} LIMIT %s OFFSET %s").format(
                                   self._ordering(collection)), (*params, limit, offset))
        page = [self._record(collection, row) for row in rows]
        return {"records": page, **_page(page, total, offset, limit)}

    @staticmethod
    def summary(record: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in record.items()
                if key not in {"fields", "raw_record", "content", "coverage"}}

    def record(self, collection: str, record_id: str) -> dict[str, Any]:
        self.validate(collection, {})
        self._diagnose(collection)
        if not record_id.startswith(f"{collection}:"):
            raise HTTPException(422, "Record ID does not match collection")
        original_id = record_id[len(collection) + 1:]
        if collection == "artifacts":
            if self.entity is not None:
                raise HTTPException(422, "Artifacts do not have direct identity links")
            rows = self.data._fetch(
                "SELECT artifact_key, content_hash, created_at FROM rockygpt_v2.release_artifacts "
                "WHERE dataset_version_id=%s::uuid AND artifact_key=%s",
                (self.data.dataset["id"], original_id),
            )
            if not rows:
                raise HTTPException(404, "Campus record was not found")
            return {"id": record_id, "collection": collection, "title": original_id,
                    "fields": rows[0], "artifact_path": []}
        if collection in ARTIFACT_COLLECTIONS:
            record = next((r for r in self._artifact_records(collection)
                           if r["id"] == record_id), None)
            if record is None:
                raise HTTPException(404, "Campus record was not found")
            return self._artifact_record(collection, record)
        base, params = self._table_scope(collection)
        rows = self.data._fetch(self._selection(collection, full=True) + base + sql.SQL(
            " AND t.id::text=%s LIMIT 1"
        ), (*params, original_id))
        if not rows:
            raise HTTPException(404, "Campus record was not found in this scope")
        return self._record(collection, rows[0])

    def _artifact_record(self, collection: str, record: dict[str, Any]) -> dict[str, Any]:
        """A parsed artifact record with its original published item and exact path."""
        original_id = record["id"][len(collection) + 1:]
        artifact = {"faculty": "faculty", "courses": "courses",
                    "program_requirements": "programs", "buildings": "campus-buildings",
                    "schools": "campus-schools", "subjects": "course-subjects",
                    "graduation_plans": "graduation-plans"}[collection]
        path = (original_id.split(".") if collection == "program_requirements"
                else [original_id])
        if collection == "program_requirements":
            path = ["schools", path[0], "majors", path[1], "requirements", path[2]]
        raw = self.data._artifact(artifact)
        if collection == "graduation_plans":
            path = ["plans", str(next(index for index, item in enumerate(raw["plans"])
                                      if str(item.get("id")) == original_id))]
        if collection in {"buildings", "schools", "subjects"}:
            key = {"buildings": "concept3d_id", "schools": "section",
                   "subjects": "code"}[collection]
            path = [collection, str(next(
                index for index, item in enumerate(raw[collection])
                if str(item.get(key)) == original_id))]
        for segment in path:
            raw = raw[int(segment)] if isinstance(raw, list) else raw[segment]
        return {**record, "fields": raw, "source_record_id": original_id,
                "artifact_key": artifact, "artifact_path": path, "raw_record": raw}

    def reference(
        self, collection: str, source_key: str, source_record_key: str,
        source_record_id: str | None,
    ) -> dict[str, Any]:
        self.validate(collection, {})
        if collection in ARTIFACT_COLLECTIONS:
            records = [r for r in self._artifact_records(collection)
                       if r["source_key"] == source_key
                       and r.get("source_record_key") == source_record_key
                       and (source_record_id is None
                            or r["id"] == f"{collection}:{source_record_id}")]
            record_ids = [r["id"] for r in records]
        elif collection in TABLES:
            base, params = self._table_scope(collection)
            base += sql.SQL(" AND s.source_key=%s AND t.source_record_key=%s")
            params.extend([source_key, source_record_key])
            if source_record_id is not None:
                base += sql.SQL(" AND t.id::text=%s")
                params.append(source_record_id)
            records = self.data._fetch(sql.SQL("SELECT t.id::text AS id ") + base
                                       + sql.SQL(" ORDER BY t.id LIMIT 2"), tuple(params))
            record_ids = [f"{collection}:{r['id']}" for r in records]
        else:
            raise HTTPException(422, "This collection requires an exact record ID")
        if not record_ids:
            raise HTTPException(404, "Campus source reference was not found")
        if len(record_ids) > 1:
            raise HTTPException(422, "Ambiguous source reference; supply source_record_id")
        return self.record(collection, record_ids[0])

    def artifact_value(
        self, artifact_key: str, path: list[str], offset: int, limit: int,
    ) -> dict[str, Any]:
        """Page the JSON subtree in PostgreSQL; never download the complete raw artifact."""
        if self.entity is not None:
            raise HTTPException(422, "Artifact values do not have direct identity links")
        subtree = sql.SQL(
            "WITH selected AS (SELECT payload #> %s::text[] AS value, content_hash, created_at "
            "FROM rockygpt_v2.release_artifacts WHERE dataset_version_id=%s::uuid "
            "AND artifact_key=%s) "
        )
        params = (path, self.data.dataset["id"], artifact_key)
        rows = self.data._fetch(subtree + sql.SQL(
            "SELECT value IS NOT NULL AS present, jsonb_typeof(value) AS kind, "
            "CASE jsonb_typeof(value) WHEN 'object' THEN "
            "(SELECT count(*) FROM jsonb_object_keys(value)) "
            "WHEN 'array' THEN jsonb_array_length(value) ELSE 0 END AS total, "
            "CASE WHEN jsonb_typeof(value) IN ('object','array') THEN NULL "
            "ELSE value END AS scalar, content_hash, created_at FROM selected"), params)
        if not rows or not rows[0]["present"]:
            raise HTTPException(404, "Artifact value was not found")
        row = rows[0]
        children = []
        if row["kind"] in {"object", "array"}:
            entries = self.data._fetch(subtree + sql.SQL(
                "SELECT key, jsonb_typeof(child) AS kind, "
                "CASE jsonb_typeof(child) WHEN 'object' THEN "
                "(SELECT count(*) FROM jsonb_object_keys(child)) "
                "WHEN 'array' THEN jsonb_array_length(child) ELSE 0 END AS count, "
                "CASE WHEN jsonb_typeof(child) IN ('object','array') THEN NULL "
                "ELSE left(child::text, 160) END AS preview, "
                "CASE WHEN jsonb_typeof(child) IN ('object','array') THEN NULL "
                "ELSE child END AS scalar, "
                "coalesce(child->>'name',child->>'title',child->>'code',key) AS label "
                "FROM selected CROSS JOIN LATERAL ("
                "SELECT key, value AS child, 0::bigint AS ordinal FROM jsonb_each("
                "CASE WHEN jsonb_typeof(selected.value)='object' THEN selected.value "
                "ELSE '{}'::jsonb END) UNION ALL "
                "SELECT (ordinality-1)::text AS key, value AS child, ordinality AS ordinal "
                "FROM jsonb_array_elements(CASE WHEN jsonb_typeof(selected.value)='array' "
                "THEN selected.value ELSE '[]'::jsonb END) WITH ORDINALITY "
                ") entries ORDER BY ordinal, key LIMIT %s OFFSET %s"), (*params, limit, offset))
            for entry in entries:
                label = (entry["key"] if row["kind"] == "object" else
                         f"[{entry['key']}] {entry['label']}" if entry["label"] != entry["key"]
                         else f"[{entry['key']}]")
                preview = (f"{entry['count']} fields" if entry["kind"] == "object" else
                           f"{entry['count']} items" if entry["kind"] == "array" else
                           "null · no value stored" if entry["kind"] == "null" else
                           entry["preview"] or "")
                scalar = entry.pop("scalar", None)
                leaf = ({"value": scalar} if entry["kind"] not in {"object", "array"} else
                        {"value": {} if entry["kind"] == "object" else []}
                        if entry["count"] == 0 else {})
                children.append({**entry, "path": [*path, entry["key"]],
                                 "label": label, "preview": preview, **leaf})
        value = ({"value": row["scalar"]} if row["kind"] not in {"object", "array"} else
                 {"value": {} if row["kind"] == "object" else []} if row["total"] == 0 else {})
        return {"artifact_key": artifact_key, "path": path, "kind": row["kind"],
                "children": children, **value,
                "content_hash": row["content_hash"], "created_at": row["created_at"],
                "timestamp_meaning": "artifact_created_at_not_source_verification",
                **_page(children, row["total"], offset, limit)}
