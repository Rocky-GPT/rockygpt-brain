"""POST /v1/chat.

**Timeout budget.** `brain.orchestrator.run_chat_turn` bounds the model/
tool-calling path to `OUTER_DEADLINE_SECONDS` (45s) internally. Persisting
the chat log here is bounded separately to `PERSISTENCE_TIMEOUT_SECONDS`
(5s). 45 + 5 = 50s, leaving real margin under the UI's documented
60-second upstream timeout for request parsing, rate limiting, and network
overhead — none of which are meaningful compared to those two budgets. A
persistence timeout or a database-layer failure is mapped to the
documented `503 SERVICE_UNAVAILABLE` envelope (a transient dependency
outage), not a generic `500`; nothing here catches `BaseException` or
`CancelledError`, so external request cancellation still propagates
normally through both awaits.

**Rate-limit key.** A signed, trusted `x-rockygpt-client-key` is used
as-is. For an unsigned/untrusted caller, the key must still be *stable*
across that caller's requests (a fresh random value per request would make
every request look like a new identity and defeat the limiter entirely) but
must not be a raw identifier held in memory or ever logged/persisted: it is
`HMAC-SHA256(ABUSE_HASH_KEY, conversationId)`, the same construction
`persistence.hashing.hash_identifier` already uses for durable pseudonymous
ids, reused here purely as an in-memory lookup key that is never written
anywhere. If `ABUSE_HASH_KEY` isn't configured or the request has no
`conversationId`, every such request instead shares one fixed,
per-process-random anonymous bucket generated once at import time — fixed
(so the limiter can actually enforce a shared cap across all such
requests), but not a hardcoded literal, and never itself logged/persisted.
"""

from __future__ import annotations

import asyncio
import secrets
import time

import asyncpg
from fastapi import APIRouter, Request

from rockygpt_brain.api.parsing import MAX_CHAT_BODY_BYTES, parse_json_body
from rockygpt_brain.brain.orchestrator import run_chat_turn
from rockygpt_brain.brain.outcome import ChatOutcome
from rockygpt_brain.errors import InternalError, RateLimitedError, ServiceUnavailableError
from rockygpt_brain.persistence.chat_logs import ChatLogInput, insert_chat_log
from rockygpt_brain.persistence.hashing import hash_identifier
from rockygpt_brain.schemas.chat import ChatRequest, ChatSuccess
from rockygpt_brain.schemas.common import QuestionOrigin
from rockygpt_brain.security.client_identity import ClientIdentity, resolve_client_identity
from rockygpt_brain.security.redaction import redact

router = APIRouter()

PERSISTENCE_TIMEOUT_SECONDS = 5.0

# Fixed for the life of this process, generated once — not a hardcoded
# literal (which would be predictable/shared across deployments) and not a
# fresh value per request (which would defeat rate limiting for every
# unsigned caller lacking a conversationId).
_ANONYMOUS_RATE_LIMIT_BUCKET = secrets.token_hex(16)


def _header_origin(request: Request) -> QuestionOrigin | None:
    value = request.headers.get("x-rockygpt-origin")
    if value == "client":
        return "client"
    if value == "dev":
        return "dev"
    if value == "bot":
        return "bot"
    return None


def _resolve_question_origin(body: ChatRequest, request: Request) -> QuestionOrigin:
    return body.question_origin or _header_origin(request) or "client"


def _rate_limit_key(
    *, identity: ClientIdentity, conversation_id: str | None, abuse_hash_key: str | None
) -> str:
    if identity.trusted:
        return identity.key
    if abuse_hash_key and conversation_id:
        return f"conv:{hash_identifier(hash_key=abuse_hash_key, value=conversation_id)}"
    return _ANONYMOUS_RATE_LIMIT_BUCKET


@router.post("/v1/chat", response_model=ChatSuccess)
async def create_chat_turn(request: Request) -> ChatSuccess:
    settings = request.app.state.settings
    body = await parse_json_body(request, ChatRequest, max_bytes=MAX_CHAT_BODY_BYTES)

    # Fail fast, before any paid model call: a chat turn is never returned
    # to the caller unless it can also be persisted (requestId must always
    # be upsert-able by a later /v1/feedback call), so there is no reason
    # to spend a model call when persistence is already known unavailable.
    db_pool = request.app.state.db_pool
    if db_pool is None:
        raise ServiceUnavailableError("Persistence is not configured.")
    hash_key = (
        settings.chat_log_hash_key.get_secret_value() if settings.chat_log_hash_key else None
    )
    if not hash_key:
        raise InternalError("Server is misconfigured: CHAT_LOG_HASH_KEY is required.")

    abuse_hash_key = (
        settings.abuse_hash_key.get_secret_value() if settings.abuse_hash_key else None
    )
    identity = resolve_client_identity(
        client_key=request.headers.get("x-rockygpt-client-key"),
        client_signature=request.headers.get("x-rockygpt-client-signature"),
        abuse_hash_key=abuse_hash_key,
    )
    rate_limit_key = _rate_limit_key(
        identity=identity, conversation_id=body.conversation_id, abuse_hash_key=abuse_hash_key
    )

    per_identity_result = request.app.state.chat_rate_limiter.check(rate_limit_key)
    if not per_identity_result.allowed:
        raise RateLimitedError(
            "Chat rate limit exceeded.",
            retry_after_seconds=per_identity_result.retry_after_seconds,
        )
    global_result = request.app.state.global_chat_rate_limiter.check("global")
    if not global_result.allowed:
        raise RateLimitedError(
            "The service is busy; try again shortly.",
            retry_after_seconds=global_result.retry_after_seconds,
        )

    question_origin = _resolve_question_origin(body, request)

    started_at = time.monotonic()
    outcome = await run_chat_turn(
        request=body,
        model_client=request.app.state.model_client,
        data_client=request.app.state.data_client,
    )
    latency_ms = int((time.monotonic() - started_at) * 1000)

    request_id = request.state.request_id
    await _persist(
        request,
        db_pool=db_pool,
        hash_key=hash_key,
        request_id=request_id,
        body=body,
        outcome=outcome,
        question_origin=question_origin,
        latency_ms=latency_ms,
    )
    request.app.state.change_bus.publish()

    return ChatSuccess(
        request_id=request_id,
        answer=outcome.answer,
        route=outcome.route,
        citations=outcome.citations,
        ui_actions=outcome.ui_actions,
        suggested_questions=outcome.suggested_questions,
    )


async def _persist(
    request: Request,
    *,
    db_pool: asyncpg.Pool,
    hash_key: str,
    request_id: str,
    body: ChatRequest,
    outcome: ChatOutcome,
    question_origin: QuestionOrigin,
    latency_ms: int,
) -> None:
    session_id = hash_identifier(hash_key=hash_key, value=body.conversation_id or request_id)
    visitor_id = (
        hash_identifier(hash_key=hash_key, value=body.visitor_id) if body.visitor_id else None
    )

    entry = ChatLogInput(
        id=request_id,
        session_id=session_id,
        visitor_id=visitor_id,
        user_message=redact(body.message) or "",
        assistant_message=redact(outcome.answer) or "",
        route=outcome.route,
        question_origin=question_origin,
        tools_invoked=outcome.tools_invoked,
        tool_arguments={"calls": outcome.tool_calls_log},
        citations=[{"title": c.title, "url": c.url} for c in outcome.citations],
        facts_extracted=[],
        debug_info=outcome.debug_info,
        latency_ms=latency_ms,
    )
    try:
        async with asyncio.timeout(PERSISTENCE_TIMEOUT_SECONDS):
            await insert_chat_log(db_pool, entry)
    except TimeoutError as exc:
        raise ServiceUnavailableError("Persistence timed out.") from exc
    except (asyncpg.PostgresError, OSError) as exc:
        raise ServiceUnavailableError("Persistence is temporarily unavailable.") from exc
