"""Bounded server-owned conversation memory and exact assistant claim ledger."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from rockygpt_brain.evidence import Evidence, EvidenceKind, EvidenceRegistry


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
        }

    def register_conversation_evidence(self, registry: EvidenceRegistry) -> None:
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

    def snapshot(self) -> MemorySnapshot:
        return MemorySnapshot(
            recent_turns=tuple(self.recent_turns),
            claims=tuple(self.claims),
            entities=tuple(dict(item) for item in self.entities),
            corrections=tuple(dict(item) for item in self.corrections),
        )

    def append(
        self,
        turn: MemoryTurn,
        claims: list[AssistantClaim],
        *,
        recent_limit: int,
        claim_limit: int,
    ) -> None:
        self.recent_turns = (self.recent_turns + [turn])[-recent_limit:]
        self.claims = (self.claims + claims)[-claim_limit:]
