from __future__ import annotations

from datetime import UTC, datetime

import pytest

from rockygpt_brain.errors import GroundingError
from rockygpt_brain.evidence import Evidence, EvidenceKind, EvidenceRegistry, validate_draft
from rockygpt_brain.memory import AssistantClaim, MemorySnapshot
from rockygpt_brain.planning import AnswerDraft, ClaimKind, DraftClaim, RouteMode


def _draft(text: str, evidence_ids: list[str]) -> AnswerDraft:
    return AnswerDraft(
        answer=text,
        route="standard",
        claims=[DraftClaim(text=text, kind=ClaimKind.CONVERSATION, evidenceIds=evidence_ids)],
        citationEvidenceIds=[],
        uiActions=[],
        suggestedQuestions=[],
    )


def _conversation_registry() -> EvidenceRegistry:
    registry = EvidenceRegistry()
    registry.register(
        Evidence(
            evidenceId="conversation:claim-1",
            kind=EvidenceKind.CONVERSATION,
            sourceId="turn:turn-1",
            title="RockyGPT conversation record",
            payload={"text": "The shuttle leaves at 9:00 AM."},
        )
    )
    return registry


def test_conversation_claim_must_reproduce_exact_ledger_statement() -> None:
    registry = _conversation_registry()

    with pytest.raises(GroundingError):
        validate_draft(
            _draft("Earlier, I said the shuttle leaves at 10:00 PM.", ["conversation:claim-1"]),
            registry,
            RouteMode.CONVERSATION,
            require_grounding=True,
        )


def test_exact_conversation_recall_is_accepted() -> None:
    registry = _conversation_registry()
    validate_draft(
        _draft(
            "Earlier, I told you: The shuttle leaves at 9:00 AM.",
            ["conversation:claim-1"],
        ),
        registry,
        RouteMode.CONVERSATION,
        require_grounding=True,
    )


def test_conversation_answer_cannot_lie_behind_an_accurate_claim() -> None:
    registry = _conversation_registry()
    draft = _draft(
        "Earlier, I told you: The shuttle leaves at 9:00 AM.",
        ["conversation:claim-1"],
    )
    dishonest = draft.model_copy(update={"answer": "Earlier, I said it leaves at 10:00 PM."})

    with pytest.raises(GroundingError):
        validate_draft(
            dishonest,
            registry,
            RouteMode.CONVERSATION,
            require_grounding=True,
        )


def test_source_recall_requires_same_turn_and_rejects_added_time() -> None:
    registry = _conversation_registry()
    registry.register(
        Evidence(
            evidenceId="historical:source-1",
            kind=EvidenceKind.HISTORICAL_DATA,
            sourceId="transportation",
            title="Official Shuttle Schedule",
            url="https://www.ramapo.edu/shuttle/",
            payload={"turnRequestId": "turn-1"},
        )
    )
    validate_draft(
        _draft(
            "The source I used then was Official Shuttle Schedule.",
            ["conversation:claim-1", "historical:source-1"],
        ),
        registry,
        RouteMode.CONVERSATION,
        require_grounding=True,
    )

    with pytest.raises(GroundingError):
        validate_draft(
            _draft(
                "Official Shuttle Schedule supported the earlier 10:00 PM answer.",
                ["conversation:claim-1", "historical:source-1"],
            ),
            registry,
            RouteMode.CONVERSATION,
            require_grounding=True,
        )


def test_selected_historical_source_alone_supports_source_recall_not_new_facts() -> None:
    registry = _conversation_registry()
    registry.register(
        Evidence(
            evidenceId="historical:source-only",
            kind=EvidenceKind.HISTORICAL_DATA,
            sourceId="transportation",
            title="Official Shuttle Schedule",
            url="https://www.ramapo.edu/shuttle/",
            payload={"turnRequestId": "turn-1"},
        )
    )
    validate_draft(
        _draft(
            "The source I used then was Official Shuttle Schedule.",
            ["historical:source-only"],
        ),
        registry,
        RouteMode.CONVERSATION,
        require_grounding=True,
    )

    with pytest.raises(GroundingError):
        validate_draft(
            _draft(
                "Official Shuttle Schedule supported a 10:00 PM answer.",
                ["historical:source-only"],
            ),
            registry,
            RouteMode.CONVERSATION,
            require_grounding=True,
        )


def test_memory_projection_exposes_only_selected_ledger_claims() -> None:
    now = datetime(2026, 8, 24, tzinfo=UTC)
    first = AssistantClaim("claim-1", "turn-1", "First answer", (), now)
    second = AssistantClaim("claim-2", "turn-2", "Second answer", (), now)
    memory = MemorySnapshot(
        claims=(first, second),
        historical_evidence=(
            {"turnRequestId": "turn-1", "evidenceId": "source-1"},
            {"turnRequestId": "turn-2", "evidenceId": "source-2"},
        ),
    )

    selected = memory.select_for_communication(["claim-1"])
    assert selected.claims == (first,)
    assert selected.historical_evidence == ({"turnRequestId": "turn-1", "evidenceId": "source-1"},)
    assert memory.select_for_communication([]).claims == (second,)
    assert memory.select_for_communication(["invented"]).claims == ()
