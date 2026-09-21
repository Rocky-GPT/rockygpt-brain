"""Read-only identity inspection for the development UI."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date as CalendarDate
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, ValidationError

from rockygpt_brain.retrieval.data import CampusData
from rockygpt_brain.retrieval.models import CAMPUS_ZONE
from rockygpt_brain.retrieval.profiles import SECTION_COLLECTIONS, IdentityRegistry, ProfileQuery


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


class IdentityCoverage(BaseModel):
    model_config = ConfigDict(extra="ignore")
    identity_count: int
    identities_by_kind: dict[str, int]
    linked_records: dict[str, int]
    relationships: dict[str, int]
    unresolved: list[CoverageIssue]


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
