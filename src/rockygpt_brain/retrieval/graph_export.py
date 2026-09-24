"""Complete graph export from the published registry and canonical catalog layer.

No UI state, browse pagination, semantic search, or inferred/reverse edges.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from copy import deepcopy
from typing import Any

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder

from rockygpt_brain.retrieval.knowledge import KnowledgeGraph


def payload_hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False).encode()).hexdigest()


def export_graph(data: Any, snapshot: dict[str, Any]) -> dict[str, Any]:
    registry = data._artifact("campus-identities")
    courses = data._artifact("courses")
    coverage = data._artifact("campus-identity-coverage")
    course_identities = data._artifact("catalog-course-identities")
    requirement_groups = data._artifact("program-requirement-groups")
    if data.identity_readiness().get("status") != "available" or not isinstance(courses, dict):
        raise HTTPException(503, "Published graph inputs unavailable; no partial export produced")
    # Read source provenance within the same transaction as the artifacts, rather
    # than using the connection bootstrap's earlier source-catalog snapshot.
    data.sources = {row["id"]: row for row in data._fetch(
        "SELECT s.id::text, s.source_key, s.title, s.canonical_url, s.trust_tier, "
        "s.freshness_sla_hours, r.status AS provenance_status, r.completed_at "
        "FROM rockygpt_v2.sources s LEFT JOIN rockygpt_v2.source_runs r "
        "ON r.source_key=s.source_key AND r.dataset_version_id=%s::uuid "
        "WHERE s.trust_tier IN ('official_primary', 'official_secondary')", (data.dataset["id"],),
    )}
    hashes = {"campus-identities": payload_hash(registry), "courses": payload_hash(courses),
              "campus-identity-coverage": payload_hash(coverage) if coverage is not None else None,
              "catalog-course-identities": payload_hash(course_identities)
              if course_identities is not None else None,
              "program-requirement-groups": payload_hash(requirement_groups)
              if requirement_groups is not None else None}
    graph = KnowledgeGraph(data)
    index = graph.index()
    # The canonical loader may suppress an unreadable catalog or missing source.
    # A complete download must fail rather than claim that the resulting subset is all nodes.
    if graph.reader.diagnostics or len(graph.courses) != len(courses):
        raise HTTPException(503, "Published course identities could not be fully read")
    metadata = data._fetch(
        "SELECT id::text, version, status, created_at, activated_at, source_commit_sha, "
        "quality_summary FROM rockygpt_v2.dataset_versions WHERE id=%s::uuid",
        (data.dataset["id"],),
    )
    if len(metadata) != 1 or metadata[0]["status"] != "active":
        raise HTTPException(409, "Campus release changed; retry the download")
    manifests = data._fetch(
        "SELECT artifact_key, content_hash, created_at FROM rockygpt_v2.release_artifacts "
        "WHERE dataset_version_id=%s::uuid ORDER BY artifact_key", (data.dataset["id"],),
    )
    nodes = deepcopy(index["nodes"])
    node_ids = {node["id"] for node in nodes}
    identities = {str(entity.id): (i, entity) for i, entity in enumerate(graph.registry.entities)}
    course_records = {graph.course_identity(source, key): rows[0]
                      for (source, key), rows in graph.course_groups.items() if len(rows) == 1}
    published_courses = {course["id"]: index for index, course in enumerate(
        course_identities["courses"]) if isinstance(course, dict)
        and isinstance(course.get("id"), str)} if graph.published_courses is not None else {}
    for node in nodes:
        if node["id"] in identities:
            i, _ = identities[node["id"]]
            node["identity_origin"] = "published_registry"
            node["source_bindings"] = deepcopy(registry["entities"][i]["links"])
            node["provenance"] = {"artifact_key": "campus-identities",
                                  "payload_sha256": snapshot["identity_hash"],
                                  "path": ["entities", i]}
        else:
            record = course_records[node["id"]]
            original_id = record["id"].removeprefix("courses:")
            node["source_bindings"] = [{"collection": "courses",
                "source_key": record["source_key"],
                "source_record_keys": [record["source_record_key"]],
                "source_record_ids": [original_id]}]
            if node["id"] in published_courses:
                node["identity_origin"] = "published_catalog_course"
                node["provenance"] = {"artifact_key": "catalog-course-identities",
                                      "payload_sha256": hashes["catalog-course-identities"],
                                      "path": ["courses", published_courses[node["id"]]]}
            else:
                node["identity_origin"] = "source_scoped_catalog_course"
                node["provenance"] = {"artifact_key": "courses",
                                      "payload_sha256": hashes["courses"], "path": [original_id]}

    edges, declarations, unresolved = [], [], []
    for i, entity in enumerate(graph.registry.entities):
        for j, relationship in enumerate(entity.relationships):
            published = deepcopy(registry["entities"][i]["relationships"][j])
            identifier = f"relationship:{entity.id}:{j}"
            locator = {"artifact_key": "campus-identities",
                       "payload_sha256": snapshot["identity_hash"],
                       "path": ["entities", i, "relationships", j]}
            target = graph.relationship_target(relationship)
            declaration = {"id": identifier, "source": str(entity.id),
                           "published": published, "provenance": locator,
                           "resolved_target": target if target in node_ids else None}
            declarations.append(declaration)
            if target in node_ids:
                edges.append({"id": identifier, "source": str(entity.id), "target": target,
                              "type": relationship.type, "evidence": published["evidence"],
                              "provenance": locator})
            else:
                ref = relationship.target_record
                candidates = (graph.course_groups.get((ref.source_key, ref.source_record_key), [])
                              if ref else [])
                unresolved.append({**declaration, "reason": "unresolved_relationship",
                    "candidate_records": [{"id": row["id"], "source_key": row["source_key"],
                                           "source_record_key": row["source_record_key"]}
                                          for row in candidates]})
    # Keep the canonical index's diagnostics in full, and add fully addressable
    # declarations above instead of guessing targets for unresolved statements.
    diagnostics = deepcopy(index["diagnostics"])
    records, record_edges = contextual_records(
        requirement_groups, node_ids, hashes["program-requirement-groups"], diagnostics,
    )
    if coverage is None:
        diagnostics.append({"reason": "identity_coverage_unavailable"})
    elif not isinstance(coverage, dict) or not isinstance(coverage.get("unresolved"), list):
        diagnostics.append({"reason": "identity_coverage_invalid", "raw_report_retained": True})
    exported = {
        "schema": "rockygpt.published-campus-knowledge-graph", "schema_version": 2,
        "exported_at": data.now.isoformat(),
        "snapshot": {**snapshot, "dataset": metadata[0], "artifacts": manifests,
                     "graph_input_hashes": hashes},
        "completeness": {
            "scope": "entire_published_canonical_graph", "paginated": False,
            "ui_filtered": False, "truncated": False,
            "all_canonical_nodes": True, "all_published_relationship_declarations": True,
            "all_resolved_directed_edges": True, "all_identity_source_bindings": True,
            "coverage_report": "included_in_full" if coverage is not None else "unavailable",
            "contextual_records": "included_in_full" if requirement_groups is not None
            else "not_published_in_this_release",
            "source_record_bodies": "not_embedded; published_evidence_references_retained_in_full",
        },
        "semantics": {
            "edges": "Resolved published directed relationships; no reverse or inferred edges.",
            "published_relationships": "Every declaration, including unresolved targets/repeats.",
            "source_bindings": "Match collection and source_key, membership in source_record_keys, "
                               "and, when supplied, membership in source_record_ids. Arrays are "
                               "independent constraints; never zip or match by names.",
            "course_ids": "Published by the data repository in catalog-course-identities; releases "
                          'before it derive the same UUIDv5(NAMESPACE_URL, json.dumps(["rockygpt", '
                          '"course", source, key])). Only unambiguous source-scoped catalog keys '
                          "become canonical nodes.",
            "contextual_records": "Requirement groups: one record per distinct published "
                                  "requirement section, keeping its rule tree, counts and notes. "
                                  "record_edges connect programs to groups (requirement_group) "
                                  "and groups to catalog courses (requirement_option); an option "
                                  "is never an unconditional requirement.",
            "provenance": "Artifact paths and hashes locate published graph assertions. Evidence "
                          "references are exact published selectors, not new factual verification.",
            "profile_course": "Undated profile course list, not a current teaching assignment.",
            "listed_faculty": "Catalog Program Faculty field listing; not a convenership, "
                              "appointment or current teaching assignment.",
            "office_at": "A person's published office room has the building's reviewed room "
                         "prefix, or a person reviewed its exact text as naming the building; "
                         "a location, not a school or appointment.",
            "located_at": "An office's published room has the building's reviewed room prefix, "
                          "a person reviewed its exact text as naming the building, or the "
                          "building's record carries a reviewed official statement placing "
                          "it; not a complete building directory.",
            "part_of": "A program's catalog school (or its reviewed legacy name) or a person's "
                       "faculty-profile school names the school; retired and split-school "
                       "records are not placed.",
            "includes_course": "A catalog course's own code starts with the subject's code; "
                               "the subject is not a department, program or school.",
            "coverage": "Published report and resolution diagnostics, not a new source audit. "
                        "Issue counts are not counts of unique missing entities.",
            "time": "exported_at is generation time, not source capture time. No date filtering.",
            "payload_hashes": "SHA-256 of UTF-8 JSON with sorted keys, compact separators and "
                              "ensure_ascii=False; stored content_hash is retained separately.",
        },
        "counts": {
            "nodes": len(nodes), "nodes_by_kind": dict(sorted(Counter(
                node["kind"] for node in nodes).items())),
            "edges": len(edges), "relationships_by_type": dict(sorted(Counter(
                edge["type"] for edge in edges).items())),
            "published_relationships": len(declarations),
            "published_relationships_by_type": dict(sorted(Counter(
                item["published"]["type"] for item in declarations).items())),
            "relationship_evidence_references": sum(len(d["published"]["evidence"])
                                                    for d in declarations),
            "registry_entities": len(graph.registry.entities),
            "source_binding_groups": sum(len(node["source_bindings"]) for node in nodes),
            "unresolved_relationships": len(unresolved), "diagnostics": len(diagnostics),
            "contextual_records": len(records),
            "contextual_records_by_type": dict(sorted(Counter(
                record["record_type"] for record in records).items())),
            "record_edges": len(record_edges),
            "record_edges_by_type": dict(sorted(Counter(
                edge["type"] for edge in record_edges).items())),
            "published_coverage_issues": len(coverage["unresolved"])
                if isinstance(coverage, dict) and isinstance(coverage.get("unresolved"), list)
                else None,
        },
        "nodes": nodes, "edges": edges, "published_relationships": declarations,
        "contextual_records": records, "record_edges": record_edges,
        "unresolved_relationships": unresolved, "diagnostics": diagnostics,
        "identity_registry": deepcopy(registry), "coverage": deepcopy(coverage),
        "source_catalog": deepcopy(sorted(data.sources.values(), key=lambda s: s["source_key"])),
    }
    return dict(jsonable_encoder(exported))


EDGE_PROPERTIES = ("order", "path", "logic", "code")


def contextual_records(
    artifact: Any, node_ids: set[str], artifact_hash: str | None, diagnostics: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Requirement groups as records, and their edges only where both endpoints exist."""
    if artifact is None:
        return [], []
    if not isinstance(artifact, dict) or not isinstance(artifact.get("groups"), list) \
            or not isinstance(artifact.get("edges"), list):
        diagnostics.append({"reason": "requirement_groups_invalid"})
        return [], []
    records = []
    for index, group in enumerate(artifact["groups"]):
        record = deepcopy(group)
        record["locator"] = {"artifact_key": "program-requirement-groups",
                             "payload_sha256": artifact_hash, "path": ["groups", index]}
        records.append(record)
    record_ids = {record["id"] for record in records}
    edges = []
    for index, edge in enumerate(artifact["edges"]):
        ends = []
        for end in (edge.get("source", {}), edge.get("target", {})):
            kind = "record" if "record_id" in end else "entity"
            value = end.get("record_id") if kind == "record" else end.get("entity_id")
            ends.append((kind, value, value in (record_ids if kind == "record" else node_ids)))
        if not all(found for _, _, found in ends):
            diagnostics.append({"reason": "unresolved_record_edge", "type": edge.get("type"),
                                "path": ["edges", index]})
            continue
        edges.append({
            "id": f"record-edge:{index}", "type": edge["type"],
            "source": ends[0][1], "source_kind": ends[0][0],
            "target": ends[1][1], "target_kind": ends[1][0],
            "properties": {key: edge[key] for key in EDGE_PROPERTIES if key in edge},
            "locator": {"artifact_key": "program-requirement-groups",
                        "payload_sha256": artifact_hash, "path": ["edges", index]},
        })
    return records, edges
