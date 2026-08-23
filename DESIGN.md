# RockyGPT brain (Python) — architecture proposal

This document is an independent design proposal written from `spec/` only, per
`CLAUDE.md`'s clean-room rule. It records the choices `spec/` deliberately
leaves open and the reasoning behind them. See `THREAT_MODEL.md` for the
companion security analysis.

## 1. Goals derived from `spec/`

- Serve `GET /health`, `GET /readiness`, `POST /v1/chat`, `POST /v1/feedback`,
  and the `/v1/admin/logs*` operator surface exactly as described in
  `spec/brain-api.openapi.yaml`.
- Ground campus-fact answers in `rockygpt-data` responses only, with citations
  that trace to real fetched records — never invented URLs or titles.
- Route active emergencies and suicidal-intent messages to a deterministic
  `safety` path that always surfaces `911` or `988`.
- Own conversation persistence, feedback, and redaction/expiry, without ever
  touching the data service's database or storing raw identifiers.
- Work from a standalone checkout with no source dependency on any other
  RockyGPT repository.

## 2. Stack choices

| Concern | Choice | Why |
| --- | --- | --- |
| Language/runtime | Python 3.11+ | Mandated by `CLAUDE.md`. |
| Web framework | FastAPI + Uvicorn (ASGI) | Async-native, integrates typed Pydantic models for strict validation, gives an introspectable OpenAPI document for the contract gate almost for free. |
| Request/response models | Pydantic v2, `extra="forbid"` everywhere | Directly encodes the OpenAPI `additionalProperties: false` requirement and the field-level bounds (`maxLength`, enums, patterns) called out in `spec/brain-api.openapi.yaml`. |
| Outbound HTTP (data service, OpenAI) | `httpx.AsyncClient` | Async, timeout-first, used natively by the `openai` SDK too so there is one HTTP stack in the process. |
| Model calls | OpenAI Chat Completions with tool/function calling (`openai` SDK) | `OPENAI_CHAT_MODEL` is caller-selected; tool calling lets the model request structured campus data instead of free-associating, which is the basis of the anti-hallucination design in §4. |
| Persistence | PostgreSQL via `asyncpg`, hand-written SQL, no ORM | The schema is one table with simple upsert/expiry semantics; an ORM would add ceremony without simplifying anything. Raw SQL keeps the brain-owned-schema boundary auditable in one file (`persistence/schema.sql`). |
| Rate limiting | In-process fixed-window counters | Single-instance-correct and dependency-free. Documented as a scaling limitation in §7 rather than papered over with an unverified Redis dependency. |
| Logging | Stdlib `logging` with a JSON formatter | Structured logs without a new dependency; the formatter is written to make it structurally impossible to pass raw message text through (see `observability/logging.py`). |
| Lint/format | `ruff` (lint + format) | One fast dependency covering both jobs. |
| Types | `mypy --strict` on `src/` | Matches "type checker" in the delivery order. |
| Tests | `pytest` + `pytest-asyncio` + `respx` (HTTP mocking) | `respx` intercepts `httpx` calls so data-service and OpenAI interactions are tested without live network access. |

## 3. Process shape

```
                          ┌─────────────────────────┐
 browser → rockygpt-ui →  │        FastAPI app        │
                          │                          │
                          │  probes  chat  feedback  │
                          │              admin       │
                          └───────┬─────────┬────────┘
                                  │         │
                     data_client  │         │  persistence (asyncpg)
                                  ▼         ▼
                          rockygpt-data   brain-owned Postgres schema
                                  ▲
                                  │ tool calls
                          brain/orchestrator ── model_client (OpenAI)
```

One process serves all routes; there is no internal message queue or worker
tier because the brain has no long-running background work beyond a purge
loop and an SSE fan-out, both of which are cheap in-process tasks (§8).

## 4. Chat pipeline and the anti-hallucination guarantee

`brain/orchestrator.py` runs, per request:

1. **Environment/admin gates and rate limiting** (see `THREAT_MODEL.md`).
2. **Deterministic safety classification** (`brain/safety.py`) on the message.
   If it fires, the model is never called: the brain fetches
   `GET /v1/safety-resources` for citations (falling back to a static,
   hardcoded 911/988 message if that call fails — a safety answer must not
   depend on a live dependency to mention emergency numbers) and returns
   `route: "safety"` immediately. This makes the safety gates in
   `spec/acceptance.md` deterministic and fast rather than a probabilistic
   property of a model call.
3. **Otherwise, a bounded tool-calling loop.** The model is given one tool
   per `rockygpt-data` search endpoint (`brain/tools.py`), plus
   `get_safety_resources` and `get_map`. Tool execution calls
   `data_client`, and every record's `source` object (the data service's own
   `sourceId`/`title`/`url`/`collectedAt`) is recorded in a per-turn
   `ProvenanceRegistry` (`brain/grounding.py`) keyed by `sourceId`. The loop
   runs for at most `MAX_TOOL_ITERATIONS` (default 4) round trips.
4. **Structured final answer.** The model's last step must call a
   `submit_answer` tool whose arguments are `answerMarkdown`, `route`,
   `citedSourceIds` (a list of `sourceId` values), `uiActions`,
   `suggestedQuestions`. The brain **never renders a citation the model
   typed** — it looks up each `citedSourceId` in the registry built from
   real tool results this turn and discards anything not present there. This
   is the load-bearing decision behind "fabricated URLs or source titles fail
   the gate": fabrication is structurally impossible because the citation
   payload the user sees is assembled server-side from data actually
   fetched, not from model output.
5. **UI action and suggested-question validation.** Both are re-validated
   against the OpenAPI enum/shape before being returned; anything malformed
   is dropped rather than allowed to break the contract.
6. **Synchronous persistence.** The chat log row is written before the HTTP
   response is returned, so a `requestId` returned to the UI is always
   upsert-able by a subsequent `/v1/feedback` call (§6).

General-knowledge questions (e.g. "2 + 2") take the same loop but the model
is instructed to skip tool calls when no campus grounding is needed, giving
`citations: []` and `route: "standard"` (or `"ungrounded"` when it is
explicitly declining an unsupported campus claim) without being forced to
invoke `rockygpt-data`.

### Time pinning

`ChatRequest.now` and `ChatRequest.timezone` are threaded through as the
single source of truth for "current time": if `now` is supplied, it — not
wall-clock time — is what is passed as `at` to hours/shuttle tools and stated
in the system prompt. Absent `now`, the brain uses its own UTC clock. This
satisfies "pinned `now` and timezone values control hours and shuttle
calculations."

## 5. Safety classification design

`brain/safety.py` is a deterministic, dependency-free classifier (no model
call) with two independent triggers:

- **Active emergency**: present-tense/active-indicator phrasing about
  unconsciousness, a current fire, or weapon use (e.g. "he's unconscious",
  "there's a fire in", "someone has a gun"). A second pattern set for
  informational/hypothetical phrasing ("what's the fire evacuation
  procedure", "how do I report a weapon") suppresses the trigger so
  procedural questions are not misrouted, matching the explicit acceptance
  gate for that case.
- **Suicidal intent**: phrase-level patterns for expressed self-harm intent
  ("kill myself", "want to end my life", "don't want to be alive anymore").

Both are intentionally conservative toward false positives (a safety route
that isn't needed costs a slightly-off answer; a missed one is unacceptable)
and are unit-tested against both the acceptance-gate examples and adjacent
informational phrasings that must *not* trigger. This is documented as a
heuristic, not a clinical tool, in `THREAT_MODEL.md`.

## 6. Persistence and privacy

Single table `chat_logs` (see `persistence/schema.sql`) keyed by `id =
requestId`. `conversationId` and `visitorId` are never stored raw: they are
transformed with `HMAC-SHA256(CHAT_LOG_HASH_KEY, value)` before insertion
(`persistence/hashing.py`), matching "durable identifiers use a keyed,
non-reversible transformation." `user_message`, `assistant_message`, and
`feedback_comment` are redacted (`security/redaction.py`) before being
written — the redaction never touches the live response returned to the
caller, only the stored copy.

`/v1/feedback` upserts rating/category/comment plus a normalized
`feedback` (`positive`/`negative`) column onto the row matching
`requestId`. `/v1/admin/logs/feedback` sets/clears that same `feedback`
column directly as an operator override, independent of the student rating.

**Expiry** is enforced by a background purge loop (`persistence/purge.py`)
that runs hourly:

- rows older than 30 days have `user_message`/`assistant_message`/
  `feedback_comment` cleared (satisfies "question and answer text expires
  within 30 days" while keeping the row for aggregate `LogMetrics`);
- rows older than 90 days are deleted outright (satisfies "ratings, redacted
  feedback, and non-text operational metadata expire within 90 days").

The admin log listing (`/v1/admin/logs`) computes an opaque `version`
watermark from `(max(updated_at), count(*))`. A request whose `version` query
parameter or `If-None-Match` header matches the current watermark gets a
cheap "nothing changed" response (`{"modified": false}` or `304`) instead of
re-querying and re-serializing the page. `/v1/admin/logs/stream` is an
in-process SSE fan-out (`observability/change_bus.py`) that emits
`data: {"type":"change"}` whenever a row is written, plus a heartbeat
comment every 15s so idle connections aren't silently dropped and clients
can safely reconnect.

## 7. Known scaling limitations (documented, not hidden)

- **Rate limiting is per-process.** A multi-instance deployment needs a
  shared store (e.g. Redis) to enforce one global quota per client; today
  each instance enforces its own. This is acceptable for the current
  single-instance staging/production topology implied by `spec/` and is
  called out here so it is a conscious tradeoff, not a surprise.
- **The SSE change bus is per-process** for the same reason; multi-instance
  operator dashboards would only see changes written by the instance they
  are connected to.
- **Unsigned-client rate limiting falls back to a coarse identity** (the
  hashed `conversationId`) because the brain never sees a raw IP. This is an
  abuse-mitigation heuristic, not a security boundary — the real boundary is
  the signed `x-rockygpt-client-key`/`x-rockygpt-client-signature` pair from
  `rockygpt-ui`.

## 8. Deployment shape

Single container, one process, `uvicorn` serving the FastAPI app. Startup
runs schema migration (idempotent `CREATE TABLE IF NOT EXISTS`). `/health`
never touches the database, data service, or model, per spec. `/readiness`
pings the brain's own database (`SELECT 1`) and the data service's
`GET /health`/`GET /readiness` — both are cheap, sub-second, deterministic
checks that fit the 3-second budget — and reports `failing: ["database"]`
and/or `["dataset"]` accordingly. It does not call OpenAI: there is no cheap
liveness ping for a model provider, a real completion call would be slow and
token-costly to run on every probe, and a transient model outage should
surface as a per-request `503` from `/v1/chat` (which does call the model)
rather than pull the whole service out of rotation. See `DEPLOYMENT.md` and
`ROLLBACK.md` for the container definition and rollback procedure.
