"""POST /v1/feedback.

Upserts onto the existing `chat_logs` row for `requestId` (rating,
category, comments, and a normalized `positive`/`negative` `feedback`
column), and publishes to the operator change bus on success so
`/v1/admin/logs/stream` observes the update. Follows the same bounded-
persistence pattern established in api/chat.py: the DB write is bounded to
`PERSISTENCE_TIMEOUT_SECONDS`, and a timeout or database-layer failure maps
to the documented `503 SERVICE_UNAVAILABLE` envelope rather than a generic
`500`. Nothing here catches `BaseException` or `CancelledError`, so
external request cancellation still propagates.

**Rate-limit key.** Unlike `/v1/chat`'s `conversationId` (which normally
identifies one ongoing session shared across many messages from the same
caller), `requestId` here identifies one specific *prior* chat turn and is
different for essentially every legitimate feedback submission by
construction — an unsigned caller's requests would never share a bucket
even without any adversarial intent, so hashing it would not rate-limit
anything real. A trusted, signed `x-rockygpt-client-key` is used as-is;
every unsigned/untrusted caller instead shares one fixed, per-process-
random anonymous bucket generated once at import time.

A `requestId` with no matching chat log (e.g. expired, or never actually
persisted) has no documented `404` in this endpoint's contract — it is
reported as `400 INVALID_REQUEST` with a fixed message rather than
reflecting the submitted id back.
"""

from __future__ import annotations

import asyncio
import secrets

import asyncpg
from fastapi import APIRouter, Request

from rockygpt_brain.api.parsing import MAX_FEEDBACK_BODY_BYTES, parse_json_body
from rockygpt_brain.errors import InvalidRequestError, RateLimitedError, ServiceUnavailableError
from rockygpt_brain.persistence.chat_logs import upsert_student_feedback
from rockygpt_brain.schemas.feedback import FeedbackRequest, FeedbackSuccess
from rockygpt_brain.security.client_identity import ClientIdentity, resolve_client_identity
from rockygpt_brain.security.redaction import redact

router = APIRouter()

PERSISTENCE_TIMEOUT_SECONDS = 5.0

# Fixed for the life of this process, generated once — not a hardcoded
# literal (predictable/shared across deployments) and not a fresh value per
# request (which would defeat rate limiting for every unsigned caller).
_ANONYMOUS_RATE_LIMIT_BUCKET = secrets.token_hex(16)


def _rate_limit_key(*, identity: ClientIdentity) -> str:
    return identity.key if identity.trusted else _ANONYMOUS_RATE_LIMIT_BUCKET


@router.post("/v1/feedback", response_model=FeedbackSuccess)
async def upsert_chat_feedback(request: Request) -> FeedbackSuccess:
    settings = request.app.state.settings
    body = await parse_json_body(request, FeedbackRequest, max_bytes=MAX_FEEDBACK_BODY_BYTES)

    abuse_hash_key = (
        settings.abuse_hash_key.get_secret_value() if settings.abuse_hash_key else None
    )
    identity = resolve_client_identity(
        client_key=request.headers.get("x-rockygpt-client-key"),
        client_signature=request.headers.get("x-rockygpt-client-signature"),
        abuse_hash_key=abuse_hash_key,
    )
    rate_limit_key = _rate_limit_key(identity=identity)

    per_identity_result = request.app.state.feedback_rate_limiter.check(rate_limit_key)
    if not per_identity_result.allowed:
        raise RateLimitedError(
            "Feedback rate limit exceeded.",
            retry_after_seconds=per_identity_result.retry_after_seconds,
        )
    global_result = request.app.state.global_feedback_rate_limiter.check("global")
    if not global_result.allowed:
        raise RateLimitedError(
            "The service is busy; try again shortly.",
            retry_after_seconds=global_result.retry_after_seconds,
        )

    db_pool = request.app.state.db_pool
    if db_pool is None:
        raise ServiceUnavailableError("Persistence is not configured.")

    try:
        async with asyncio.timeout(PERSISTENCE_TIMEOUT_SECONDS):
            updated = await upsert_student_feedback(
                db_pool,
                request_id=body.request_id,
                rating=body.rating,
                category=body.category,
                comments=redact(body.comments),
            )
    except TimeoutError as exc:
        raise ServiceUnavailableError("Persistence timed out.") from exc
    except (asyncpg.PostgresError, OSError) as exc:
        raise ServiceUnavailableError("Persistence is temporarily unavailable.") from exc

    if not updated:
        raise InvalidRequestError("No chat log exists for the given requestId.")

    request.app.state.change_bus.publish()
    return FeedbackSuccess(success=True)
