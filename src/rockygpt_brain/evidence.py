"""Request-local provenance and deterministic draft validation."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from rockygpt_brain.contracts import Citation
from rockygpt_brain.errors import GroundingError
from rockygpt_brain.planning import AnswerDraft, ClaimKind, RouteMode


class EvidenceKind(str, Enum):
    DATA = "data"
    CONVERSATION = "conversation"
    POLICY = "policy"
    RAG = "rag"
    HISTORICAL_DATA = "historical_data"


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str = Field(alias="evidenceId", min_length=1, max_length=256)
    kind: EvidenceKind
    source_id: str = Field(alias="sourceId", min_length=1, max_length=256)
    title: str = Field(min_length=1, max_length=500)
    url: HttpUrl | None = None
    collected_at: datetime | None = Field(default=None, alias="collectedAt")
    dataset_id: str | None = Field(default=None, alias="datasetId")
    dataset_version: str | None = Field(default=None, alias="datasetVersion")
    payload: dict[str, Any] = Field(default_factory=dict)


class EvidenceRegistry:
    def __init__(self) -> None:
        self._items: dict[str, Evidence] = {}

    def register(self, evidence: Evidence) -> None:
        existing = self._items.get(evidence.evidence_id)
        if existing is not None and existing != evidence:
            raise ValueError(f"conflicting evidence ID: {evidence.evidence_id}")
        self._items[evidence.evidence_id] = evidence

    def get(self, evidence_id: str) -> Evidence | None:
        return self._items.get(evidence_id)

    def all(self) -> list[Evidence]:
        return list(self._items.values())

    def citations(self, evidence_ids: list[str]) -> list[Citation]:
        citations: list[Citation] = []
        seen: set[str] = set()
        for evidence_id in evidence_ids:
            item = self._items.get(evidence_id)
            if item is None or item.url is None or item.source_id in seen:
                continue
            seen.add(item.source_id)
            citations.append(
                Citation(
                    sourceId=item.source_id,
                    title=item.title,
                    url=item.url,
                    collectedAt=item.collected_at,
                )
            )
        return citations[:3]

    def prompt_payload(self) -> list[dict[str, Any]]:
        return [item.model_dump(mode="json", by_alias=True) for item in self._items.values()]


def validate_draft(
    draft: AnswerDraft,
    registry: EvidenceRegistry,
    mode: RouteMode,
    *,
    require_grounding: bool,
) -> None:
    reasons: list[str] = []
    used: set[str] = set()
    if require_grounding and not draft.claims:
        reasons.append("grounded routes require at least one claim")
    for claim in draft.claims:
        if claim.kind in {ClaimKind.CAMPUS, ClaimKind.CONVERSATION} and not claim.evidence_ids:
            reasons.append(f"{claim.kind.value} claim has no evidence")
        for evidence_id in claim.evidence_ids:
            used.add(evidence_id)
            evidence = registry.get(evidence_id)
            if evidence is None:
                reasons.append(f"unknown evidence ID {evidence_id}")
                continue
            if claim.kind == ClaimKind.CAMPUS and evidence.kind not in {
                EvidenceKind.DATA,
                EvidenceKind.RAG,
                EvidenceKind.POLICY,
            }:
                reasons.append(f"campus claim uses {evidence.kind.value} evidence")
            if claim.kind == ClaimKind.CONVERSATION and evidence.kind not in {
                EvidenceKind.CONVERSATION,
                EvidenceKind.HISTORICAL_DATA,
            }:
                reasons.append("conversation claim uses non-conversation evidence")
            if claim.kind == ClaimKind.GENERAL and mode != RouteMode.GENERAL:
                reasons.append("general claim is not allowed on a campus route")
    for evidence_id in draft.citation_evidence_ids:
        evidence = registry.get(evidence_id)
        if evidence is None:
            reasons.append(f"citation uses unknown evidence ID {evidence_id}")
        elif evidence.url is None:
            reasons.append(f"citation evidence {evidence_id} has no public URL")
        if evidence_id not in used:
            reasons.append(f"citation evidence {evidence_id} supports no declared claim")
    if reasons:
        raise GroundingError(reasons)
