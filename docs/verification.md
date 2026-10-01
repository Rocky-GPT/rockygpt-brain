# Current verification

This document describes checks for the bounded office-facts rebuild. The
[September 4 verification report](historical/verification-20260904.md) is historical
and does not certify the current code.

## Checks with no paid calls

```sh
ruff check .
mypy src tests scripts
pytest -q
python scripts/check_chat.py --out /tmp/chat-regression.json
```

Use a new report path on every run. CI runs these checks and uploads the HTTP
regression report. Database integration tests require an isolated test database;
tests that cannot run must be reported as skipped, never counted as verified.
CI supplies `BRAIN_GATEWAY_TEST_DATABASE_URL` using its new disposable PostgreSQL
service. Locally, that optional integration test requires a fresh isolated cluster
and an empty localhost database named `brain_gateway_test*`; it applies migrations
and creates cluster roles, so it must never target a shared database or cluster.

The regression runner sends full requests to `/v1/chat` through FastAPI TestClient.
Each follow-up includes the actual service answer from the preceding turn. It uses
the production gateway with a scripted transport and memory ledger, plus synthetic
canonical office facts. It loads no credentials, creates no database connection,
and makes no paid calls. It is an integration check of code behavior; the scripted
model already knows which tool to select and therefore does not test interpretation.
Its elapsed times measure local fixture execution, not provider or production latency.

Reports record the Git commit and dirty state, runtime source fingerprint (including
the provider release), public fixture configuration, prompt, scorer and adapter,
case set, and source-data fingerprint. Source fingerprints include uncommitted
runtime files, so the commit alone is never presented as sufficient provenance.

Each result keeps the request, full response, expected assertions, and failures
separately for contract, support checks, completion, refusal, and error handling.
The summary reports how many turns actually asserted each dimension. Here,
"support" means expected source membership and known-value checks, not a semantic
entailment score. A citation present in the answer does not prove arbitrary prose.

## October 1, 2026 saved results

Final local checks: **282 tests passed, one skipped**; Ruff, strict mypy across 33
files, and `git diff --check` passed. The skipped test is the opt-in real PostgreSQL
ledger integration test. It ran separately against a disposable cluster and passed
at 1:24:57 PM Eastern after credential hardening; the spending implementation and
migrations remained unchanged afterward. The built wheel matched current runtime
source, included `provider-release.json`, and excluded retired modules.

The [offline HTTP report](../evals/chat/office-slice-20261001-results.json) passes
16/16 regression turns with no paid calls. All turns assert the wire/error
contract; 12 assert support checks, 12 completion, and three refusal behavior.
The cases are authored and inspected, not blind. Their scripted interpretation
cannot establish live-model reliability.

Three development smoke runs used the real provider, published campus data, and
durable accounting through the HTTP route. Earlier reports remain unchanged:

| Saved run | Observed outcome | Settled ledger cost | Uncertain reservation |
| --- | --- | --- | --- |
| [Initial](../evals/chat/office-slice-20261001-live-smoke.json) | An office lookup missed the canonical match; the follow-up returned 503 `provider_unavailable`. | $0.000428625 | $0.001645875 |
| [Second](../evals/chat/office-slice-20261001-live-smoke-2.json) | The lookup resolved with stale-data limitations; the follow-up returned 502 because a finish result reference passed the tool schema but failed runtime validation. | $0.000617250 | $0 |
| [Final](../evals/chat/office-slice-20261001-live-smoke-3.json) | All three requests returned HTTP 200: the office lookup, its email follow-up, and a mixed account/public-information request. All had `status: unavailable` because the published contact capture was stale. | $0.000901500 | $0 |

The final run resolved the real canonical Registrar record from
`dev-profiles-headings3-20260929`. Its contact evidence was captured September 23,
so the response displayed dated observations with explicit current-value
limitations. The mixed request retained the dated phone observation and the
private-account limitation. No current-contact verification or data refresh was
claimed. Fresh publication evidence is still needed for current factual answers.

Final live latencies were 5.976, 5.783, and 5.846 seconds (median 5.846 seconds).
Each turn made two provider calls; all six settled. The second run recorded 7.595
and 6.291 seconds; the initial report did not record per-turn latency. These are
sequential smoke timings, not load-test percentiles or representative averages.
Across the three reports, settled ledger cost is $0.001947375, with the initial
$0.001645875 reservation still unresolved in the saved evidence. A reservation is
not proof of a provider charge; it remains charged against the allowance pending
reconciliation.

The fixes addressed shared contracts: office discovery accepts name-word ordering
variations; every retrieved office result is rendered by code; and `finish` now
adds only nonfactual parts instead of supplying fact references. SDK response
serialization also drops null optional fields. The latter corrects an integration
risk, but the initial provider failure's cause was not proven. These changes do not
establish success on unseen student conversations, and the earlier failures have
not been relabeled as passes.

The three-request final run is a small integration smoke test, not an independent
quality evaluation, safety audit, broad campus-coverage check, or production release
approval. It supplies no measured overall accuracy or calibrated confidence score.

## Before calling a release ready for students

1. Verify the deployed identity publication and linked contact evidence through the
   read-only adapter, and verify ledger roles and durable allowance enforcement.
2. Freeze unseen student conversations and expected supported outcomes. Include
   long and omitted histories, ambiguity, mixed requests, absent and conflicting
   records, safety, failures, and adversarial source text. Run through the actual
   HTTP path using the same provider gateway and a bounded development allowance.
3. Review factual support and useful completion independently of topic labels.
   Record unnecessary refusals, missed limitations, latency, and total charged cost
   including failed calls. Preserve original failures and report partial coverage.

No live-model quality claim follows from the offline regression report. No benchmark
in `plan/` or `understanding/` can substitute for this HTTP-path evaluation. Once a
case has been inspected, later runs are regressions even if its filename says blind.

Provider/configuration unit tests verify fail-closed handling of malformed or expired
pricing, not that deployed prices remain current. Provider timeouts can leave an
uncertain charge; operators must reconcile those rows against provider usage before
releasing them. Do not retry them by resetting counters or switching environments.
