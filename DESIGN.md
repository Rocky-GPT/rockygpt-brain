# RockyGPT Hybrid V1

This is a greenfield brain. Its architecture is derived only from the public UI,
Brain, DATA, deployment, and acceptance contracts. Previous brain source is not
an implementation input.

## Core architecture

```text
question
  -> AI #1: UNDERSTAND (typed structured intent)
  -> Python BRAIN: choose exactly the needed path
       CODE | RAG | MEMORY | GENERAL | SAFETY
  -> typed result JSON
  -> AI #2: COMMUNICATE
  -> human-facing answer
```

CODE versus RAG is the central hybrid decision. Objective, computable campus
questions go to deterministic code over structured DATA. Document, policy, and
prose questions go to retrieval. Memory answers conversation questions; GENERAL
is restricted to confirmed non-campus questions; deterministic SAFETY may
intercept before AI #1. AI #2 receives results and communicates them—it does not
recalculate shuttle times or invent facts. `RoutePlan` is only the validated
wire shape emitted by AI #1, not an agent framework. The supporting security,
evidence, memory, and compatibility layers may constrain or verify this pipeline
but must not obscure it.

## Request lifecycle

```text
validate/authenticate
  -> deterministic safety and privacy preflight
  -> schema-validated RoutePlan (zero to four allowlisted operations)
  -> execute capability, retrieval, and conversation-memory reads
  -> normalize records into an EvidenceBundle
  -> generate a structured draft that names evidence IDs
  -> post-generation grounding and policy validation
  -> project through the external API compatibility adapter
  -> atomically persist the successful turn and memory updates
  -> return one JSON response
```

The planner and writer are bounded model calls, not an autonomous tool loop.
Invalid structured output gets one repair attempt. A draft with unknown evidence
IDs or unsupported campus claims gets one correction attempt; otherwise the
brain returns a conservative verified answer.

Safety and privacy preflight happens before any model or DATA call. Output policy
and grounding run after draft generation, because model text is untrusted until
then.

## Modules

- `app`: FastAPI routes and the compatibility adapter for the frozen Brain API.
- `brain`: the readable request coordinator implementing the lifecycle above.
- `planning`: typed `RoutePlan` parsing and capability allowlisting.
- `capabilities`: small domain adapters; deterministic operations stay in DATA.
- `data_client`: typed HTTP-only access to DATA `/v2` contracts.
- `evidence`: provenance registry, normalized facts, and grounding validation.
- `model`: stateless OpenAI Responses API adapter using strict JSON-schema output
  and `store=false`.
- `memory`: bounded recent turns, assistant-claim ledger, entity/correction state.
- `persistence`: brain-owned PostgreSQL logs, feedback, retention, and memory.
- `policy`: deterministic emergency, privacy, secret, and output checks.

The external response shape, admin log shape, and persistence schema are edge
contracts; they do not determine internal routing or capability design.

## RoutePlan

`RoutePlan.mode` is one of `general`, `conversation`, `capability`, `rag`,
`composite`, `clarify`, or `policy`. It contains at most four operations. Each
operation has an allowlisted name and validated typed arguments. Campus intent
that is unsupported or unavailable never falls through to general model
knowledge. Composite partial success returns verified portions and identifies
the unavailable portions.

For shuttle planning, the brain normalizes language into distinct `route`,
`origin`, `destination`, `serviceDate`, `serviceDay`, `selection`, and
`timeScope` fields. `first` means `selection=first,timeScope=full_day`; `next`
means `selection=next,timeScope=remaining`.

## Capability result and evidence

Every capability returns:

```text
outcome: success | empty | no_match | needs_clarification |
         unsupported | unavailable | error
records: typed records
completeness: complete | partial | unknown, plus returned/matched/limit/truncated
appliedFilters and deterministic ordering
dataset identity/version
evidence: immutable IDs plus server-owned public source metadata
warnings and safe error code when applicable
```

Only DATA can declare corpus completeness. BRAIN may preserve or weaken that
declaration, never strengthen it. DATA performs `retrieve -> filter -> sort ->
bound -> declare completeness`.

Evidence registries are scoped to one turn, but every accepted claim persists a
durable turn/evidence mapping and the server-owned evidence snapshot needed to
audit it later. Public citation titles and URLs are resolved from that registry,
never copied from model output. Conversation evidence may support only claims
about the conversation. RAG text is untrusted content and can neither change
instructions nor select capabilities. An authoritative empty result still carries
dataset/source evidence so that negative claims are auditable.

## Time

One immutable `TimeContext` is created at request start from the optional pinned
`now`, the requested IANA timezone, and the campus timezone
`America/New_York`. `now` fixes the instant. The validated request timezone is
authoritative for interpreting relative dates such as today and tomorrow;
published schedule clock times and next/current comparisons use the campus
timezone at that same instant. `serviceDay` is derived only from `serviceDate`,
and a supplied inconsistent pair is rejected. DATA owns schedule parsing,
DST-aware local comparison, cross-midnight behavior, and half-open opening
intervals. Different operations never call their own wall clocks.

## Memory and persistence

Memory is keyed by separate HMAC-SHA256 hashes of visitor and conversation IDs;
neither identifier grants authorization. It contains:

1. a bounded recent-turn window;
2. an exact assistant utterance/normalized-claim ledger with evidence IDs;
3. bounded selected entities and explicit corrections with attribution.

User corrections remain user claims until independently verified. Sensitive
facts are not promoted to durable entity memory. The server-owned claim ledger
outranks client-supplied history; the last ten client history entries are only a
bounded fallback and can never overwrite durable state. A successful response,
its evidence snapshot, and its memory update commit together. Failed accepted
attempts record only redacted operational/error metadata and never change
conversational state. Concurrent updates use database transactions and
monotonically ordered turns. Question, answer, and claim text expires within 30
days; feedback and non-text operational metadata expires within 90 days.

## External API and security

`spec/brain-api.openapi.yaml` is normative. The service returns a single complete
JSON response within the UI's 60-second timeout and implements probes, chat,
feedback, authenticated development logs, operator feedback, and SSE change
notifications. Request bodies are capped at 64 KiB. Malformed JSON, JSON scalars,
unknown fields, and schema violations normalize to the documented HTTP 400
envelope; oversized bodies use HTTP 413. `X-Request-Id` is present on every
finite HTTP response and on the SSE handshake, while a body request ID appears
only in schemas that permit it (chat and error). Framework documentation and
implicit OpenAPI routes are disabled so every runtime route remains represented
by the normative checked-in specification. Readiness checks local model
configuration presence as well as required DATA/database dependencies without a
live model call. Staging environment tokens, admin bearer authentication, and
constant-time signed-client verification are compatibility requirements.

All capabilities are public and read-only. Grades, GPA, private student data,
private addresses, secrets, and account actions are unsupported. Active fire,
weapon use, unconsciousness, and suicidal intent take deterministic policy paths;
informational safety questions do not.

## Budgets

- overall brain deadline: 55 seconds (below the UI's 60-second timeout)
- validation, identity, policy preflight, and compatibility projection: 2 seconds
- routing including its single repair attempt: 8 seconds total
- at most four parallel DATA capability/retrieval operations, including one safe
  transient retry: 5 seconds total
- drafting including its single grounding correction attempt: 24 seconds total
- deterministic grounding and output-policy verification: 2 seconds
- atomic persistence: 3 seconds
- scheduling, transport, and response buffer: 11 seconds
- no retry for deterministic 4xx; one retry only for safe transient DATA/model
  failures when the remaining deadline permits it

Tests inject clocks and fake model/DATA ports. Prompts, model name, code SHA, and
DATA release version are recorded for the frozen integration build.
