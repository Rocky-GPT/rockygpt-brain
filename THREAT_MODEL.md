# Threat model

This threat model covers the greenfield Hybrid V1 described by `DESIGN.md`.

## Assets and boundaries

Protected assets are conversation/feedback content, pseudonymous identifiers,
OpenAI/database/admin/staging secrets, service availability, conversation
integrity, and the source provenance behind campus claims.

```text
untrusted browser/direct caller
  -> UI proxy or Brain API
  -> Hybrid V1 brain
       -> read-only DATA HTTP API
       -> OpenAI Responses API (store=false)
       -> brain-owned PostgreSQL
```

Every incoming field remains untrusted even when it passed through the UI. DATA
records are trusted as campus evidence according to their source tier; RAG text
is untrusted content carried inside that evidence.

## Model control boundary

The architectural security invariant is:

```text
AI #1 UNDERSTAND
  -> Python dispatch: CODE | RAG | MEMORY | GENERAL | SAFETY
  -> typed JSON
  -> AI #2 COMMUNICATE
```

AI #1 can propose only a schema-valid, allowlisted plan. Python dispatches at
most four bounded operations. The model never runs an autonomous tool loop and
cannot invent a new operation. AI #2 receives typed result/evidence JSON; Python
then validates its evidence references and public response.

CODE and RAG stay deliberately separate:

- CODE handles objective structured operations. DATA owns deterministic
  filtering, sorting, bounding, interval/date behavior, and completeness.
- RAG handles prose. Retrieved chunks cannot select capabilities, modify system
  instructions, or become source metadata merely because their text says so.

GENERAL is permitted only for confirmed non-campus questions. MEMORY supports
conversation truth, not current campus truth. SAFETY can bypass model routing
for deterministic emergency and privacy responses.

## Principal threats and controls

### Forged identity or unauthorized access

- Functional staging routes compare `x-rockygpt-environment-token` with the
  configured staging secret; probes remain public.
- Admin routes independently require a bearer token and are not exposed as
  public production functionality.
- A client key is trusted only after constant-time HMAC-SHA256 verification with
  `ABUSE_HASH_KEY`. Missing/invalid signatures share a stable fail-closed abuse
  bucket, so rotating caller-controlled IDs cannot bypass the limiter.
- Visitor/conversation IDs are high-entropy opaque correlation inputs, not
  authentication or authorization. No private capability relies on them.

### Sensitive data at rest or in telemetry

- Raw IPs, abuse client keys, visitor IDs, and conversation IDs are never
  persisted. Visitor and conversation identifiers receive independent keyed,
  domain-separated HMAC transformations before storage.
- Stored questions, answers, claims, and feedback comments are redacted for
  student IDs, email, phone, payment/SSN-like numbers, and common secrets.
- Application telemetry uses allowlisted structural fields such as request ID,
  selected path, safe argument keys, status, evidence count, latency, and failure
  category; it excludes prompts, raw message text, headers, and secrets.
- Redacted question/answer/claim text expires within 30 days. Redacted feedback
  and non-text operational metadata expire within 90 days.

Redaction is heuristic defense in depth; bounded retention is the backstop.

### Fabricated, stale, or lost provenance

- Public citation title/URL metadata is resolved server-side from real DATA/RAG
  evidence, never copied from either AI response.
- Request-local evidence IDs are valid only inside one turn. Successful turns
  persist durable evidence snapshots containing the source identity, public
  metadata, dataset/index version, and the claim association needed by later
  conversation-truth queries.
- AI #2 may cite only evidence IDs supplied in its typed input. Unknown IDs and
  unsupported campus claims trigger correction or a conservative answer.
- Client-supplied history can help resolve a referent but cannot overwrite the
  server-owned assistant-claim ledger or prove what Rocky previously said.

### Failure-state corruption

- A successful response, memory update, and evidence snapshot commit together.
- An accepted failed turn may create a redacted operational error record and
  increment failure metrics, but it never mutates recent turns, assistant
  claims, entities, or corrections.
- User corrections remain attributed user claims until current evidence verifies
  them.

### Prompt injection and unsafe disclosure

- Safety/privacy preflight runs before model or DATA access for secrets, private
  student data, grades/GPA, private addresses, and unauthorized actions.
- RAG text and user content cannot alter the dispatcher allowlist or policy.
- Post-generation grounding and output policy treat AI #2 text as untrusted.
  Credential-, SSN-, payment-, and student-ID-like output is rejected before it
  reaches the client; a second invalid draft becomes a fixed safe response.
- Active fire, weapon use, unconsciousness, and suicidal intent take
  deterministic emergency paths; informational safety questions remain normal
  evidence-backed requests.

### Resource exhaustion and dependency failure

- The ASGI edge rejects bodies over 64 KiB before JSON/model work, validating
  both `Content-Length` and actual bytes read.
- Strict schemas bound message/history lengths and operation counts.
- Dependency calls and the two model calls have explicit timeouts inside a
  55-second overall deadline; retries occur only when the remaining budget can
  still produce a valid response.
- Rate-limit failures use the frozen 429 envelope and numeric `Retry-After`.
- Process-local rate-limit key state is bounded and expired; unsigned chat and
  feedback use shared fail-closed buckets rather than caller-selected IDs.
- Readiness stays under three seconds, checks database and DATA reachability, and
  verifies required model configuration without a paid/live model call.

## Residual and implementation-dependent risk

- OpenAI still processes prompt content even with Responses API `store=false`;
  provider-side handling remains an external dependency risk.
- Free-text redaction and emergency classification cannot be perfect.
- Rate limiting is process-local in this milestone and therefore must move to a
  shared store before horizontal scaling. Log/SSE change notification already
  uses a durable PostgreSQL watermark with bounded polling.
- Forward database migrations and encrypted backup procedures depend on the
  deployment platform and must be documented before promotion. Retention runs
  at startup and hourly; graceful shutdown closes DATA and PostgreSQL clients.
