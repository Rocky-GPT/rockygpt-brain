"""Student discovery locates identities; their contact facts have one shared reader."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from rockygpt_brain.api.identities import _campus_data, _snapshot
from rockygpt_brain.retrieval.entity_facts import EntityFactProjection, EntityFacts

router = APIRouter(prefix="/v1")

# What a person is part of, named in the list so two people are told apart
# without opening each card. Read from the registry's evidenced part_of links,
# the same index that names them, not from fact values.
CONTEXT_KINDS = frozenset({"school", "office", "organization"})


@router.get("/directory")
def directory() -> dict[str, Any]:
    """A bounded identity index, without fetching every contact's source records."""
    with _campus_data() as data:
        registry, snapshot = _snapshot(data)
        by_id = {entity.id: entity for entity in registry.entities}
        contacts = []
        ordered = sorted(registry.entities, key=lambda entry: (entry.name.casefold(), entry.id))
        for entity in ordered:
            if not any(link.collection in {"contacts", "faculty"} for link in entity.links):
                continue
            is_faculty = any(link.collection == "faculty" for link in entity.links)
            bucket = "Offices"
            if entity.kind == "person":
                bucket = "Staff & Faculty" if is_faculty else "Others"
            context: list[str] = []
            if entity.kind == "person":
                for relation in entity.relationships:
                    if relation.type != "part_of" or relation.target_entity_id is None:
                        continue
                    target = by_id.get(relation.target_entity_id)
                    if (target is not None
                            and target.kind in CONTEXT_KINDS and target.name not in context):
                        context.append(target.name)
            entry = {
                "id": str(entity.id), "canonical_entity_id": str(entity.id),
                "name": entity.name, "aliases": entity.aliases,
                "kind": entity.kind, "bucket": bucket,
                "searchText": " ".join([entity.name, *entity.aliases, *context]).casefold(),
            }
            if context:
                entry["context"] = " · ".join(context[:2])
            contacts.append(entry)
        offices = [entry for entry in contacts if entry["bucket"] == "Offices"]
        faculty = [entry for entry in contacts if entry["bucket"] == "Staff & Faculty"]
        others = [entry for entry in contacts if entry["bucket"] == "Others"]
        return {
            **snapshot, "allContacts": contacts, "offices": offices,
            "facultyStaff": faculty, "others": others,
            "counts": {"offices": len(offices), "staffFaculty": len(faculty),
                       "others": len(others), "total": len(contacts)},
            "total": len(contacts), "releaseVersion": snapshot["dataset_version"],
        }


@router.get("/entities/{entity_id}/facts", response_model=EntityFactProjection)
def entity_facts(
    entity_id: UUID,
    dataset_version: Annotated[str, Query(min_length=1, max_length=160)],
    identity_hash: Annotated[str, Query(min_length=1, max_length=128)],
) -> EntityFactProjection:
    """Public published facts only, pinned to the identity index the caller selected."""
    with _campus_data() as data:
        _, snapshot = _snapshot(data)
        if (dataset_version != snapshot["dataset_version"]
                or identity_hash != snapshot["identity_hash"]):
            raise HTTPException(409, "Campus dataset changed; reload the directory")
        return EntityFacts(data).build(entity_id, include_records=False)
