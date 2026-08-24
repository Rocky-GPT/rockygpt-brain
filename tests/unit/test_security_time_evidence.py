from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime

import pytest

from rockygpt_brain.evidence import Evidence, EvidenceKind, EvidenceRegistry, validate_draft
from rockygpt_brain.errors import GroundingError
from rockygpt_brain.planning import AnswerDraft, ClaimKind, DraftClaim, RouteMode
from rockygpt_brain.security import redact_text, verify_signed_client
from rockygpt_brain.time_context import TimeContext


def test_signed_client_requires_exact_hmac() -> None:
    secret = "a" * 32
    key = "client-key"
    signature = hmac.new(secret.encode(), key.encode(), hashlib.sha256).hexdigest()
    assert verify_signed_client(key, signature, secret)
    assert not verify_signed_client(key, "0" * 64, secret)
    assert not verify_signed_client(key, signature, None)


def test_redaction_removes_sensitive_values() -> None:
    value = "Email me@school.edu, call 201-555-0199, ID R12345678, SSN 123-45-6789"
    redacted = redact_text(value)
    assert redacted is not None
    assert "me@school.edu" not in redacted
    assert "201-555-0199" not in redacted
    assert "R12345678" not in redacted
    assert "123-45-6789" not in redacted


def test_time_context_uses_request_date_and_same_campus_instant() -> None:
    context = TimeContext.create(
        pinned_now=datetime(2026, 8, 25, 3, 30, tzinfo=UTC),
        requested_timezone="America/Los_Angeles",
    )
    assert context.request_date.isoformat() == "2026-08-24"
    assert context.campus_date.isoformat() == "2026-08-24"
    assert context.as_of == "2026-08-25T03:30:00Z"


def test_unknown_evidence_id_fails_grounding() -> None:
    registry = EvidenceRegistry()
    registry.register(
        Evidence(
            evidenceId="known",
            kind=EvidenceKind.DATA,
            sourceId="source",
            title="Official source",
            url="https://example.edu/source",
        )
    )
    draft = AnswerDraft(
        answer="The shuttle is at nine.",
        route="standard",
        claims=[
            DraftClaim(text="The shuttle is at nine.", kind=ClaimKind.CAMPUS, evidenceIds=["fake"])
        ],
        citationEvidenceIds=["fake"],
        uiActions=[],
        suggestedQuestions=[],
    )
    with pytest.raises(GroundingError):
        validate_draft(draft, registry, RouteMode.CAPABILITY, require_grounding=True)
