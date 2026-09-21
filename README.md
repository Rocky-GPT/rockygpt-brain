# RockyGPT Brain

A student assistant for Ramapo College, grounded in the currently published
campus dataset. One model-driven conversation loop searches and reads official
campus evidence. Eligible single contact questions are rendered from validated
fields and citations in code after one tool-request call. Generated prose is
checked by a separate evidence review before it is returned. There is no intent classifier,
phrase routing, vector service, process conversation memory, or generated SQL.

Combined contact/hours requests can use `lookup_profile`, which resolves a
published `campus-identities` release artifact and retrieves its exact linked
record keys from that same dataset. Persistent UUIDs identify entities; source
record IDs, sources, collection times, and freshness remain separate. No name
similarity establishes a link. Missing components and conflicting fields remain
explicit without discarding independent facts. Operating schedules with unknown
availability scope never establish phone-answering or staff hours. Profiles use
the ordinary generated-answer evidence review; they have no exact-answer bypass.
Older releases without the artifact report that profile linking is unavailable.

Profile sections include `club` and `event`. Clubs retain the directory's published
category and contact/social links; a listing does not establish current meetings
or membership. Events represent individual RSVP instances, not academic programs
or operating schedules. A null profile date retains an event's published occurrence;
an explicit date filters in `America/New_York`. Repeated titles require a date or
persistent identity, and missing times/locations remain unknown. Exact row IDs
qualify colliding legacy event keys while preserving the original keys. An
`organized_by` relationship requires explicit event-page group identity evidence;
organizer or venue name similarity is insufficient. Separate page captures keep
their own timestamps, and conflicting organizer assertions are not resolved by recency.
The club `event` section follows incoming approved organizer links and rechecks
their evidence, retaining each event's identity. It inspects at most 20 candidate
events and reports unexamined candidates; this is not a complete event calendar.
Date disambiguation likewise checks at most 20 identically named instances and
otherwise retains ambiguity. Existing event search remains available for broader queries.

## Run

```sh
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
uvicorn rockygpt_brain.api.app:app --host 127.0.0.1 --port 8000
```

`.env` loads from the working directory without overriding exported variables.
Set the environment-owned credentials in `.env.example` and apply the separate
operational migrations using the [Phase 1 setup guide](docs/phase1.md). `DATABASE_URL`
retains read-only SELECT access to published `rockygpt_v2` tables. The operational
connection receives only its matching `brain_development` or `brain_production`
role. Missing accounting configuration stops paid calls.

The bundled `release.json` fixes the shared baseline at `gpt-5.4`, explicit `none`
draft reasoning and `medium` review reasoning. No model comparison or selection
is part of Phase 1. Legacy API-key fallback is removed; a conflicting
`OPENAI_CHAT_MODEL` override is rejected. Each environment has an independent
$10 default monthly allowance. Explicit administrator-recorded development
supplements expire at the end of their approved month; see [Phase 3](docs/phase3.md).
`python -m rockygpt_brain.config` prints the release hash.
If `STAGING_SERVICE_TOKEN` is set, chat requires the matching
`x-rockygpt-environment-token` header. Keep it identical in UI and Dev.

## HTTP contract

- `GET /health` and `HEAD /health`: process liveness, independent of dependencies.
- `GET /readiness`: checks deployment configuration, price validity, the operational ledger,
  and a read-only campus connection with an active release. This does not call the model or prove every
  source is fresh; freshness is checked when records are retrieved.
- `POST /v1/chat`: one JSON response, with the complete conversation supplied
  on every request. The generated schema is served at `/openapi.json`.

```json
{"messages":[{"role":"user","content":"How do I contact Financial Aid?"}]}
```

The response has `answer` (Markdown with validated source links), `status`
(`answered`, `partial`, `clarification`, or `unavailable`), `citations`, `model`,
`requestId`, `datasetVersion`, `elapsedMs`, `trace`, and `metrics`. Each citation includes
its evidence ID, title, URL, collection, collection timestamp, freshness, trust,
validity, and limitations. Trace exposes only tool names, arguments, result
counts, search coverage, statuses, and duration; it does not contain model reasoning.
Metrics distinguish draft and review model calls, requested and executed tools,
and fixed validation-failure codes. Rejected answer text and review explanations
are not returned. Invalid-output logs contain only the request ID and reason code.

Clients append the returned answer as an assistant message before the next user
message. Prior assistant text resolves references but is not authoritative;
campus evidence is retrieved anew on each turn. Failed requests are not appended.
No student text or conversations are persisted by Brain. The operational ledger
persists request IDs, release identity, token/cost metadata, and turn summaries. Model calls use
`store=false`; this does not alter the provider's account-level retention policy.

Requests are capped at 64 KiB, 80 messages, 16,000 characters per message, and
48,000 total content characters. Oversized histories are rejected explicitly.
Answers are capped at 12,000 characters so they fit in a subsequent request.
There are at most four active turns per process, eight model calls in total
(at most six draft/tool calls, with capacity reserved for review), twelve admitted tool attempts, a
50-second execution budget, and a 52-second HTTP deadline. Retrieval has a
30-second deadline, leaving time for synthesis and review. Exhausting retrieval
does not disable answer repair. Provider connection attempts are capped at two
seconds per address; model reads share the remaining turn budget. Timed-out
workers retain their slot until provider and database cleanup finish. Validation errors use HTTP 422; upstream
errors use 429/502/503/504 with a safe structured error and a request ID.

Campus panel, feedback, admin-log, and classifier endpoints are not part of this
Brain. The student and Dev chat interfaces use the contract above. Campus retrieval remains read-only. The separate operational schema records
reservations, settlements, uncertain usage, and text-free turn metrics.

Budget exhaustion returns HTTP 429 with `error.code=budget_exhausted`,
`retryable=false`, the next New York month boundary in `resetAt`, and published
source-catalog resources when available. Unknown usage retains its reservation
across restarts and month changes. Expired prices or an unavailable ledger stop
paid work. A conservative input ceiling returns `context_limit` without silently
truncating conversation history.

## Verification

```sh
ruff check .
mypy src tests
pytest -q
# Also required for accounting changes (disposable localhost database only):
BRAIN_TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55439/brain_accounting_test pytest -q
```

Run realistic live conversations from the sibling eval repository:

```sh
python ../rockygpt-evals/brain-reset/run.py --base-url http://127.0.0.1:8000 \
  --output ../rockygpt-evals/brain-reset/results/checkpoint.json
```

The harness checks contracts and replays actual generated conversation history.
Review answers against retrieved facts as well: citation presence does not prove
that a source supports every assertion. See [architecture](spec/system-boundaries.md)
and [checkpoint verification](docs/verification.md).

Phase 2 implementation, frozen retrieval measurements, browser checks, and the
remaining live-configuration blocker are recorded in [Phase 2](docs/phase2.md).
