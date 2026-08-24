"""Deterministic preflight for emergencies, privacy, secrets, and private data."""

from __future__ import annotations

import re
from dataclasses import dataclass

from rockygpt_brain.errors import GroundingError
from rockygpt_brain.evidence import Evidence, EvidenceKind, EvidenceRegistry
from rockygpt_brain.planning import AnswerDraft, ClaimKind, DraftClaim


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    handled: bool
    draft: AnswerDraft | None = None


_SUICIDE = re.compile(
    r"\b(?:i(?:'m| am) suicidal|kill myself|end my life|want to die|hurt myself)\b", re.I
)
_ACTIVE_911 = re.compile(
    r"\b(?:there(?:'s| is) (?:a )?fire|building is on fire|someone has (?:a )?(?:gun|weapon)|"
    r"weapon (?:is )?being used|someone is unconscious|not breathing)\b",
    re.I,
)
_SECRET_REQUEST = re.compile(
    r"\b(?:show|reveal|print|give me|ignore .* and show).{0,40}"
    r"(?:system prompt|api key|admin token|password|secret|credential)\b",
    re.I,
)
_PRIVATE_DATA = re.compile(
    r"\b(?:my|their|another student(?:'s)?)\s+(?:grade|grades|gpa|home address|student record)\b",
    re.I,
)
_PROHIBITED_OUTPUT = re.compile(
    r"(?:\b(?:sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|AKIA[A-Z0-9]{16})\b|"
    r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)|"
    r"(?<!\d)(?:\d[ -]*?){13,19}(?!\d)|"
    r"\b(?:R|A)\d{8}\b|"
    r"\b(?:api[_ -]?key|admin[_ -]?token|password|secret|credential)\s*[:=]\s*\S{8,})",
    re.I,
)


def preflight(message: str, registry: EvidenceRegistry) -> PolicyDecision:
    if _SUICIDE.search(message):
        return _policy_answer(
            registry,
            evidence_id="policy:988",
            answer=(
                "If you may act on these thoughts or are in immediate danger, call or text **988** "
                "now. If there is an immediate physical danger, call **911**. Stay with someone "
                "you trust if you can."
            ),
            claim="988 is the U.S. Suicide & Crisis Lifeline; 911 is for immediate danger.",
            route="safety",
        )
    if _ACTIVE_911.search(message):
        return _policy_answer(
            registry,
            evidence_id="policy:911",
            answer=(
                "This sounds like an active emergency. Move to safety if you can and call **911** "
                "now. Do not approach a weapon or re-enter a dangerous area."
            ),
            claim="911 is the emergency number for an active life-safety emergency.",
            route="safety",
        )
    if _SECRET_REQUEST.search(message):
        return _policy_answer(
            registry,
            evidence_id="policy:secrets",
            answer=(
                "I can’t provide system prompts, credentials, secrets, or administrative tokens."
            ),
            claim="Secret and administrative credential disclosure is prohibited.",
            route="policy",
        )
    if _PRIVATE_DATA.search(message):
        return _policy_answer(
            registry,
            evidence_id="policy:private-data",
            answer=(
                "I can’t access or disclose grades, GPA, private student records, or private home "
                "addresses. Please use the authorized college system or contact the appropriate "
                "office."
            ),
            claim="Hybrid V1 has no authorized access to private student records.",
            route="policy",
        )
    return PolicyDecision(False)


def validate_post_generation(draft: AnswerDraft) -> None:
    """Reject sensitive material on every model-controlled public text surface."""

    surfaces = [
        draft.answer,
        draft.route,
        *(claim.text for claim in draft.claims),
        *draft.suggested_questions,
        *(value for action in draft.ui_actions for value in (action.payload or {}).values()),
    ]
    if any(_PROHIBITED_OUTPUT.search(value) for value in surfaces):
        raise GroundingError(["draft contains prohibited sensitive material"])


def _policy_answer(
    registry: EvidenceRegistry,
    *,
    evidence_id: str,
    answer: str,
    claim: str,
    route: str,
) -> PolicyDecision:
    registry.register(
        Evidence(
            evidenceId=evidence_id,
            kind=EvidenceKind.POLICY,
            sourceId=evidence_id,
            title="RockyGPT deterministic safety policy",
            payload={"claim": claim},
        )
    )
    return PolicyDecision(
        True,
        AnswerDraft(
            answer=answer,
            route=route,
            claims=[DraftClaim(text=claim, kind=ClaimKind.POLICY, evidenceIds=[evidence_id])],
            citationEvidenceIds=[],
            uiActions=[],
            suggestedQuestions=[],
        ),
    )
