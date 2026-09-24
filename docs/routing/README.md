# Jev routing

Routing is implemented but defaults to **off**. Live validation is pending a TypeSafe
or OpenRouter key; `live-status.json` records the preflight result. No paid
evaluation calls have been made and no environment has been promoted.

## Behavior

`BRAIN_ROUTING_MODE=shadow` classifies requests and records metrics while preserving
GPT tool selection. `active` allows Jev to choose the first tool or directly execute
a validated contact/profile lookup. Both modes incur paid calls. `off` makes no
Jev calls and needs no TypeSafe credential.

Jev sees the accepted conversation with the current request separated from prior
messages. Candidates come from the active release's curated identities, capped at
24 and ranked by current exact name/alias matches, previous mentions, then lexical
overlap. Candidates are selectors, not evidence or new identity links.

The router uses one request to pinned `jev-1.13.0`. Decisions require probability
and confidence ≥0.90. Field/section probabilities ≥0.90 include, ≤0.10 exclude,
and intermediate values defer. A separate question checks whether a complete
single-entity lookup is representable. Ambiguity, unsupported qualifiers, complex
dates, and unresolved arguments defer to GPT. Simple dates reuse the existing
campus-local resolver; meal labels are request filters, never proof of availability.

Contact and all existing profile sections can run directly. Every lookup still uses
the ordinary schema validation, read-only retrieval, context bounds, evidence
collection, trace, and exact-answer checks. Eligible contact requests can complete
without GPT. Other direct results feed GPT synthesis and the existing evidence
review. Jev never certifies a factual answer or resolves conflicting evidence.

When only a tool is resolved, GPT's first call is constrained to that tool; later
calls regain all tools. Uncertain, general, and mixed routes retain the ordinary
flow. No new frontend flow or request field is required.

The routing deadline is two seconds, includes preparation, and gives the HTTP
attempt only its remaining allowance. HTTP cancellation covers the entire response
body, not just individual socket reads. There are no automatic retries. Durable
accounting cleanup must finish before further paid work. Routing consumes the
existing 45-second turn allowance; the four-call GPT ceiling and review reserves
remain unchanged, with at most one additional paid routing call.

Provider failures, invalid answers, oversized context, expired routing prices, and
late decisions fall back. Accounting failures and exhausted spending limits stop
paid work. Unknown usage retains its reservation and requires reconciliation.

## Setup and migration

1. Apply `migrations/003_routing_accounting.sql` after migrations 001 and 002 using
   the operational database administrator. It only adds `routing` to the operation
   category constraint; balances, row-level security, and campus schemas are unchanged.
2. Set the server-only `BRAIN_TYPESAFE_API_KEY` for the intended environment, or set
   `BRAIN_ROUTING_PROVIDER=openrouter` and `BRAIN_OPENROUTER_API_KEY` to reach Jev
   through OpenRouter. Never expose either key through a `NEXT_PUBLIC_` setting. Keep
   `BRAIN_ROUTING_MODE=off` for rollout.
3. Update any `BRAIN_EXPECTED_CONFIG_HASH` deployment pin using
   `python -m rockygpt_brain.config`. Routing prompts, code, model and prices are
   covered by the hash; the deployment's off/shadow/active mode is logged separately.
4. Run the development evaluation below before enabling active routing.

Pricing is versioned in `release.json`: 42 nanodollars per input token, zero per
output token, matching [TypeSafe's model documentation](https://docs.typesafe.ai/models).
Both providers share the existing monthly and per-turn allowances. API documentation:
[TypeSafe evaluation endpoint](https://docs.typesafe.ai/api).

### Through OpenRouter

`BRAIN_ROUTING_PROVIDER=openrouter` sends the same request to OpenRouter's
[System One endpoint](https://openrouter.ai/docs/api/api-reference/systemone/submit-a-system-one-request),
which forwards it to TypeSafe. OpenRouter lists `typesafe/jev-1.13` at the same
per-token price, so the `release.json` price still applies. Any fee OpenRouter charges
for buying credits is outside the ledger.

The brain requests `typesafe/jev-1.13` and accepts only the reported snapshot
`typesafe/jev-1.13-20260917` as `jev-1.13.0`. That snapshot name comes from
OpenRouter's API reference example, so confirm it on the first paid call. Any other
reported model is billed, recorded in the ledger and falls back as
`routing_model_changed`. The ledger names `openrouter` as the provider and keeps
OpenRouter's generation ID as the receipt. Evaluation reports record `routingProvider`,
because latency through OpenRouter includes an extra hop.

## Evaluation and promotion

Run from the Brain directory, with development credentials and the migration applied:

```sh
.venv/bin/python scripts/evaluate_routing.py --output /tmp/jev-comparison.json
```

This runs the 30 fixed synthetic cases in `cases.json` three times, pairing off and
active modes and alternating their order. Both halves of a pair use the same campus
time. Every paid call goes through the ledger. Missing credentials stop before any
paid work; paid-call errors stop the run. Reports include answers/citations for
review, request costs including Jev, elapsed time, selected route, and dataset version.
These are synthetic evaluation artifacts, not stored student conversations.

Review each paired answer against its cited evidence and the original fixture.
Check completeness, current subject, dates, unsupported assertions, and honest
limitations. Create a review file of this form, with every pair ID included:

```json
{
  "reportDigest": "copy gates.reportDigest from the comparison report",
  "pairs": {
    "registrar-phone:0": {"baselinePass": true, "activePass": true}
  }
}
```

Then recompute gates without more paid calls:

```sh
.venv/bin/python scripts/evaluate_routing.py \
  --report /tmp/jev-comparison.json --quality-review /tmp/jev-quality.json \
  --output /tmp/jev-reviewed.json
```

Development requires ≥95% precision among resolved routes, reviewed answer quality,
complete billing, stable paired datasets, and lower mean cost and latency on eligible
direct lookups. Production additionally requires lower overall mean cost and median
latency, and p95 latency no more than 5% above baseline. Review approvals are bound
to the report digest. The runner never changes deployment settings automatically.
If gates fail or validation is incomplete, keep active routing off.

To roll back, set `BRAIN_ROUTING_MODE=off` and restart the Brain. The additive ledger
migration can remain applied; existing routing charges retain their audit history.

## Observability and privacy

`metrics.routing` contains mode, model, version, route, confidence, direct retrieval,
fallback reason, and elapsed milliseconds. `routingCalls` and `routingModelMs` count
actual admitted calls at the paid boundary; `modelCalls` includes both providers.
Direct exact answers identify Jev in the existing `model` field; GPT-written answers
continue identifying GPT. Billing amounts remain operational metadata.

Chat operational summaries now omit question text, message history, answers and
citations. They persist routing decisions and existing text-free usage metadata.
Consequently, new turns do not populate the legacy `/v1/logs` conversation history,
which filters for stored questions. Historical rows are not deleted. Explicitly
submitted feedback remains a separate, unchanged feature.
