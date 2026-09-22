"""Entity-first development graph over published identities and catalog courses.

Source rows describe entities; only published identity relationships become edges.
Catalog identity is source-scoped and never inferred from a display name.
"""
from __future__ import annotations

import json
from collections import defaultdict
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import HTTPException

from rockygpt_brain.retrieval.graph import GraphData
from rockygpt_brain.retrieval.profiles import IdentityRegistry


def course_id(source: str, key: str) -> str:
    return str(uuid5(NAMESPACE_URL, json.dumps(["rockygpt", "course", source, key])))


class KnowledgeGraph:
    def __init__(self, data: Any) -> None:
        self.data = data
        self.registry = IdentityRegistry.model_validate(data._artifact("campus-identities"))
        self.reader = GraphData(data)
        self.courses = self.reader._artifact_records("courses")
        self.course_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for course in self.courses:
            self.course_groups[(course["source_key"], course["source_record_key"])].append(course)

    def index(self) -> dict[str, Any]:
        nodes = [{"id": str(entity.id), "kind": entity.kind, "name": entity.name,
                  "aliases": entity.aliases} for entity in self.registry.entities]
        diagnostics = list(self.reader.diagnostics)
        for (source, key), records in self.course_groups.items():
            if len(records) != 1:
                diagnostics.append({"reason": "ambiguous_course_identity", "record": key,
                                    "collection": "courses", "source_key": source})
                continue
            course = records[0]
            nodes.append({"id": course_id(source, key), "kind": "course",
                          "name": course["title"], "aliases": [key]})
        known = {node["id"] for node in nodes}
        edges = []
        for entity in self.registry.entities:
            for relationship in entity.relationships:
                target = (str(relationship.target_entity_id)
                          if relationship.target_entity_id else None)
                if relationship.target_record:
                    ref = relationship.target_record
                    records = self.course_groups.get((ref.source_key, ref.source_record_key), [])
                    if len(records) == 1 and (not ref.source_record_id or
                            records[0]["id"] == f"courses:{ref.source_record_id}"):
                        target = course_id(ref.source_key, ref.source_record_key)
                if target not in known:
                    diagnostics.append({"reason": "unresolved_relationship", "entity": entity.name,
                                        "relationship": relationship.type})
                    continue
                evidence = [item.model_dump(mode="json", exclude_none=True)
                            for item in relationship.evidence]
                edges.append({"source": str(entity.id), "target": target,
                              "type": relationship.type, "evidence": evidence})
        coverage = self.data._artifact("campus-identity-coverage")
        if isinstance(coverage, dict) and isinstance(coverage.get("unresolved"), list):
            diagnostics.extend(item for item in coverage["unresolved"] if isinstance(item, dict))
        return {"nodes": nodes, "edges": edges, "diagnostics": diagnostics}

    def properties(self, entity_id: UUID, collection: str | None, offset: int,
                   limit: int) -> dict[str, Any]:
        entity = next((item for item in self.registry.entities if item.id == entity_id), None)
        groups = []
        if entity:
            reader = GraphData(self.data, entity_id)
            collections: list[str] = list(dict.fromkeys(link.collection for link in entity.links))
            if collection is not None and collection not in collections:
                raise HTTPException(422, "Collection is not linked to this entity")
            for key in ([collection] if collection else collections):
                page = reader.browse(key, {}, None, offset, limit)
                records = []
                for row in page["records"]:
                    try:
                        records.append(reader.record(key, row["id"]))
                    except HTTPException as error:
                        if error.status_code != 404:
                            raise
                        reader.diagnostics.append({"reason": "linked_property_unavailable",
                                                   "collection": key, "record": row["id"]})
                groups.append({"collection": key, "records": [self._properties(r) for r in records],
                               "total": page["total"], "next_offset": page["next_offset"]})
            return {"entity_id": str(entity_id), "groups": groups,
                    "diagnostics": reader.diagnostics}
        for (source, key), records in self.course_groups.items():
            if len(records) == 1 and course_id(source, key) == str(entity_id):
                if collection not in {None, "courses"}:
                    raise HTTPException(422, "Collection is not linked to this entity")
                record = self.reader.record("courses", records[0]["id"])
                return {"entity_id": str(entity_id), "groups": [{"collection": "courses",
                        "records": [self._properties(record)] if offset == 0 else [],
                        "total": 1, "next_offset": None}],
                        "diagnostics": []}
        raise HTTPException(404, "Campus entity was not found")

    @staticmethod
    def _properties(record: dict[str, Any]) -> dict[str, Any]:
        # Retain separate source values, dates and provenance; never choose a winner.
        return {key: value for key, value in record.items()
                if key not in {"raw_record", "navigation", "artifact_path"}}
