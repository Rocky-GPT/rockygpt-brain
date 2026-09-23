"""Entity-first development graph over published identities and catalog courses.

Source rows describe entities; only published identity relationships become edges.
Catalog identity is source-scoped and never inferred from a display name.
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from rockygpt_brain.retrieval.graph import GraphData
from rockygpt_brain.retrieval.profiles import Identity, IdentityRegistry, IdentityRelationship
from rockygpt_brain.retrieval.release_cache import cached

COURSE_IDENTITY_FIELDS = ("id", "source_key", "source_record_key")


def course_id(source: str, key: str) -> str:
    """The original course ID derivation; releases now publish these IDs themselves."""
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
        # The data repository owns course IDs; releases before it published them derive the same.
        published = data._artifact("catalog-course-identities")
        self.published_courses: dict[tuple[str, str], str] | None = None
        if isinstance(published, dict) and isinstance(published.get("courses"), list):
            self.published_courses = {
                (course["source_key"], course["source_record_key"]): course["id"]
                for course in published["courses"]
                if isinstance(course, dict)
                and all(isinstance(course.get(key), str) for key in COURSE_IDENTITY_FIELDS)
            }

    def course_identity(self, source: str, key: str) -> str:
        if self.published_courses is not None and (source, key) in self.published_courses:
            return self.published_courses[(source, key)]
        return course_id(source, key)

    def index(self) -> dict[str, Any]:
        nodes = [{"id": str(entity.id), "kind": entity.kind, "name": entity.name,
                  "aliases": entity.aliases,
                  **({"status": entity.status.state} if entity.status else {})}
                 for entity in self.registry.entities]
        diagnostics = list(self.reader.diagnostics)
        for (source, key), records in self.course_groups.items():
            if len(records) != 1:
                diagnostics.append({"reason": "ambiguous_course_identity", "record": key,
                                    "collection": "courses", "source_key": source})
                continue
            course = records[0]
            nodes.append({"id": self.course_identity(source, key), "kind": "course",
                          "name": course["title"], "aliases": [key]})
        known = {node["id"] for node in nodes}
        edges = []
        for entity in self.registry.entities:
            for relationship in entity.relationships:
                target = self.relationship_target(relationship)
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

    def relationship_target(self, relationship: IdentityRelationship) -> str | None:
        """The same exact target resolution is used by the index and full export."""
        if relationship.target_entity_id:
            return str(relationship.target_entity_id)
        if relationship.target_record:
            ref = relationship.target_record
            records = self.course_groups.get((ref.source_key, ref.source_record_key), [])
            if len(records) == 1 and (not ref.source_record_id or
                    records[0]["id"] == f"courses:{ref.source_record_id}"):
                return self.course_identity(ref.source_key, ref.source_record_key)
        return None


@dataclass(frozen=True)
class ReleaseGraph:
    """One release's registry and graph index, shared read-only by development requests.

    `courses` maps each course node with exactly one catalog record to that record's
    source key, source record key and original record ID.
    """
    registry: IdentityRegistry
    identities: dict[str, Identity]
    index: dict[str, Any]
    nodes: dict[str, dict[str, Any]]
    courses: dict[str, tuple[str, str, str]]


def release_graph(data: Any) -> ReleaseGraph:
    """The active release's graph, built once per release (see `release_cache`).

    The complete export builds its own graph instead: it pins every read to one
    database snapshot.
    """
    def build() -> ReleaseGraph:
        graph = KnowledgeGraph(data)
        index = graph.index()
        return ReleaseGraph(
            registry=graph.registry,
            identities={str(entity.id): entity for entity in graph.registry.entities},
            index=index, nodes={node["id"]: node for node in index["nodes"]},
            courses={graph.course_identity(source, key): (source, key, rows[0]["id"])
                     for (source, key), rows in graph.course_groups.items() if len(rows) == 1},
        )
    return cached(data, "knowledge-graph", build)
