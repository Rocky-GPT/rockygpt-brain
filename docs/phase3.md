# Phase 3 implementation checkpoint — not complete

This is a working checklist against `plan.md:462–471`, not an acceptance report.
The release is labeled `phase3-development-2026-09-16`. Do not treat the Phase 2
retrieval results as proof that Phase 3 answer generation passes.

| Requirement | Current implementation | Required completion evidence |
| --- | --- | --- |
| Same controller answers, retrieves or clarifies; no preliminary classifier | Existing controller; explicit ordinary-general answer exemption | Fresh balanced general, clarification, campus and adversarial runs |
| Preserve every requested part | Existing prose/review prompt; independent exact parts combine only with complete request coverage | Independent review of full development conversations and partial failures |
| Reusable exact formats and calculations | Contact, dated menu/hours, named-route departure formats; code timetable extrema | Fresh exact-path and calculation-tool semantic runs required |
| Focused independent review of generated campus prose | Independent review and safety vetoes retained | Fresh corpus including policy, conflict, scope, allergy and dated plans |
| One write, one check, no repair/recheck | Implemented; invalid/rejected candidates retain independently rendered facts and their caveats, or return unavailable | Component tests pass; fresh real rejected-review cases still required |
| Shared call/time/context/spend ceilings | `budget.py`, used independently by controller and paid gateway | Admission tests, local database integration, paid traces |
| Follow-up, corrections, adversarial and attribution behavior | Existing protections retained; general exemption needs fresh semantic validation | Current development corpus using actual generated conversation history |
| Simpler engine passes development corpus; all AI operations visible | No completion claim | Fresh report with all failures, coverage, accounting and independent semantic review |

## Current ceilings and cost deviation

The development release has four model calls (at most three controller/writer
calls and one review), two retrieval rounds, eight tool operations, a 45-second
execution deadline, and a 47-second HTTP deadline. It retains the existing
128,000-token conservative input bound and zero SDK retries. Retrieval must leave
30 seconds for synthesis/checking; each writer must leave 15 seconds for review.

The plan labels $0.01 an **initial proposed** per-turn ceiling. It is not usable
with the current Phase 2 model and conservative byte-based reservation. The
existing gpt-5.4 price configuration reserves up to $0.356 for a maximum-size draft
and $0.38144 for its maximum-size review. Phase 3 therefore explicitly sets a
$0.80 conservative admission ceiling for this development baseline. This is not
an expected or measured cost per answer, and is not a claim that the plan's
traffic/cost acceptance gate has passed. Phase 4's model/cost comparison must
resolve that gate. No model or provider selection was changed in Phase 3.

Every draft admission retains capacity for a worst-case required review. Settled
usage releases its unused local reservation; uncertain calls keep their full
commitment. The durable ledger enforces the default $10 monthly development
allowance plus any administrator-recorded, dated supplement. The user approved
a $20 September 2026 supplement after the first exact-path test was blocked;
that development allowance is now $30 for September only. No balances, reservations or uncertain calls were reset.
A read-only preflight on 2026-09-16 found $2.951065 committed and $7.048935 remaining.

## Implementation notes

- Rejected or malformed generated prose is never repaired, rechecked, or spliced
  into an answer. Independently rendered exact parts retain their source, date,
  allergy and incomplete-list caveats when the remainder fails. Unsupported prose
  is never reused. Answerable incomplete fallbacks must count as failures.
- A non-null `general_scope` is eligible only without campus retrieval/evidence,
  citations or campus-fact paragraphs. Paragraph labels alone are not an
  exemption. This structural gate still depends on correct model scope selection;
  fresh adversarial tests are required before declaring it safe.
- Complete unranked shuttle searches include code-computed next/last departures
  per route and origin, with evidence IDs and dated downstream stops. Ranked or
  truncated sets cannot establish extrema. Computations preserve return endpoint
  semantics and overnight service order, reject conflicting/ambiguous times,
  reject DST gaps/folds, and never assume walking/eating times or live service.
  The original timetable restrictions remain in the evidence.

## Validation checkpoint (not full Phase 3 acceptance)

- Full Brain suite with the existing disposable localhost database: **334 passed**,
  no skips. Ruff and mypy pass for modified runtime and runner code.
- UI typecheck and lint pass. Twelve Node tests pass, covering stream fragmentation,
  live progress before completion, interrupted streams, error preservation and
  failed-turn conversation boundaries.
- `phase3/general-run-01.json`: six fresh complete engine runs, all six admitted
  and completed; nine paid operations, $0.0554215 actual cost, zero unsettled
  reservations. Beginning/ending configuration hashes match. Agent semantic
  review passed these six narrow expectations; human review remains pending.
  This is not the full development corpus or a held-out release certification.
- Greeting, stable explanation and missing-referent clarification each used one
  model call. Engine elapsed times were 5.175s, 4.523s and 3.273s respectively;
  these exclude HTTP and later telemetry cleanup. Fabricated campus assertions,
  an unrelated citation request, and an unverifiable current sports score were
  declined without publishing the requested false/unverified claim. Those three
  responses used one draft plus one independent review each.
- Subsequent student-browser checks completed a general explanation, exact
  Registrar contact lookup, and a transcript-policy follow-up. The progress
  display was visibly verified on the actual streaming route.

## User-requested one-line live status

The UI now requests optional SSE on the existing `/v1/chat` endpoint. Ordinary
JSON clients, including Dev, keep their existing response contract. SSE carries
operation codes, bounded structured lookup context, and one final validated
answer/error. At the user's explicit follow-up request, it also sends the
schema-checked candidate answer text during review as an unverified preview.
It never sends private model reasoning or raw tool/source payloads as progress.

One accessible, non-wrapping line replaces “Thinking…”. Actual events map to
connecting, understanding, fetching campus data, calculating, preparing the
answer and checking the answer. No timer rotation or invented rewriting stage
is used. A smaller gray line describes the actual collection, meal/date filters,
or calculation operation. Only known metadata values are rendered as context.
During review, a separate gray, scrollable “Draft · not verified” preview shows
the candidate text as plain text. It has no final-answer actions, never enters
conversation history, and disappears when the request finishes, fails, or is
cancelled. The next request resets all context and preview state.
The Next route forwards the stream without buffering. Final errors
preserve HTTP-equivalent status, support IDs and retryability in the terminal
frame. Interrupted streams show a clear connection error.

Browser verification observed Connecting → Fetching → Preparing for the contact
lookup, and Checking for the transcript answer. The status text measured about
20px high with `white-space: nowrap`; it disappeared after completion. Cancellation
closes the stream and prevents the next backend operation while in-flight paid
work retains its accounting and concurrency slot until cleanup.

Remaining Phase 3 work: live semantic validation of menu/hours/multipart exact formats
and supported-fact fallbacks, full request coverage, live calculation-tool selection,
fresh full development conversations and evidence review, and final browser
regression of the dinner/shuttle failure cases. **Phase 3 is still active.**

## Exact-format checkpoint and live testing blocker

The new exact format checks consume published entities, dates and requested
attributes independently of the model's proposed request quote. Unsupported
qualifiers, incomplete extrema, conflicts and stale/unknown fields keep the
ordinary reviewed path. Multipart requests only finish early if all requested
content is covered. A rejected prose answer can retain separately verified facts
and all code-owned qualifications, without splicing rejected model text.
Thirty-three focused tests cover these boundaries, including dietary qualifiers,
allergen caveats, wrong venues/dates, partial lists, overnight closing times,
opening-plus-closing requests, quote truncation, and partial-failure preservation.
Open-now conclusions remain in the reviewed path because a preceding overnight
service window can matter.

Today's real dinner list had 51 records, exceeding the prior 50-record search
limit. The bounded search maximum is now 100; exact formats split citation groups
and retain the existing answer/context limits. A read-only real-data check retrieved
and rendered all 51 current dinner records with 51 evidence references in a
1,376-character answer. This verifies data and rendering, not model selection or
end-to-end latency. Oversized exact answers continue to the bounded reviewed path.

`phase3/exact-run-01.json` retains the failed fresh six-case attempt. No answer
completed: the provider returned `project_spend_limit_exceeded` / insufficient
quota for the development project. The runner was interrupted while recording
the last provider failure; its last row lacks a terminal error and there is no
ending configuration hash. It is explicitly not a completed evaluation or pass.
The report retains $1.01515 in unsettled conservative reservations, not measured
spend. No holds or spending limits were reset. The runner now stops on a quota or
rate-limit error and records interruption instead of masking it with a missing
result exception. At that checkpoint, paid evaluation and browser answer verification were paused
until the provider project could admit requests again (subsequently resolved below). Local tests, lint and type
checks pass; the full conversation acceptance suite remains outstanding.

## Calculation and temporary-budget update

The calculation tool now supports ascending numeric sorting with operand indices,
scoped counts of distinct retrieved record IDs, signed elapsed minutes, and time
ordering. Inputs require source field references or explicit user values and units.
Published opening intervals preserve overnight closing dates; shuttle times retain
arrival/departure roles and the published stop label. Only independently validated
complete timetable outputs populate calculated schedule references. Duration uses
absolute timestamps across daylight-saving transitions. Counts never assert full
campus coverage, and time comparisons never invent walking/eating time or operating
exceptions. Seventeen focused checks cover these boundaries, including an engine
turn that retrieves hours and a shuttle schedule, computes their gap, writes, and
reviews within four calls. All 321 tests then passed against the disposable database.

The user approved **Add $20** for development API testing this month on September
16. Migration 002 and a dated administrator-owned allowance were applied to the
configured operational ledger and verified through the actual restricted runtime
role. September's development cap is $30; October automatically returns to the
$10 base cap. Production remains $10. Application roles have SELECT-only access
to supplements; database checks prohibit production supplements, and tests verify
expiration and prevention of self-granted increases. Existing reservations and
uncertain holds were retained unchanged.

## Resumed validation and budget decision

OpenAI authentication completed. The user explicitly chose **Keep OpenAI cap at
$20**; that existing provider cap was left unchanged. The separate September
Brain allowance remains $30 and expires automatically to $10 in October.
Production's allowance is unchanged. The earlier authentication and quota failure
notes describe previous attempts, not the current state.

`exact-run-02.json` completed all six attempts with four usable answers and two
review timeouts. Dinner used one call and rendered all 51 dated records; named
closing hours and the ambiguous shuttle clarification also succeeded. Confirmed
cost was $0.1174535, with $0.59128 retained for uncertain calls. This was a failed
acceptance screen, not six passes.

A controlled low-reasoning review experiment (`exact-run-03.json`) completed five
of six requests but still timed out on dinner plus a private GPA question.
`evidence-gate-low-01.json` passed 26/27 fixed safety cases: it incorrectly approved
a claim that a published tuition price disproves waiver coverage. The experiment
was rejected and the original medium review setting restored. The diagnostic
full conversation attempt `development-run-01.json` stopped on that configuration
change after six attempts; it is incomplete and cannot be an acceptance pass.

The current mixed-answer path composes independently verified exact request parts
with separately generated and reviewed remaining prose. The writer sees which
parts the server will prepend; it does not need to regenerate their item lists or
citations. The independent reviewer checks every generated paragraph and receives
the verified prefix as context. New interpretations of exact facts still require
review. Rejected generated text is never reused. Tests cover supported and rejected
mixed answers, preservation of allergen caveats, and full review coverage.

The evaluation runner now records the synthetic structured draft/verdict outputs
(not private model reasoning), actual retrieval evidence and corpus hashes. An
explicit run allowance admits each next turn only when its worst-case reservation
plus prior settled and uncertain calls fits. Unrun turns and failed attempts are
not passes. `exact-run-04.json` and `development-run-02.json` are fresh live checks
of this implementation; their semantic reviews remain pending until completed.

Phase 3 remains active: complete the fresh corpus, independently review every
result against evidence, fix observed failures, and finish browser/HTTP checks.

## Deadline correction after real failures

The original medium review repeatedly exceeded the proposed 30-second turn ceiling
in the retained live reports, including otherwise supported mixed and catalog
answers. The lower-reasoning experiment failed a critical safety case and was not
adopted. The development baseline now explicitly sets a 45-second execution / 47-second
HTTP ceiling, leaving 30 seconds after retrieval and 15 seconds for review. Every provider call is limited
by the same remaining turn budget; draft calls also reserve review time. Model/tool counts, context, monetary
ceilings and zero retries are unchanged. The UI proxy's existing 60-second deadline
still exceeds the server deadline. This is a tested configuration deviation from
the plan's initial proposal, not a claim that latency targets pass; Phase 4 must
compare configurations against the complex-answer p95 target of 25 seconds.

Recursive evidence defaults now preserve identical nested fields and coverage
without repeating them on every menu row. Lossless reconstruction tests retain
missing/null/false distinctions. Contact discovery uses the same stemming for
query and title, so a direct office match ranks above incidental staff mentions.

The writer now uses a low reasoning allowance while independent review remains
medium. This addresses observed missing qualifiers and incorrectly scoped citations,
not an exemption from review. Long evidence IDs use reversible turn-local references
inside model payloads; returned citations and traces retain original record IDs.
Only declared tool reference fields are expanded, never user text or search keywords.
The lossless encoding preserves every source field. The full local suite passes
332 checks before the shared timeout-bound alignment; new live verification is required.


## Context and draft-preview browser check (September 16)

The live student UI displayed the late-night menu/date context, then the actual
candidate under “Draft · not verified” while independent review ran. The final
verification declined that summary, and the preview disappeared in favor of the
unavailable answer. This verifies preview lifecycle, not answer quality. Local
validation: 334 Brain tests and 12 UI Node tests pass, plus Ruff, mypy, UI typecheck
and lint. The earlier evidence-gate-medium-03 report finished with four failing
cases; those failures and the incomplete mixed answers remain Phase 3 acceptance
work. The preview change does not resolve or waive those failures.

## Coverage and protocol corrections (September 16, evening)

The full HTTP diagnostic `phase3/development-http-03.json` remains a **failed**
run: 23 final responses from 25 attempted turns, two runtime errors, and two
follow-ups not run after an earlier failure. Supported-looking responses do not
establish acceptance. Its separate agent audit records missed calendar dates,
incomplete mixed answers, and delayed urgent guidance. Human review is pending.

Subsequent focused diagnostics identified and corrected concrete causes:

- Term-based calendar searches now explicitly leave date bounds unset unless the
  student supplies a narrower range. Shared-session dates require a term search
  without a singular session filter. The focused calendar answer includes the
  August 26 class start and September 1 full-semester add/drop refund deadline.
- The shared name filter now supports clubs and programs. Previously Brain could
  discover organization names but reject the follow-up exact-name lookups.
- Repeated tool results reference previously delivered, identical records in the
  same turn instead of sending their full fields again. Changed records are sent
  again. Shared evidence defaults can span different source URLs while preserving
  each record's distinct URL and qualifications. Replaying the captured dinner
  searches without any provider calls reduced the final conservative input bound
  from 147,416 to 114,054, below the unchanged 128,000 limit.
- A proposed extra compression of serialized structured content was removed:
  the public retrieval layer already removes that redundant content, so it did
  not address the observed overflow.
- GPT-5.4 can return multiple assistant messages in one completed response.
  Structured output now selects final-answer messages rather than concatenating
  commentary with the answer. A captured failure contained two identical final
  messages; identical completed copies are accepted once, while conflicting or
  incomplete copies remain invalid. Original provider items and usage are kept.
  This does not bypass schema validation or the required evidence review.
- Immediate-danger requests now ask for immediate general safety guidance on
  the first call. The focused safety response completed in one call (6.054s
  model time), without delaying emergency guidance for a campus lookup.
- Planning instructions explicitly distinguish already-ended services/events
  from options the student can still attend at the supplied campus time.

`coverage-diagnostic-02` and `-03` and their separate agent audits retain all
failures. `coverage-diagnostic-04` still failed: a duplicated final message caused
an unavailable program answer, and the dinner/event review timed out. Its
uncertain reservation was retained. Narrow successful calendar, office and safety
answers are not combined into a whole-corpus pass.

Local validation at this checkpoint: the full disposable-database suite passed
346 tests, followed by 38 provider tests including the new duplicate-final
regression. Ruff and mypy pass. The current full conversation diagnostic and
27-case review regression must finish and be independently assessed before a
final committed-runtime HTTP acceptance run. Phase 3 remains active.


## Coverage and latency checkpoint — September 16, continued

The full 27-turn `development-run-04` attempted every turn and returned 25 final
responses plus two errors. Its independent agent review is **fail**. The
subsequent eight-turn `writer-staged-01` improved password reset, program and
organization distinctions, and office locations, but still failed four turns.
Their separate review files retain the failures; human review remains pending.

The 33-case `evidence-gate-medium-05` passed 31 cases. One failure was unsafe:
a Department was accepted as a student organization. The other rejected valid
parts of an evening summary. This configuration was not accepted as complete.

Corrections under evaluation now expose each record's published category and
actual retrieval query, filters, result counts, truncation, and evidence IDs to
the independent reviewer. Query coverage never establishes coverage outside its
own dates/filters. The review schema distinguishes actual category implications
from invented guarantees or an unsupported assumption that every list is exhaustive.
The new eight-case `review-coverage-cases` preserves valid captured menu answers,
paired deliberately overbroad coverage claims, and the original category/evening
pairs. Original regression cases and expectations are unchanged.

An initial check (`review-coverage-01`) passed five of eight: both category cases,
the first complete menu, and both overbroad-menu rejections. It still falsely
rejected the ordinary earlier-event list and the second complete menu. The
follow-up schema change is being checked in `review-coverage-02`; this is a
component diagnostic, not full acceptance.

Read-only measurement on the configured development ledger found transaction
setup medians of 358.7 ms sequentially and 179.6 ms with its three setup statements
pipelined. The pipeline synchronizes before account access; account locks, cap
reads, reservation/settlement ordering, and transaction boundaries are unchanged.
Local real-database tests verify the selected role/timeouts and that a failed
role change cannot expose a connection. Full local validation passed 354 tests;
85 focused tests also passed after the final review-schema wording change.
All deadlines, account limits, uncertain reservations, and the $20 OpenAI cap
remain unchanged. Fresh live conversation and full committed-runtime HTTP
acceptance are still required.


Latest live checkpoint: `review-coverage-02` completed all eight cases, passed
seven, and still falsely rejected the valid evening summary. Its configuration
hash remained unchanged. `remaining-path-01` attempted all seven turns under
the same unchanged configuration, returning five final answers and two timeouts
(menu/allergy and dinner/events). The GPA path rejected invented intermediate
operands, then correctly calculated mean(4.0, 3.0) = 3.5 from supplied values.
The conversation run cost $0.3668135 settled with $0.527385 uncertain still held.
The separate checkpoint explicitly leaves full semantic acceptance outstanding.
No new test run was launched after the user's concern about the elapsed time;
both live processes finished and their reports are retained. Phase 3 is incomplete.
A separate read-only measurement found a 574.3 ms median ledger connection setup;
connection reuse has not been implemented or represented as a completed speedup.


## Per-request ledger connection reuse

The paid gateway now owns one connection for readiness and all reservation /
settlement operations in its synchronous request. Every operation still uses its
own explicit transaction and local role/timeouts. Between operations the
connection is idle: no transaction or account lock spans provider execution.
A failed SQL statement rolls back its transaction; previously committed spending
holds survive. The connection closes on normal exit or failure. Cross-worker
sharing and nested sessions fail closed. Telemetry after gateway exit can open a
fresh connection as before. No account budgets or model settings changed.

`ledger-session-latency-01.json` measures four read-only transactions on the
configured development ledger: 3,788 ms with separate client connections versus
2,052 ms with one session, a 1,736 ms reduction in this small sequential sample.
The database pool reused one server backend in both modes; server backend IDs
must not be mistaken for the number of client connections. This is not proof
that complete model turns now meet their deadline. No paid calls were made for
this measurement. Live answer/review acceptance remains outstanding.
Validation after connection reuse: the full disposable-database suite passed
358 tests, including committed-hold visibility, lock release between operations,
SQL rollback, cancellation cleanup, cross-worker rejection, and one-connection
gateway operation. Ruff and mypy also pass.


## Short venue names in exact formats

The captured `remaining-path-01` first dining turn requested an exact format for
“What vegetarian options are on Birch's dinner menu today?” but used the query
“Birch.” The formatter rejected all nonempty keyword queries before even checking
the request, and the shortened venue name was not a published alias. As a result,
the request used a writer and reviewer despite having all 39 required records.

Retrieval now reports a unique published-name prefix only after checking every
loaded venue name before date/flag filters or ranking can hide another match.
Exact menu/hours formats can consume that code-resolved name, require the returned
entity to match, and still validate every other request qualifier, date, field,
freshness and coverage rule. A prefix is not persisted as a published alias.
Unresolved keywords, suffixes, partial words, mixed-entity results and conflicting
or missing resolution metadata do not gain an exact-format exemption.

A real frozen-database integration test replays that captured tool choice and
returns all 39 menu items/citations after one injected model call, compared with
three calls in the captured live run. This validates the deterministic path, not
new live model-selection latency. Full local validation passed 369 tests; Ruff,
mypy and whitespace checks pass. No model calls were made by these tests.
The remaining evening review false rejection is being checked only against its
original valid/invalid pair after adding general scope examples. All prior
failed reports remain retained; Phase 3 is not yet accepted.

`evening-pair-01` completed both original cases successfully under matching
beginning/ending configuration hashes: the valid summary passed, while the
wrong later closing time failed with the unaffected parts accepted. Model
checks took 36.465 and 22.090 seconds end-to-end respectively. This two-case
component diagnostic is not combined with earlier successes into a full pass.
Its separate agent audit leaves human review pending. Full committed-runtime
conversation and review regression acceptance remain outstanding.


## Full committed-runtime HTTP checkpoint: 015ab1a

`development-http-05` attempted all 27 turns from the unchanged 20-conversation
suite. Brain 015ab1a, UI 217c0d6 and evaluation harness c129080 were recorded before
and after; commits, suite hash and runtime configuration hash remained unchanged.
Every campus result used `phase2-development-20260916`. The separate agent audit
finds 20 passes, two safe but unhelpful responses, and **five failures**. Human
review remains pending; this is not a Phase 3 acceptance pass.

- Library hours/contact, password reset, and catalog/live-seat questions retrieved
  useful evidence but returned a generic unavailable fallback after review.
- The shuttle itinerary timed out at the HTTP deadline, retaining its uncertain
  reservation. The program/club question exceeded the conservative context bound.
- The shortened-venue vegetarian menu now used the exact path: one model call,
  all 39 items/citations, and 5,605 ms end-to-end HTTP latency. Its vegan Lunch and
  closing-time follow-ups preserved the correction and service-period meanings.
- The menu/allergen request and combined dinner/event request completed. The
  private GPA example used the calculator, and urgent guidance completed with
  one model call in 7,220 ms without a campus lookup.

Median HTTP latency was 20,118 ms. Settled cost was $1.0448825 with $0.20193 still
reserved as uncertain; no holds were reset. All 27 turns were admitted, 25 returned
final responses, and two returned errors. No skipped turns or individually
successful retries were combined into these results. The full review regression
was not launched because this candidate already failed conversation acceptance.

The next diagnosis needs the actual unchecked draft for the three review
fallbacks, not guesses from the final generic error. The student SSE protocol
already exposes that draft; the HTTP evaluation harness currently requests JSON
and therefore loses it. Capturing existing SSE preview events in synthetic
acceptance reports can address that observability gap without exposing reasoning
or changing the runtime's review policy. Bounded tool-result delivery also needs
to prevent a broad retrieval from consuming the entire remaining model context.
