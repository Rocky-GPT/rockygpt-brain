"""Read-only identity inspection for the development UI."""

from __future__ import annotations

import os
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date as CalendarDate
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from rockygpt_brain.retrieval.data import CampusData
from rockygpt_brain.retrieval.models import CAMPUS_ZONE
from rockygpt_brain.retrieval.profiles import (
    SECTION_COLLECTIONS,
    Identity,
    IdentityRegistry,
    ProfileQuery,
    RelationshipEvidence,
    _identity_summary,
    _normalize,
    lookup_terms,
    narrows_by_date,
)


def _require_development() -> None:
    if os.getenv("BRAIN_ENVIRONMENT") != "development":
        raise HTTPException(status_code=404, detail="Not found")


router = APIRouter(prefix="/v1/dev/identities", dependencies=[Depends(_require_development)])


class CoverageIssue(BaseModel):
    model_config = ConfigDict(extra="ignore")
    collection: str
    entity: str | None = None
    record: str | None = None
    reason: str
    # The Data compiler's coverage kind; older releases have none. Any text is kept, so a
    # new kind shows up instead of invalidating the whole report.
    kind: str | None = None


class IdentityCoverage(BaseModel):
    model_config = ConfigDict(extra="ignore")
    identity_count: int
    identities_by_kind: dict[str, int]
    linked_records: dict[str, int]
    relationships: dict[str, int]
    unresolved: list[CoverageIssue]


class AliasSource(BaseModel):
    """Why the data compiler put an alias on an identity (`alias_sources` in the report)."""
    model_config = ConfigDict(extra="forbid")
    basis: Literal[
        "identity_map", "record_name", "school_abbreviation", "school_former_name",
        "event_title", "department", "abbreviation", "program_family", "human_reviewed",
    ]
    evidence: RelationshipEvidence | None = None
    source_url: str | None = None
    reviewed_at: str | None = None
    note: str | None = None


class AliasRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")
    entity_id: UUID
    alias: str
    sources: list[AliasSource] = Field(min_length=1)


@contextmanager
def _campus_data() -> Iterator[CampusData]:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise HTTPException(status_code=503, detail="Campus identity data is unavailable")
    data = CampusData(database_url, datetime.now(CAMPUS_ZONE))
    try:
        yield data
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=503, detail="Campus identity data is unavailable"
        ) from None
    finally:
        data.close()


def _snapshot(data: CampusData) -> tuple[IdentityRegistry, dict[str, Any]]:
    identity_state = data.identity_readiness()
    if identity_state.get("status") != "available":
        raise HTTPException(status_code=503, detail="Campus identity registry is unavailable")
    registry = IdentityRegistry.model_validate(data._artifact("campus-identities"))
    return registry, {
        "dataset_version": data.dataset["version"],
        "campus_date": data.today.isoformat(),
        "identity_hash": identity_state["artifact_hash"],
    }


@router.get("")
def identity_index() -> dict[str, Any]:
    with _campus_data() as data:
        registry, snapshot = _snapshot(data)
        coverage = None
        payload = data._artifact("campus-identity-coverage")
        if payload is not None:
            try:
                report = IdentityCoverage.model_validate(payload)
                if report.identity_count == len(registry.entities):
                    coverage = report.model_dump(exclude_none=True)
            except ValidationError:
                pass  # Invalid or missing coverage must not fabricate complete coverage.
        return {
            **snapshot,
            "identities": [entity.model_dump(mode="json", exclude_none=True)
                           for entity in registry.entities],
            "coverage": coverage,
        }


def _alias_sources(
    payload: Any, registry: IdentityRegistry,
) -> dict[tuple[UUID, str], list[dict[str, Any]]] | None:
    """The report's alias sources, only when every one names an alias in this registry."""
    if not isinstance(payload, dict) or not isinstance(payload.get("alias_sources"), list):
        return None
    if payload.get("identity_count") != len(registry.entities):
        return None
    aliases = {(entity.id, alias) for entity in registry.entities for alias in entity.aliases}
    try:
        records = [AliasRecord.model_validate(item) for item in payload["alias_sources"]]
    except ValidationError:
        return None
    if any((record.entity_id, record.alias) not in aliases for record in records):
        return None  # Provenance for a different registry must not be shown as this one's.
    return {(record.entity_id, record.alias): [
        source.model_dump(mode="json", exclude_none=True) for source in record.sources
    ] for record in records}


@router.get("/aliases")
def identity_aliases() -> dict[str, Any]:
    """Every alias: what a lookup by it finds, and why each identity carries it."""
    with _campus_data() as data:
        registry, snapshot = _snapshot(data)
        sources = _alias_sources(data._artifact("campus-identity-coverage"), registry)
        matching: dict[str, list[Identity]] = defaultdict(list)
        for entity in registry.entities:
            for term in lookup_terms(entity):
                matching[term].append(entity)
        spelled: dict[str, str] = {}
        for entity in registry.entities:
            for alias in entity.aliases:
                spelled.setdefault(_normalize(alias), alias)
        rows = []
        for term, alias in sorted(spelled.items(), key=lambda item: (item[0], item[1])):
            matches = matching[term]
            rows.append({
                "alias": alias,
                "lookup": ("single" if len(matches) == 1
                           else "event_dates" if narrows_by_date(matches) else "ambiguous"),
                "matches": [{
                    **_identity_summary(entity),
                    "by_name": _normalize(entity.name) == term,
                    "aliases": [{"alias": value,
                                 "sources": (sources or {}).get((entity.id, value), [])}
                                for value in entity.aliases if _normalize(value) == term],
                } for entity in matches],
            })
        return {**snapshot, "sources_published": sources is not None,
                "alias_count": sum(len(entity.aliases) for entity in registry.entities),
                "aliases": rows}


@router.get("/{entity_id}")
def identity_profile(
    entity_id: UUID,
    date: CalendarDate | None = None,
    meal: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
    include: Annotated[str | None, Query(max_length=120)] = None,
    menu_limit: Annotated[int, Query(ge=1, le=100)] = 12,
    dataset_version: Annotated[str | None, Query(min_length=1, max_length=160)] = None,
) -> dict[str, Any]:
    sections = [part.strip() for part in include.split(",")] if include is not None else list(
        SECTION_COLLECTIONS
    )
    try:
        query = ProfileQuery.model_validate({
            "entity_id": entity_id, "include": sections,
            "date": date, "meal": meal, "menu_limit": menu_limit,
        })
    except ValidationError:
        raise HTTPException(
            status_code=422, detail="Invalid profile section or meal selection"
        ) from None
    with _campus_data() as data:
        registry, snapshot = _snapshot(data)
        if dataset_version is not None and dataset_version != snapshot["dataset_version"]:
            raise HTTPException(status_code=409, detail="Campus dataset changed; reload identities")
        if not any(entity.id == entity_id for entity in registry.entities):
            raise HTTPException(status_code=404, detail="Campus identity was not found")
        return {**snapshot, "profile": data.lookup_profile(query)}
