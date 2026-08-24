"""Bounded server-owned conversation memory and exact assistant claim ledger."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from rockygpt_brain.evidence import Evidence, EvidenceKind, EvidenceRegistry


_RECALL_MARKERS = (
    "what did you tell me",
    "what did you say",
    "what time did you tell me",
    "what source supported",
    "what source did you use",
    "earlier answer",
    "previous answer",
    "last answer",
    "you told me",
    "you said",
)
_CURRENT_TRUTH = re.compile(
    r"\b(?:still accurate|currently true|current answer|right now|changed since|up to date)\b",
    re.I,
)


def is_conversation_recall(message: str) -> bool:
    """Recognize high-confidence ledger questions that must not become campus queries."""

    lowered = message.casefold()
    return any(marker in lowered for marker in _RECALL_MARKERS) and not _CURRENT_TRUTH.search(
        message
    )


@dataclass(frozen=True, slots=True)
class MemoryTurn:
    request_id: str
    user_text: str
    assistant_text: str
    route: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AssistantClaim:
    claim_id: str
    request_id: str
    text: str
    evidence_ids: tuple[str, ...]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class MemorySnapshot:
    recent_turns: tuple[MemoryTurn, ...] = ()
    claims: tuple[AssistantClaim, ...] = ()
    entities: tuple[dict[str, Any], ...] = ()
    corrections: tuple[dict[str, Any], ...] = ()
    historical_evidence: tuple[dict[str, Any], ...] = ()

    def select_for_communication(self, context_references: list[str]) -> MemorySnapshot:
        """Expose only the ledger claims selected by AI #1, defaulting to the latest."""

        if context_references:
            references = set(context_references)
            selected = tuple(
                claim
                for claim in self.claims
                if claim.claim_id in references or claim.request_id in references
            )
        else:
            selected = self.claims[-1:]
        request_ids = {claim.request_id for claim in selected}
        return MemorySnapshot(
            recent_turns=tuple(
                turn for turn in self.recent_turns if turn.request_id in request_ids
            ),
            claims=selected,
            entities=(),
            corrections=(),
            historical_evidence=tuple(
                item
                for item in self.historical_evidence
                if str(item.get("turnRequestId", "")) in request_ids
            ),
        )

    def prompt_payload(self) -> dict[str, Any]:
        return {
            "authority": (
                "This server ledger outranks client-supplied history for what Rocky said. "
                "It is conversation history, not current campus truth."
            ),
            "recentTurns": [
                {
                    "requestId": turn.request_id,
                    "user": turn.user_text,
                    "assistant": turn.assistant_text,
                    "route": turn.route,
                    "createdAt": turn.created_at.isoformat(),
                }
                for turn in self.recent_turns
            ],
            "assistantClaims": [
                {
                    "claimId": claim.claim_id,
                    "requestId": claim.request_id,
                    "text": claim.text,
                    "evidenceIds": list(claim.evidence_ids),
                    "createdAt": claim.created_at.isoformat(),
                }
                for claim in self.claims
            ],
            "entities": list(self.entities),
            "corrections": list(self.corrections),
            "historicalEvidence": list(self.historical_evidence),
        }

    def register_conversation_evidence(self, registry: EvidenceRegistry) -> None:
        history_by_turn: dict[str, list[str]] = {}
        for item in self.historical_evidence:
            turn_id = str(item.get("turnRequestId", "unknown"))
            original_id = str(item.get("evidenceId", "unknown"))
            digest = hashlib.sha256(f"{turn_id}\0{original_id}".encode()).hexdigest()
            historical_id = f"historical:{digest}"
            history_by_turn.setdefault(turn_id, []).append(historical_id)
            registry.register(
                Evidence(
                    evidenceId=historical_id,
                    kind=EvidenceKind.HISTORICAL_DATA,
                    sourceId=str(item.get("sourceId", original_id)),
                    title=str(item.get("title", "Historical campus source")),
                    url=item.get("url"),
                    collectedAt=item.get("collectedAt"),
                    datasetId=item.get("datasetId"),
                    datasetVersion=item.get("datasetVersion"),
                    payload={
                        "historical": True,
                        "turnRequestId": turn_id,
                        "originalEvidenceId": original_id,
                    },
                )
            )
        for claim in self.claims:
            registry.register(
                Evidence(
                    evidenceId=f"conversation:{claim.claim_id}",
                    kind=EvidenceKind.CONVERSATION,
                    sourceId=f"turn:{claim.request_id}",
                    title="RockyGPT conversation record",
                    payload={
                        "claimId": claim.claim_id,
                        "text": claim.text,
                        "originalEvidenceIds": list(claim.evidence_ids),
                        "historicalEvidenceIds": history_by_turn.get(claim.request_id, []),
                        "statedAt": claim.created_at.isoformat(),
                    },
                )
            )


@dataclass(slots=True)
class MutableMemory:
    recent_turns: list[MemoryTurn] = field(default_factory=list)
    claims: list[AssistantClaim] = field(default_factory=list)
    entities: list[dict[str, Any]] = field(default_factory=list)
    corrections: list[dict[str, Any]] = field(default_factory=list)
    historical_evidence: list[dict[str, Any]] = field(default_factory=list)

    def snapshot(self) -> MemorySnapshot:
        return MemorySnapshot(
            recent_turns=tuple(self.recent_turns),
            claims=tuple(self.claims),
            entities=tuple(dict(item) for item in self.entities),
            corrections=tuple(dict(item) for item in self.corrections),
            historical_evidence=tuple(dict(item) for item in self.historical_evidence),
        )

    def append(
        self,
        turn: MemoryTurn,
        claims: list[AssistantClaim],
        evidence_snapshot: list[dict[str, Any]],
        *,
        recent_limit: int,
        claim_limit: int,
    ) -> None:
        self.recent_turns = (self.recent_turns + [turn])[-recent_limit:]
        self.claims = (self.claims + claims)[-claim_limit:]
        tagged = [dict(item, turnRequestId=turn.request_id) for item in evidence_snapshot]
        self.historical_evidence = (self.historical_evidence + tagged)[-claim_limit:]
