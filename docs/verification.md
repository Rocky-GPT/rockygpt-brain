# Brain restart checkpoint — September 4, 2026

The original checkpoint `e2ddd39` failed its later frozen acceptance run:
17/20 conversations and 24/27 turns passed semantic review. Its failures were
a program/club HTTP error, an event-to-library location inference, and an overly
broad Saturday lunch exclusion. The composite described below is historical and
does not mark that frozen checkpoint verified.

The current branch adds bounded recovery, a required evidence-support gate,
code-enforced event/source scope, discovery of small published collections,
official source fallback for uncitable record websites, retained expanded
evidence, and reserved database/network time. Menu paragraphs can retain up to
50 record citations, and inferred allergy safety receives an explicit runtime
veto. The synthesis prompt is unchanged.
Local validation: 98 Brain tests, Ruff, and strict mypy pass. The new complete
acceptance report and independent semantic review are recorded in
`rockygpt-evals/brain-reset/checkpoints/2026-09-04-behavior/` after execution.

The evidence gate and source-use interpretation still depend on a model; the
runtime checks do not guarantee correctness outside the tested conversations.

---

This checkpoint replaces the label-only classifier with a student assistant.
Implementation decisions came from the product goal and the current published
campus dataset, not historical Brain branches or designs.

## Data and execution

The inspected active dataset is `v2-20260904145456`, activated September 4 at
15:01:54 UTC. It contains 14 critical facts, 242 contacts, 77 campus-hours rows,
63 dining-hours rows, 504 menu items, four shuttle routes and 51 trips, 144
academic-date rows, 274 events, 254 clubs, 146 programs, 14 documents with 3,039
chunks, and 13 published artifacts including 3,344 catalog courses.

The checkpoint default and final live evaluation use `gpt-5.4-2026-03-05` through
the `gpt-5.4` alias. The local Brain model setting was updated accordingly;
credentials and published data were unchanged. Database inspection and evaluation
use read-only transactions. No ingestion, database migration, deployment, or push
was performed.

## Findings corrected during verification

- Added a trusted CA bundle while preserving `verify-full` and explicit CA
  settings. Moved read-only enforcement from rejected Neon startup options to
  per-query transactions, with local SQL timeouts.
- Preserved missing-data distinctions: no library-hours row, no published map,
  and missing menu dates do not imply closure or nonexistence.
- Enriched menu venue/URL from the published menu-context metadata. A query for
  the venue now finds menu rows; no-match searches are refined before claiming
  data is missing. Removed duplicate serialized evidence content.
- Restored shuttle endpoint meanings from the data contract: campus departure,
  intermediate stops in published order, and campus return. Generic departure
  and arrival labels had caused an incomplete answer about evening returns.
- Clarified evidence scope: an event room does not establish the general location
  or entrance of an entire facility. Official sources must support actual claims.
- Required explicit dates for schedule searches and supplied the current weekday
  and calendar-week boundaries. A Friday request had advanced to the following
  week, and a shuttle query had omitted its Saturday filter.
- Included dining meal labels only after an exact match to the published venue,
  day, validity, and interval sequence. Travel plans must preserve departure and
  arrival locations rather than confuse campus departures with return times.
- Described each collection's contents and required checking concise verified
  facts before declaring a public fact unavailable. A repeated password-reset
  case had searched only documents and contacts, missing the published action URL.
- Fixed client error and environment-token contracts, bounded request/answer
  sizes and active turns, and added an HTTP deadline whose worker retains its
  slot until cleanup finishes.
- Replaced machine field keys and individual menu items in citation labels with
  source titles; retained individual record identity for inspection.

The early GPT-4.1 run hit that model's project quota and also incorrectly reported
an available menu as missing. It was not counted as successful verification.
The final model's observed limits were 500 requests/minute and 500,000 tokens/minute.
A later HTTP 429 was diagnosed as `insufficient_quota` /
`credit_balance_exhausted`, with no reset headers. Model calls stopped; no credits
were purchased. This prevented completion of the last full sweep after the
collection-description change. Brain now distinguishes this non-retryable quota
failure from a temporary rate limit, and the eval harness stops on HTTP 429.

## Verification results

- Brain: 67 tests passed; Ruff and strict mypy passed. Tests cover history,
  tool output replay, citation and freshness rejection, failure recovery,
  read-only retrieval, schedule/date semantics, size bounds, and timeout cleanup.
  Exhausted credit returns HTTP 429 with a distinct non-retryable error;
  ordinary rate limits remain retryable.
- Evaluation harness: eight tests passed, including actual HTTP history replay,
  pacing, resumable reports, and stopping immediately on HTTP 429.
- Student UI: four Playwright HTTP-route tests passed for payload validation,
  exact history forwarding, upstream failures, and per-client rate limiting.
  Four additional browser checks passed across desktop and mobile: exhausted
  quota preserves the service message without a retry button, while temporary
  rate limits retain retry. These browser requests used a local mock.
- Both web clients passed TypeScript checks and lint. Live CLI → Dev → Brain
  follow-ups preserved all four messages. UI → Brain readiness and malformed
  chat smoke checks passed without a model call.
- The Brain wheel built successfully and included the runtime prompt without
  credentials or retired classifier files.

The [reviewed composite](../../rockygpt-evals/brain-reset/checkpoints/2026-09-04/verified-checkpoint.md)
contains 20 conversations / 27 turns, including four actual multi-turn
conversations. All composite requests returned HTTP 200 and passed contract
checks. An [independent agent review](../../rockygpt-evals/brain-reset/verification-review.md)
corroborated the campus claims against published records and found no remaining
material unsupported claim in that answer set. The corrected shuttle case also
passed two independent repetitions.

Composite HTTP latency was 6.680 seconds median, 11.644 seconds at the
nearest-rank 95th percentile, and 12.028 seconds maximum. These are sequential
sample timings, not a load test.

This is a composite across iterations, not a complete pass on the final runtime.
It uses the last complete run with the password case replaced by its successful
targeted rerun after the collection-discovery correction. The global effects of
that final prompt/tool-description change remain unverified across all 27 turns
because API credits were exhausted. Original failure reports remain available.

Only synthetic conversations were used. Contract checks and independent agent
semantic review are separate; no human review was performed. The TestClient
emits one upstream deprecation warning about its HTTP client, without a failing
check.

## Practical limits

Citation membership, source trust, freshness, validity, and request bounds are
checked in code. Semantic entailment still depends on the model and requires
ongoing evaluation; source presence alone is not a factual-accuracy score.
Search returns bounded results with explicit truncation, so lists may be partial.
Historical assertions from sources beyond their freshness SLA are conservatively
withheld. Live seat counts, personal accounts, map directions, appointment
availability, and live shuttle telemetry are unavailable in the current data.

This is a local source checkpoint. The pre-existing Docker stack still lacks a
Python Brain Dockerfile; deployment was outside this request. The current Brain
serves chat and probes, not the old campus-panel, feedback, or admin-log APIs.
