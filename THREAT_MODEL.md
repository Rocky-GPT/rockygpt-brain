# Threat model

Companion to `DESIGN.md`. Assets, trust boundaries, and mitigations for the
Python brain, written from `spec/` only.

## 1. Assets

- **Student conversation content** (questions, answers, feedback comments) —
  privacy-sensitive even though campus information itself is mostly public.
- **Pseudonymous identifiers** (`conversationId`, `visitorId`, the UI's abuse
  `client_key`) — must never be reversible to a real person from brain
  storage or logs.
- **Secrets**: `OPENAI_API_KEY`, `DATABASE_URL` credential, `CHAT_LOG_HASH_KEY`,
  `ADMIN_API_TOKEN`, `ABUSE_HASH_KEY`, `STAGING_SERVICE_TOKEN`.
- **Service availability** — chat is the product's core function.
- **Answer integrity** — a citation the brain shows a student is treated as
  authoritative; a fabricated one is a correctness and trust failure, not
  just a bug.

## 2. Trust boundaries

```
untrusted           trusted-if-signed        trusted internal
browser  ──────►  rockygpt-ui  ──────►  brain  ──────►  rockygpt-data
                  (strips cookies,               (HTTP, read-only,
                   raw IP, browser                DATA_URL only)
                   auth before                brain ──────►  OpenAI
                   calling brain)                            (model only)
                                            brain ──────►  brain-owned
                                                            Postgres schema
```

The brain trusts `rockygpt-ui` to have already stripped browser cookies, raw
source addresses, and browser-supplied credentials — but the brain does not
trust the *content* of any field the UI forwards (message text, headers,
IDs): every field is still validated and bounded independently, because a
compromised or buggy UI, or a direct call from `rockygpt-evals`/an
attacker who discovers the brain's URL, is a realistic scenario the OpenAPI
contract is written to survive.

## 3. Threats and mitigations

### 3.1 Unauthenticated/unauthorized access to functional routes

**Threat**: a client without a valid staging token calls `/v1/chat` in a
staging environment; an unauthenticated client calls admin routes.

**Mitigation**: an ASGI middleware checks
`x-rockygpt-environment-token` against `STAGING_SERVICE_TOKEN`
(constant-time compare) on every route except `/health` and `/readiness`,
returning `401 UNAUTHORIZED` when configured and missing/wrong. Admin routes
additionally require `Authorization: Bearer <ADMIN_API_TOKEN>` (also
constant-time compared) and are only *registered at all* when
`ADMIN_API_TOKEN` is configured — otherwise they 404, so there is no
route to probe in an environment where an operator never opted in.

### 3.2 Spoofed client identity used to defeat abuse controls

**Threat**: a caller sets `x-rockygpt-client-key` to someone else's key (or
an arbitrary value) to inherit their rate-limit bucket or reputation.

**Mitigation**: `x-rockygpt-client-key` is trusted only when
`x-rockygpt-client-signature` verifies as
`HMAC-SHA256(ABUSE_HASH_KEY, client_key)` using `hmac.compare_digest`
(constant-time). An unsigned or invalid signature falls back to an
untrusted, ephemeral, non-persisted identity for that request only — it is
never used to look up or influence another client's state.

### 3.3 Sensitive data at rest or in logs

**Threat**: raw IP, raw `conversationId`/`visitorId`, the abuse `client_key`,
or secrets end up durably stored or written to application logs, or student
PII (email, phone, SSN/payment-like numbers, student IDs) sits in stored
chat text indefinitely.

**Mitigations**:
- The brain never receives a raw IP (the UI strips it) and never persists
  `client_key`/`client_signature` — they are used in-request only.
- `conversationId`/`visitorId` are HMAC-transformed (§6 of `DESIGN.md`)
  before storage; the raw values never reach `persistence/`.
- `observability/logging.py`'s JSON formatter takes a fixed allow-list of
  fields (level, event, request_id, route, latency_ms, status) — arbitrary
  strings (message text, headers, identifiers) cannot be logged by accident
  because the logging call sites never have the raw text in scope past the
  point redaction/hashing happens.
- `security/redaction.py` regex-redacts email, phone, SSN/payment-like
  digit runs, and student-ID-like tokens from stored `user_message`,
  `assistant_message`, and `feedback_comment` before the `INSERT`. This is
  necessarily heuristic (freeform text redaction cannot be perfect); it is
  defense in depth, not a substitute for the 30-day text expiry that bounds
  the damage of anything the regexes miss.
- Secrets are read from environment variables only, never logged (the
  logging allow-list makes this structural), and `.env` stays out of source
  control (already `chmod 600` and gitignored per the workspace README).

### 3.4 Fabricated or stale citations

**Threat**: the model states a campus fact with an invented or wrong URL/
title, or attributes a claim to a source it never actually retrieved.

**Mitigation**: see `DESIGN.md` §4. Citations are assembled server-side from
a per-turn `ProvenanceRegistry` populated only by real `rockygpt-data`
responses; a `citedSourceId` the model emits that isn't in the registry is
silently dropped, never rendered. There is no code path that turns model
-authored text into a `Citation.url`/`Citation.title`.

### 3.5 Unsupported/private/imaginary claims presented as fact

**Threat**: the model answers a campus question it has no grounding for
instead of declining.

**Mitigation**: the system prompt requires the model to prefer declining
(`route: "ungrounded"`, explicit "I can't verify that" language) over
answering when no tool result supports the claim; this is a prompting
control (not structurally enforced the way citation fabrication is) and is
covered by the model-backed answer-quality suite in `spec/acceptance.md`
rather than a deterministic unit test.

### 3.6 Active emergency or self-harm intent mishandled

**Threat**: a message describing an active emergency or suicidal intent gets
a generic chat answer instead of `911`/`988`, or — the opposite failure — an
ordinary informational safety question gets misrouted and confuses the
student.

**Mitigation**: `brain/safety.py` is deterministic and runs before any model
call (§5 of `DESIGN.md`), so this behavior does not depend on model sampling
and is directly unit-testable against both the acceptance-gate scenarios and
adjacent phrasings that must not trigger.

### 3.7 Denial of service / resource exhaustion

**Threats**: oversized request bodies; a burst of chat requests driving
uncontrolled OpenAI/data-service spend; slowloris-style slow bodies.

**Mitigations**:
- A body-size-enforcing ASGI middleware rejects bodies over a fixed cap with
  `413` before JSON parsing, checking both `Content-Length` and actual bytes
  read (so a missing/lying `Content-Length` with a chunked body cannot
  bypass the cap).
- Per-identity and global in-process rate limits on `/v1/chat` and
  `/v1/feedback` return `429` with a numeric `Retry-After` (§7 of
  `DESIGN.md` documents the single-instance limitation of this approach).
- The tool-calling loop is bounded to `MAX_TOOL_ITERATIONS`, so a
  pathological model response cannot loop indefinitely or drive unbounded
  data-service/OpenAI calls for one request.
- `httpx` clients to both `rockygpt-data` and OpenAI use explicit connect/
  read timeouts; a hung dependency fails the request rather than the worker.

### 3.8 Cross-service boundary violations

**Threat**: the brain reads/writes the data service's schema directly, or a
generated client/import couples this repository to another RockyGPT
repository's source.

**Mitigation**: the only sanctioned path to campus data is the HTTP client
in `data_client/` against `DATA_URL`; `DATABASE_URL` is documented (and, at
provisioning time, must be enforced by the DBA/infra layer — out of this
repository's control) to be scoped to a brain-owned schema only. No file in
this repository imports from or references a path outside this checkout.

### 3.9 Malformed/hostile input causing a crash or bypassing validation

**Threat**: invalid JSON, JSON scalars (`true`, `"x"`, `42`) as a body,
unknown fields, wrong-typed roles, or over-limit strings crash the process
or slip through to the model/data layer.

**Mitigation**: a shared strict-body-parsing helper (`api/parsing.py`) parses
raw bytes as JSON, rejects non-object top-level values, and validates against
`extra="forbid"` Pydantic models with the exact bounds from
`spec/brain-api.openapi.yaml` — all before any business logic runs. Parsing
and validation failures are caught centrally and turned into `400
INVALID_REQUEST` without ever raising past the request boundary (a global
exception handler turns any other unhandled exception into `500
INTERNAL_ERROR` rather than crashing the process/worker).

### 3.10 Admin surface abuse

**Threat**: log data (even redacted) or the ability to set operator feedback
is exposed without authorization, or exposed in production by default.

**Mitigation**: see §3.1. Additionally, `/v1/admin/logs` responses only ever
contain already-redacted, already-hashed data (the same rows `/v1/chat`
wrote), so there is no separate redaction path to keep in sync.

## 4. Explicitly out of scope / accepted risk

- **Model-provider-side data handling** (how OpenAI processes prompts sent
  to it) is outside this repository's control; only the OpenAI dependency
  boundary itself (API key handling, timeouts, not sending secrets in
  prompts) is in scope.
- **Regex-based PII redaction is heuristic**, not a guarantee — bounded by
  the 30-day text expiry as the actual privacy backstop (§6 of `DESIGN.md`).
- **Suicidal-intent/emergency classification is a heuristic safety net**, not
  a clinical or legal determination; it is tuned to minimize missed active
  emergencies at the cost of occasional over-triggering.
- **Per-process rate limiting and SSE fan-out** do not coordinate across
  multiple instances (§7 of `DESIGN.md`).
