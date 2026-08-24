"""Request-local provenance and deterministic draft validation."""

from __future__ import annotations

import json
import re
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from rockygpt_brain.contracts import Citation
from rockygpt_brain.errors import GroundingError
from rockygpt_brain.planning import AnswerDraft, ClaimKind, RouteMode


class EvidenceKind(StrEnum):
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
                    source_id=item.source_id,
                    title=item.title,
                    url=item.url,
                    collected_at=item.collected_at,
                )
            )
        return citations[:3]

    def prompt_payload(self) -> list[dict[str, Any]]:
        return [item.model_dump(mode="json", by_alias=True) for item in self._items.values()]


_FACT_ANCHOR = re.compile(
    r"(?:https?://[^\s]+|\b\d{1,4}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)?\b)", re.I
)


def _normalized(value: str) -> str:
    return " ".join(value.casefold().split())


def _validate_conversation_claim(claim_text: str, evidence: list[Evidence]) -> list[str]:
    reasons: list[str] = []
    conversation = [item for item in evidence if item.kind == EvidenceKind.CONVERSATION]
    historical = [item for item in evidence if item.kind == EvidenceKind.HISTORICAL_DATA]
    if not conversation:
        return ["conversation claim has no exact assistant-ledger evidence"]

    normalized_claim = _normalized(claim_text)
    prior_texts = [str(item.payload.get("text", "")).strip() for item in conversation]
    if any(text and _normalized(text) in normalized_claim for text in prior_texts):
        return reasons

    if not historical:
        return ["conversation claim does not reproduce the exact assistant-ledger statement"]

    turn_ids = {
        item.source_id.removeprefix("turn:")
        for item in conversation
        if item.source_id.startswith("turn:")
    }
    if any(str(item.payload.get("turnRequestId", "")) not in turn_ids for item in historical):
        reasons.append("historical evidence is not linked to the cited conversation turn")

    named_source = any(
        _normalized(item.title) in normalized_claim
        or (item.url is not None and str(item.url).casefold() in normalized_claim)
        for item in historical
    )
    if not named_source:
        reasons.append("source-recall claim does not name its historical source")

    support_corpus = _normalized(
        " ".join(
            [
                *prior_texts,
                *(
                    f"{item.title} {item.url or ''} {item.source_id} "
                    f"{json.dumps(item.payload, default=str, sort_keys=True)}"
                    for item in historical
                ),
            ]
        )
    )
    unsupported = [
        anchor.group(0)
        for anchor in _FACT_ANCHOR.finditer(claim_text)
        if _normalized(anchor.group(0).rstrip(".,)")) not in support_corpus
    ]
    if unsupported:
        reasons.append("conversation claim adds facts absent from the exact ledger")
    return reasons


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
    if (
        mode == RouteMode.CONVERSATION
        and draft.claims
        and not any(
            claim.kind == ClaimKind.CONVERSATION
            and _normalized(claim.text) == _normalized(draft.answer)
            for claim in draft.claims
        )
    ):
        reasons.append("conversation answer must equal a declared conversation claim")
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
        if claim.kind == ClaimKind.CONVERSATION:
            claim_evidence = [
                item
                for evidence_id in claim.evidence_ids
                if (item := registry.get(evidence_id)) is not None
            ]
            reasons.extend(_validate_conversation_claim(claim.text, claim_evidence))
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
