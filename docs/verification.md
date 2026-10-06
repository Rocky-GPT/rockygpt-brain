# Current verification

The subsequent single-request `graph_lookup` implementation, graph-node inspector
and clickable Dev UI traversal trace have not been tested or built, at the user's request. The results below predate
that change and do not verify it. Existing scripted office-tool fixtures have not
been migrated to the new traversal contract.

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

Initial rebuild checks: **282 tests passed, one skipped**; Ruff, strict mypy across 33
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
claimed in those smoke runs. Fresh publication evidence was still needed then.

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

## Field observations and prospective run, October 1

Local checks on October 1: **339 tests passed, one optional PostgreSQL ledger test
skipped**; Ruff and strict mypy across 36 files pass. Spending code and migrations
have not changed since the real ledger integration check above. The
[new offline HTTP report](../evals/chat/office-field-observations-20261001-results.json)
passes the same 16 regression turns. It remains scripted integration evidence.

The isolated publication `dev-offices-20261001-v2` preserves original record
capture dates and adds only source-supported email, phone and location observations.
The [frozen oracle](../evals/chat/office-holdout-20261001-oracle.json) was saved
before any paid calls through the real fact endpoint and PostgreSQL shared reader.
All 34 offices resolved: 28 published emails, 33 phones and 25 locations were fresh;
missing properties remained unknown. Original labels, preferences, notes and other
data were not newly verified. The oracle embeds the Data staging report and exact
release/identity pins.

The [frozen cases](../evals/chat/office-holdout-20261001-cases.json) contain 12
conversations and 20 student turns. A separate author prepared them without reading
runtime prompts, implementation, previous tests or results. These are prospective
synthetic cases, not real student traffic or a blind human study. The runner replays
actual assistant answers, preserves omitted-history counts, validates publication
pins on each evidence read and records requests, responses, source fingerprints,
latency and durable ledger operations. It does not grade its own answers.

The [first attempt](../evals/chat/office-holdout-20261001-capture.json) stopped at
HTTP 503 readiness with zero attempted turns and zero model operations; its cause
remains unproven. It was preserved, and no timeout, prompt or runtime change was made
before the second attempt, which passed its own readiness check (HTTP 200) at its
start. A [read-only readiness diagnostic](../evals/chat/office-holdout-20261001-readiness.json)
was recorded at 18:28:01Z, inside the second attempt's window (18:27:15Z to 18:29:01Z),
so it did not gate that attempt. It found the ledger and the fact reader ready.

The [second attempt](../evals/chat/office-holdout-20261001-capture-2.json) captured
20/20 HTTP responses. Its 35 provider operations settled at **$0.00530925**, with
no unsettled reservation from this run, under a $0.50 maximum admission allowance.
Costs use configured conservative prices and are not a provider invoice or hosting
cost estimate. Observed latency was 1.208–8,655.383 ms, median 5,474.831 ms; this tiny
sequential sample includes immediate safety responses and is not a load test.
Runtime, prompt, cases, oracle and public configuration fingerprints stayed unchanged.
These capture counts describe delivery, not semantic success; see the separate
[answer review](office-holdout-20261001-review.md).

That AI-authored review judged 11 turns complete, six adequate and three material
misses. All three misses gave emergency guidance while omitting requested Public
Safety contacts. No unsupported returned campus contact value was observed. The
missing-history explanation has an explicitly documented scoring ambiguity; a
stricter reading changes adequate-or-better from 17/20 to 16/20. Neither count is a
population accuracy estimate or approval for a student pilot. Runtime and prompts
were not changed to improve these exposed cases.

To capture a future run, supply credentials through the normal environment launcher,
review a fresh oracle, then explicitly select a new output and spending ceiling:

```sh
PYTHONPATH=src python scripts/capture_chat_live.py --live \
  --cases <frozen-cases.json> --oracle <fresh-oracle.json> \
  --out <new-capture.json> --max-total-usd 0.50
```

The command refuses production, existing output files and a planned worst-case cost
above the ceiling. A frozen publication pin does not itself establish freshness;
review source capture times before running. The oracle's embedded staging report
predates activation (it records `activated: false`). On October 5 a read-only query of
the local database `rockygpt_profiles_dev_offices_20261001_v2` showed
`dev-offices-20261001-v2` active, and the earlier development database
(`rockygpt_profiles_dev_headings3_20260929`) still had its own earlier publication
active. Nothing recorded here establishes what other consumers or credentials
were or were not changed. Later runs of these now-inspected cases are regressions,
not fresh holdouts.

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

## October 5, 2026: live browser session and repairs

A 50-question session in the student app against the real model and the fresh publication
found three faults and three smaller ones. Those questions were authored by the same
reviewer who then fixed the faults; they are regression material, not a blind evaluation.

- Greetings and thanks ("hey", "thx", "who r u") returned HTTP 502
  `provider_invalid_response`: the model had no valid way to finish a message that needs no
  lookup, and an empty answer was an error. The server now writes greeting, thanks and
  about replies, and a finish with nothing in it asks what the student needs.
- Nicknames and services ("new student id", "my transcript", "advising") found no office:
  office search compares published names only, and the model was never shown them. The model
  now receives the published office names and aliases each turn and queries by exact name.
- After a danger message, every later turn repeated the 911/988 text and never gave the
  requested Public Safety number. Safety now follows the latest message, and the server
  shows the safety text first and keeps everything else the turn produced.
- Smaller: lookups for shuttles and policies, an unrequested clock, a duplicated
  "which office?" line and a fresh record being demoted by an older field
  observation.

An independent offline review of those repairs (no live calls) then found, and a second
pass fixed: a `safety` finish discarded "which office?" questions, "no matching office"
notes and outages, and skipped the validation and length cap of every other finish; an
empty finish right after a safety reply, and a provider failure after a lookup, ended with
no 911/988 text where the old error path had carried it; and a failed office listing was
hidden behind the model's "unsupported". Safety is now an ordinary part of the single
answer path, the two degraded endings carry the emergency text, and an unreadable
directory fails the turn as `data_unavailable`. Tests were added for each, for the
publication pin between the listing and a lookup, and for `list_offices` ordering,
de-duplication and truncation; ten removals of those behaviors on a scratch copy are
each caught. Known and left as is: the phrase floor is deliberately a short list, so some
danger phrasings reach the model; a greeting, thanks or about part beside a failed lookup
shows the outage text in a 200 (as recall and clock already did); and the directory is
passed to the model whole, up to 200 offices.

After the repairs the same questions were asked again in the browser and behaved as intended,
including the account-limit, instruction-override and fake-"system"-message checks. Local
checks after the repairs and the review fixes: 363 tests passed, one skipped (368 after the
developer trace below); Ruff and strict
mypy pass. This is a small, one-reviewer session on synthetic questions and is not a population accuracy estimate. Model behavior still varies
between runs.

## October 6, 2026: emergency texts, hours, and a 72-question live session

What changed: the danger phrase list and the model now name the kind of emergency, and each kind gets
its own text (self harm leads with 988; medical and danger lead with 911; fire says to get out and
call 911 from a safe place, or right away for someone who cannot get out). The code adds the
published Public Safety (and, for self harm, Counseling Center) numbers after it, read through the
shared reader with a three second limit and an exact-name rule. An office's linked schedule records
are now read as its `hours` (see `entity-facts.md`). Refusals point at the office shown above them,
and a recalled reply is quoted as plain words.

Live session, in the developer UI against the real model and a local release copy with the 17
nicknames, 66 service labels and the hours reader: 72 messages as one conversation, 0 errors, 118
model calls, median 5.3 s (phrase-list replies about 0.1 s). 57 answers carried a published contact
(47 in the previous session), 5 gave hours with their source and 2 said hours are not published (the
Health Services page states none). The 72 messages are AI-written and partly written by the same
author as the fixes: they are regression material, not a measure of what students ask. There is no
real student data yet.

Independent review: five read-only reviewers (safety, hours reader, collector and registry, prompt
and decision layer, data honesty) reported 29 findings; two skeptics tried to refute each. 26 held
up, 3 were refuted. All 26 were fixed and each fix has a regression test; 15 of the new tests fail
on the pre-fix source. The main ones: the emergency numbers could be cut off by the turn deadline
and turn an emergency reply into a timeout (they now run after the model's time); a near office
name could stand in for a missing Public Safety (only the exact name counts); two readings of one
weekday shared a source id; weekday spans could cover days with no record; and 66 aliases were
labelled reviewed while their notes said nobody had reviewed them (the notes now say Dan approved
them as a batch). Known and left: a withheld schedule is shown as a known value with its note; the
phrase list sends a completed assault to the general text until a survivor text is written and
reviewed (a copy decision for Dan or a counselor).

Local checks after the fixes: 468 Brain tests passed, one skipped; Ruff and strict mypy pass; the
data repo has 382 tests passing and 5 skipped; the developer UI checks pass.
