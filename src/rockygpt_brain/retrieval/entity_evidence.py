"""Shared entity facts over the evidence a profile actually delivered.

No new source read or identity inference happens here. Profiles already resolve
exact release links and apply service dates; only those citable records enter the
same property canonicalizer used by the graph API.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from rockygpt_brain.retrieval.projection import PROPERTY_SPECS, valid_value
from rockygpt_brain.retrieval.projection_models import Assertion, Entity, Property, SourceRecord


def profile_facts(
    data: Any, output: dict[str, Any], aliases: list[str] | None = None,
) -> dict[str, Any]:
    """`aliases` are the resolved entity's registry aliases."""
    from rockygpt_brain.retrieval.entity_facts import (
        _lineage,
        canonical_properties,
        reviewed_names,
    )

    entity = output["resolution"]["entity"]
    specs = {spec.collection: spec for spec in PROPERTY_SPECS}
    properties: dict[str, Property] = {}
    sources: list[SourceRecord] = []
    issues: list[dict[str, Any]] = []
    lineage: list[Property] = []
    for record in output["records"]:
        # Related entities and dated offerings are independent subjects, never
        # attributes of the entity whose profile traversed to them.
        if record.get("canonical_entity_id") != entity["id"]:
            continue
        spec = specs.get(record["collection"])
        if spec is None:
            continue
        fields = record["fields"]
        stored = data._seen.get(record["id"], record)
        full = stored["fields"]
        for key, value in stored.get("_entity_lineage_fields", {}).items():
            lineage.append(Property(key=key, label=key, value_type="text", assertions=[
                Assertion(id=f"{record['id']}#{key}", value=value, source_id=record["id"],
                          field_path=[key], limitations=[]),
            ]))
        sources.append(SourceRecord(
            id=record["id"], collection=record["collection"],
            row_id=record["id"].split(":", 1)[-1], source_key=record.get("source_key"),
            source_record_key=record.get("source_record_key"), source_url=record.get("url"),
            artifact_key=stored.get("artifact_key"), artifact_path=stored.get("artifact_path"),
            collected_at=record.get("collected_at"), valid_from=record.get("valid_from"),
            valid_until=record.get("valid_until"), freshness=record["freshness"],
            limitations=[note for note in record.get("limitations", [])
                         if not note.startswith("Linked records disagree on ")],
        ))
        for field in spec.fields:
            if field.source not in fields:
                continue
            value = fields[field.source]
            if value != full.get(field.source):
                issues.append({"reason": "evidence_field_truncated", "record_id": record["id"],
                               "fields": [field.key]})
                continue
            if not valid_value(value, field.value_type):
                issues.append({"reason": "unsupported_field_shape", "record_id": record["id"],
                               "fields": [field.key]})
                continue
            state = record.get("coverage", {}).get("fields", {}).get(field.source)
            assertion = Assertion(
                id=f"{record['id']}#{field.source}", value=value, source_id=record["id"],
                field_path=[field.source], limitations=[],
                publication_status="not_published" if state == "not_published" else "unspecified",
            )
            if field.key not in properties:
                properties[field.key] = Property(key=field.key, label=field.key.replace("_", " "),
                                                  value_type=field.value_type, assertions=[])
            properties[field.key].assertions.append(assertion)
    for section, component in output["components"].items():
        if component["status"] in {"partial", "unavailable"}:
            issues.append({"reason": "profile_component_incomplete", "component": section})
    sources = _lineage([*properties.values(), *lineage], sources)
    named = Entity(id=entity["id"], kind=entity["kind"], name=entity["name"],
                   aliases=aliases or [])
    facts = {
        "schema_version": 3, "projection_version": "entity-facts-1",
        "dataset_version": output["dataset_version"], "entity": entity,
        "properties_complete": not issues,
        "properties": [prop.model_dump(mode="json") for prop in canonical_properties(
            list(properties.values()), sources, named,
            reviewed_names(data, named, list(properties.values())),
        )],
        "sources": [source.model_dump(mode="json") for source in sources], "coverage": issues,
        "scope": "requested_profile_sections_only",
    }
    apply_entity_coverage(data, output, facts)
    return facts


def apply_entity_coverage(data: Any, output: dict[str, Any], facts: dict[str, Any]) -> None:
    """Legacy evidence/section envelopes must not disagree with the shared model."""
    properties = {prop["key"]: prop for prop in facts["properties"]}
    aliases = {"phone": "phones", "office": "offices", "courses": "profile_courses"}
    for component in output.get("components", {}).values():
        for field in component.get("fields", {}):
            prop = properties.get(aliases.get(field, field))
            if prop is None:
                continue
            if not any(assertion["field_path"][0] == field
                       for assertion in prop["assertions"]):
                continue  # This legacy source field itself was not delivered.
            status = prop["status"]
            component["fields"][field] = (
                "not_published" if status == "unknown" else "conflict"
                if status == "conflicting" else "published")
            if status == "conflicting":
                component["conflicts"][field] = [
                    {"value": value["value"], "evidence_ids": value["supporting_evidence_ids"]}
                    for value in prop["values"] if value["value"] not in (None, "", [], {})]
            else:
                component["conflicts"].pop(field, None)
    by_record = {record["id"]: record for record in output["records"]}
    for prop in properties.values():
        for assertion in prop["assertions"]:
            record = by_record.get(assertion["source_id"])
            if record is None:
                continue
            coverage = record.setdefault("coverage", {})
            coverage.setdefault("entity_properties", {})[prop["key"]] = prop["status"]
            value = next(value["value"] for value in prop["values"]
                         if assertion["id"] in value["assertion_ids"])
            if value in (None, "", [], {}):
                coverage.setdefault("fields", {})[assertion["field_path"][0]] = "not_published"
            elif prop["status"] == "conflicting":
                coverage.setdefault("fields", {})[assertion["field_path"][0]] = "conflict"
            elif coverage.get("fields", {}).get(assertion["field_path"][0]) == "conflict":
                coverage["fields"][assertion["field_path"][0]] = "published"
    for record in by_record.values():
        if "entity_properties" not in record.get("coverage", {}):
            continue
        record["limitations"] = [note for note in record["limitations"]
                                 if not note.startswith("Linked records disagree on ")]
        conflicts = [key for key, status in record["coverage"]["entity_properties"].items()
                     if status == "conflicting"]
        if conflicts:
            record["limitations"].append("Linked records disagree on " + ", ".join(conflicts)
                                         + "; identity does not establish an authoritative value.")
        stored = data._seen.get(record["id"])
        if stored is not None:
            stored["coverage"] = {**deepcopy(record["coverage"]), "fields": {
                **stored["coverage"].get("fields", {}), **record["coverage"].get("fields", {})}}
            stored["limitations"] = list(record["limitations"])


def attach_entity_navigation(data: Any, records: list[dict[str, Any]]) -> dict[str, Any]:
    """Point collection discovery at exact identities without treating names as joins."""
    from pydantic import ValidationError

    from rockygpt_brain.retrieval.profiles import IdentityRegistry, _identity_summary
    from rockygpt_brain.retrieval.release_cache import cached

    if not records:
        return {"status": "not_requested", "entities": []}
    if data._artifact("campus-identities") is None:
        return {"status": "unavailable", "entities": [],
                "reason": "identity_registry_unavailable"}

    def index() -> dict[tuple[str, str, str], list[tuple[Any, list[str] | None]]]:
        registry = IdentityRegistry.model_validate(data._artifact("campus-identities"))
        by_record: dict[tuple[str, str, str], list[tuple[Any, list[str] | None]]] = {}
        for entity in registry.entities:
            for link in entity.links:
                for key in link.source_record_keys:
                    by_record.setdefault((link.collection, link.source_key, key), []).append(
                        (entity, link.source_record_ids))
        return by_record

    try:
        by_record = cached(data, "entity-source-navigation", index)
    except (ValidationError, TypeError):
        return {"status": "unavailable", "entities": [],
                "reason": "identity_registry_unavailable"}
    entities: dict[str, dict[str, Any]] = {}
    for record in records:
        key = record.get("source_record_key")
        # Older evidence rows already encode the exact source key. This is a
        # reversible identifier representation, never a display-name match.
        source_prefix = f"{record.get('source_key')}:"
        if key is None and str(record.get("entity_id", "")).startswith(source_prefix):
            key = record["entity_id"][len(source_prefix):]
        source_key = record.get("source_key")
        if not isinstance(source_key, str) or not isinstance(key, str):
            continue
        matches = {
            str(entity.id): entity for entity, pinned in by_record.get(
                (record["collection"], source_key, key), [])
            if not pinned or record["id"].split(":", 1)[-1] in pinned
        }
        if len(matches) == 1:
            entity_id, entity = next(iter(matches.items()))
            if record["collection"] in {"menu", "campus_hours", "dining_hours"}:
                record["related_to_entity_id"] = entity_id
            else:
                record["canonical_entity_id"] = entity_id
            entities[entity_id] = _identity_summary(entity)
        elif matches:
            record["canonical_entity_candidates"] = sorted(matches)
            entities.update({key: _identity_summary(value) for key, value in matches.items()})
    courses = [record for record in records if record["collection"] == "courses"]
    if courses:
        from rockygpt_brain.retrieval.knowledge import COURSE_IDENTITY_FIELDS, course_id

        published = data._artifact("catalog-course-identities")
        published_ids = {
            (item["source_key"], item["source_record_key"]): item["id"]
            for item in (published.get("courses", []) if isinstance(published, dict) else [])
            if isinstance(item, dict)
            and all(isinstance(item.get(key), str) for key in COURSE_IDENTITY_FIELDS)
        }
        for record in courses:
            source, key = record.get("source_key"), record.get("source_record_key")
            if source and key:
                identifier = published_ids.get((source, key), course_id(source, key))
                record["canonical_entity_id"] = identifier
                entities[identifier] = {"id": identifier, "kind": "course",
                                        "name": record["title"]}
    return {"status": "available", "entities": list(entities.values()),
            "scope": "exact_published_source_links", "next": "lookup_entity"}


def lookup_entity(data: Any, query: Any) -> dict[str, Any]:
    """Read any graph entity once and attach its original, citable source records."""
    from fastapi import HTTPException

    from rockygpt_brain.retrieval.entity_facts import EntityFacts
    from rockygpt_brain.retrieval.helpers import _json
    from rockygpt_brain.retrieval.knowledge import release_graph
    from rockygpt_brain.retrieval.profiles import entity_placements

    data._ensure_loaded()
    result: dict[str, Any] = {
        "status": "ok", "dataset_version": data.dataset["version"],
        "records": [], "total_matches": 0, "truncated": False,
        "resolution": {"status": "unavailable", "entity": None, "candidates": []},
        "coverage": {"scope": "exact_canonical_entity", "absence_is_not_nonexistence": True},
    }
    reader = EntityFacts(data)
    try:
        projection = reader.build(query.entity_id)
    except HTTPException as error:
        if error.status_code == 404:
            result["resolution"]["status"] = "no_match"
            return result
        raise
    facts = projection.model_dump(mode="json")
    offices = next((prop["status"] for prop in facts["properties"] if prop["key"] == "offices"),
                   "unknown")
    if query.properties is not None:
        wanted = set(query.properties)
        facts["properties"] = [p for p in facts["properties"] if p["key"] in wanted]
    result["resolution"] = {"status": "matched", "entity": facts["entity"], "candidates": []}
    supports: dict[str, set[str]] = {}
    for prop in facts["properties"]:
        for assertion in prop["assertions"]:
            supports.setdefault(assertion["source_id"], set()).add(assertion["field_path"][0])
    source_map = {source["id"]: source for source in facts["sources"]}
    usable: dict[str, dict[str, Any]] = {}
    for source_id, names in supports.items():
        source = source_map[source_id]
        original = reader.source_records[source_id]
        raw = original["fields"]
        fields = {name: raw[name] for name in names if name in raw}
        source_catalog = next((value for value in data.sources.values()
                               if value["source_key"] == source["source_key"]), None)
        if source_catalog is None:
            continue
        record = data._evidence(source["collection"], {
            "id": source["row_id"], "source_id": source_catalog["id"],
            "source_record_key": source["source_record_key"],
            "collected_at": source["collected_at"], "valid_from": source["valid_from"],
            "valid_until": source["valid_until"],
        }, fields, original["title"], source["source_url"])
        if record is None:
            continue
        record["canonical_entity_id"] = str(query.entity_id)
        record["source_record_key"] = source["source_record_key"]
        record["limitations"] = list(dict.fromkeys([
            *record["limitations"], *source["limitations"],
        ]))
        record["coverage"]["fields"].update({
            name: "published" if value is not None else "not_published"
            for name, value in fields.items()
        })
        if source.get("derived_from_source_id"):
            record["derived_from_evidence_id"] = source["derived_from_source_id"]
        if source["collection"] == "events":
            from rockygpt_brain.retrieval.normalization import normalize_record

            normalized = deepcopy(record)
            normalized["fields"] = deepcopy(original["fields"])
            normalize_record(normalized)
            record["fields"] = {name: normalized["fields"].get(name) for name in names}
            record["coverage"]["fields"] = {
                name: normalized["coverage"]["fields"].get(name, "published") for name in names}
            record["limitations"] = normalized["limitations"]
            source["limitations"] = list(record["limitations"])
            record["content"] = _json(record["fields"])
        previous = data._seen.get(source_id)
        if previous:
            stored = {**record, "fields": {**previous["fields"], **record["fields"]}}
            stored["content"] = _json(stored["fields"])
            data._seen[source_id] = stored
        else:
            data._seen[source_id] = record
        public = data._public(record, detail=True)
        usable[source_id] = public
        result["records"].append(public)
    accepted = []
    for prop in facts["properties"]:
        if all(
            assertion["source_id"] in usable
            and usable[assertion["source_id"]]["fields"].get(assertion["field_path"][0])
            == reader.source_records[assertion["source_id"]]["fields"].get(
                assertion["field_path"][0])
            for assertion in prop["assertions"]
        ):
            accepted.append(prop)
        else:
            facts["properties_complete"] = False
            facts["coverage"].append({"reason": "supporting_evidence_unavailable_or_truncated",
                                      "fields": [prop["key"]]})
    facts["properties"] = accepted
    apply_entity_coverage(data, result, facts)
    result["entity_facts"] = facts
    result["total_matches"] = len(result["records"])
    identity = release_graph(data).identities.get(str(query.entity_id))
    if identity is None:
        return result
    # Where to find it, as a profile's contact section gives it: each verified building
    # placement, cited with the building's own map record.
    placed, placement, failed = entity_placements(data, identity, release_graph(data).registry)
    if failed:
        result["placement_withheld"] = "placement_unavailable"
        return result
    result["placement"] = placement
    own = {source["id"] for source in facts["sources"]}
    unplaced: dict[str, dict[str, Any]] = {}
    for record in placed:
        if record["id"] in own:
            # The entity's own record: add only the room that places it, marked as the
            # entity's facts mark its offices, never the rest of an unrequested row.
            state = {"conflicting": "conflict", "unknown": "not_published"}.get(
                offices, "published")
            record["fields"] = {"office": record["fields"].get("office")}
            record["coverage"]["fields"] = {"office": state}
            if state == "conflict":
                record["limitations"].append(
                    "Linked records disagree on offices; identity does not establish an "
                    "authoritative value.")
            record["canonical_entity_id"] = str(query.entity_id)
        record["content"] = _json(record["fields"])
        public = data._public(record, detail=True)
        kept = next((item for item in result["records"] if item["id"] == record["id"]), None)
        if kept is None:
            result["records"].append(public)
        else:
            unplaced.setdefault(kept["id"], deepcopy(kept))
            kept["fields"] = {**public["fields"], **kept["fields"]}
            kept["coverage"]["fields"] = {
                **public["coverage"]["fields"], **kept["coverage"]["fields"]}
            kept["limitations"] = list(dict.fromkeys(
                kept["limitations"] + public["limitations"]))
        stored = data._seen.get(record["id"])
        if stored is None:
            data._seen[record["id"]] = record
        else:
            # A new record, never an edit of one a search or profile already shared.
            fields = {**record["fields"], **stored["fields"]}
            data._seen[record["id"]] = {
                **stored, "fields": fields, "content": _json(fields),
                "coverage": {**stored["coverage"], "fields": {
                    **record["coverage"]["fields"], **stored["coverage"]["fields"]}},
                "limitations": list(dict.fromkeys(
                    stored["limitations"] + record["limitations"])),
            }
    if unplaced:
        # For bounded_result only: it restores these if it sheds the placement, and never
        # delivers them.
        result["_unplaced_records"] = unplaced
    return result
