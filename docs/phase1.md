# Phase 1: baseline, configuration, and accounting

Implemented September 11, 2026. This is the accounting foundation, with the
existing answer/review/repair behavior retained. No paid experiments, campus
schema changes, model selection, or deployment were performed.

## Baseline and release identity

`phase1-baseline/manifest.json` records current-branch HEAD, dirty-state notice,
and source/configuration/regression hashes before implementation. The user’s
existing `plan.md` edits are preserved. The snapshot includes the public OpenAPI
schema, 20 conversations / 27 scored turns, 27 evidence-review cases, and 77
unique public evidence record representations extracted from that corpus.
These dated records form a representative frozen subset; they are not a new
live dataset export or a claim of present-day freshness. Current source smoke
checks and fresh model validation belong to later milestones.

`src/rockygpt_brain/release.json` is packaged into the wheel. Both environments
load the same file. It retains `gpt-5.4`, makes its documented default draft
reasoning (`none`) explicit, and retains `medium` review reasoning and the
existing call, output, tool, and time limits. Model changes require a new release;
Phase 1 makes no claim that this baseline meets the eventual $10 traffic target.

Run `python -m rockygpt_brain.config` to obtain the configuration hash. It includes
the release, prompts, schemas, retrieval/rendering code, API limits, and installed
OpenAI, HTTPX, psycopg, Pydantic, and FastAPI versions. Set the same
`BRAIN_EXPECTED_CONFIG_HASH` in both environments when promoting a tested wheel.
A mismatch stops requests. Requested aliases, observed returned model identities,
configuration hash, project, price version, and operation creation time are
recorded on every reservation. An unexpected returned model pauses the affected
environment pending review.

## Provisioning

1. Apply `migrations/001_accounting.sql` once with an administrator connection to
   the operational PostgreSQL database. It adds `brain_ops` and two NOLOGIN roles;
   it neither changes nor grants access to campus tables. Brain never migrates on
   startup. Back up and retain this schema across restarts and deployments.
2. Provision distinct login credentials and grant each login only its matching
   `brain_development` or `brain_production` role. Use normal password/TLS secret
   provisioning; do not grant either runtime login both roles, superuser,
   `BYPASSRLS`, schema ownership, or authority to alter budget caps. PostgreSQL
   verifies `SET ROLE`; row-level policies isolate accounts, operations, and turns.
3. Create separate OpenAI projects and keys. Mount only the selected environment’s
   key/project and operational connection into its process. Set `BRAIN_ENVIRONMENT`,
   `BRAIN_OPENAI_API_KEY`, `BRAIN_OPENAI_PROJECT`, and `BRAIN_LEDGER_DATABASE_URL`.
   Keep the separate `DATABASE_URL` campus role read-only. Existing service-token
   protection is unchanged. Legacy `OPENAI_API_KEY` is not a fallback.
4. Reconcile any existing usage from the dedicated project before opening traffic.
   Fresh dedicated projects avoid a historical external-spend gap. Other scripts
   must not receive these keys: application accounting cannot intercept calls made
   outside the application. Provider dashboard alerts supplement the ledger.
5. Verify release/price identity and `/readiness`, then start Brain normally.
   Missing or unusable accounting configuration stops paid execution.

The migration seeds **$10 per environment**, enforced with integer USD
nanodollars (`$1 = 1,000,000,000`). Runtime roles cannot change the caps. The two
balances cannot be pooled. Infrastructure and taxes are outside this allowance.

## Admission and uncertain charges

All current Brain calls, including retries for invalid drafts/reviews, use
`PaidGateway`. Standalone evidence-review evaluation uses the same gateway and
requires a development deployment. HTTP evaluation is charged by its configured
Brain server. The historical comparison runner’s live execution is retired; its
report helpers remain usable. New providers, embeddings, graders, and batch
operations require explicit later implementation through this boundary.

The adapter allows bounded text Responses calls with local function tools only.
It disables SDK retries, remote conversation state, truncation, and built-in
paid tools, and forces the standard service tier. The conservative input bound
is twice the complete serialized UTF-8 content bytes plus 8,192 framing tokens,
including instructions, schemas, tools, and continuation items. Inputs above
128,000 estimated tokens are rejected explicitly. Output ceilings include
reasoning tokens. No cache discount is assumed when reserving. This bound is
intentionally loose; it can deny a large request that a tokenizer might fit.
A measured bound violation records actual liability and pauses the environment.

Each reservation locks its environment account row in a short transaction,
checks settled current-month charges plus **all** unsettled reservations, and
commits before calling the SDK. Independent requests/processes serialize only
this admission transaction. Every operation has a durable UUID. Reusing an
admitted ID cannot authorize another execution. The usage record stores the
request/category and complete original price/bound metadata, without input text.

Completed, incomplete, or subsequently rejected responses are charged from
provider usage. Cached input is a subset of input; reasoning is a subset of
output and is not charged twice. Missing or malformed usage, timeouts,
cancellation, provider errors, and ambiguous settlement failures retain the
original reservation. No error is assumed to be free. A crash between reservation
and transmission can therefore leave a hold even if nothing was sent.

Accounting months use `America/New_York` and the actual operation clock, never a
fixture’s campus date. Open holds survive every month boundary. Delayed usage is
conservatively charged in its settlement month (or the admission month, if later).
The original admission month/time remain available for matching provider billing
records, which may use a different calendar boundary. This intentionally may
reduce a later month’s available balance; it never silently frees an unknown hold.
The displayed reset is the next calendar boundary, not a promise that unresolved
holds will disappear.

## Pricing and reconciliation

The bundled standard text prices were checked September 11: $2.50 input,
$0.25 cached input, and $15.00 output per million tokens. The price file expires
October 11; update it from official documentation and release the exact tested
configuration before then. The bounded input size stays below long-context
premium thresholds. Rates and output/reasoning semantics come from the
[GPT-5.4 model documentation](https://developers.openai.com/api/docs/models/gpt-5.4)
and [Responses reference](https://developers.openai.com/api/reference/python/resources/responses/methods/create).

Inspect a turn without a model call:

```sh
python -m rockygpt_brain.accounting --request-id REQUEST_ID
```

To reconcile an uncertain operation, obtain authoritative provider usage or a
provider confirmation of zero usage. Prepare a receipt with the following fields
using the actual operation and response identifiers:

```json
{
  "operation_id": "00000000-0000-0000-0000-000000000001",
  "provider_response_id": "provider-response-or-confirmation-id",
  "returned_model": "gpt-5.4-2026-03-05",
  "evidence_reference": "provider-export:verified-row-reference",
  "input_tokens": 100,
  "cached_input_tokens": 20,
  "output_tokens": 40,
  "reasoning_tokens": 30
}
```

```sh
python -m rockygpt_brain.reconcile receipt.json
```

This operator action uses the saved original price even after a release/expiry,
validates token subsets, records the receipt reference, and settles idempotently.
Contradictory repeat settlements are rejected. It does not call the provider or
infer usage from elapsed time. If the provider cannot establish usage, leave the
hold open. Unexpected external usage or invoice differences require administrator
reconciliation and a paused account until resolved; this is not an automated
provider billing importer. Never delete operations to restore budget. An overrun
or changed model requires investigation before an administrator unpauses the
account.

## Instrumentation and HTTP behavior

`brain_ops.operations` explains each charge: request/operation ID, category,
original price and input/output bounds, requested/returned model, normalized token
categories, elapsed model time, known cost, and uncertain status/error code.
`brain_ops.turns` persists text-free complete-turn summaries, including cost,
unsettled commitments, dataset/configuration identity, fallback flag, retrieval
time, and draft/review time. A `brain_turn` JSON log is also emitted through the
Uvicorn logger. A process crash can leave an operation without a completed turn
summary; it remains reserved and visible in the ledger.

Public metrics add token categories, draft/review time, retrieval time, fallback
status, and `usageComplete`; detailed costs and operation metadata stay in the
operational store. `costNusd` totals only settled known charges; always read it
with `unsettledNusd` and `usageComplete`. Draft time includes planning/tool-request
calls as well as answer writing. Fallback frequency can be aggregated from turn
summaries. Phase 1 retains the existing model-generated unavailable path; the
plan’s deterministic answer/fallback changes come later.

The public request and successful-response shapes remain compatible. Exhaustion
returns a non-retryable `budget_exhausted` error, New York reset timestamp, and
bounded official links from the published source catalog when reachable. Ledger,
price, and configuration failures are non-retryable configuration states. No
model is called to explain a limit. If campus retrieval also fails, the limit
response still works without fabricated resource links.

## Verification

The final local check passed 168 Brain tests (including PostgreSQL), 13 evaluation
tests, Ruff, and strict mypy. A real PostgreSQL restart preserved an uncertain
full-budget hold and continued to deny spending. The wheel includes the release
configuration and prompts, and the public OpenAPI schema matches the captured
baseline exactly. See `phase1-verification.json` for the tested identity.

Use a disposable PostgreSQL 16 instance, bound to localhost, with database name
`brain_accounting_test`. Tests refuse other hosts/database names. The CI workflow
provisions this automatically. For a local Docker instance:

```sh
docker run -d --rm --name rockygpt-phase1-postgres \
  -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_DB=brain_accounting_test \
  -p 127.0.0.1:55439:5432 postgres:16
BRAIN_TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55439/brain_accounting_test pytest -q
```

Run Ruff and strict mypy as documented in the README. The database suite applies
the migration and covers independent grants/balances, threaded and multiprocess
admission, repeated operation IDs, process recovery, unknown usage, delayed
reconciliation, exact caps, overrun pauses, New York/DST/year boundaries, and
complete three-call turn accounting. Provider tests are mocked; no test uses AI
credits. Without the explicit test database variable the PostgreSQL cases are
skipped, so that run alone is insufficient for accounting acceptance.

This phase does not establish production answer quality, paid-call latency, or
traffic affordability. Fresh acceptance/model comparisons and deployment remain
later plan work.
