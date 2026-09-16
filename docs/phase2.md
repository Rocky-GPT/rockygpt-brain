# Phase 2 — implementation and acceptance

Status: **Phase 2 development acceptance complete**, September 16, 2026.

The menu-context regression that reopened this phase is fixed and covered by
HTTP + real PostgreSQL + accounting-gateway tests. The exact contact path,
all nine required controlled outcomes, and **17/17** labeled retrieval cases pass.
The normal student browser also completed the original short dinner conversation.
This establishes the Phase 2 milestone, not production release readiness or the
later controller/model latency targets.

## Requirement-by-requirement evidence

| Plan requirement | Implementation and verification |
| --- | --- |
| Existing browser → `POST /v1/chat` | Real Developer UI browser request `eaf389c0-9615-4028-8605-52be16cb1d7f`, HTTP 200, normal Brain and OpenAI provider |
| Trusted environment, request ID, reserve before provider | Existing environment-specific paid gateway and durable ledger; admission, isolation, race, restart and month-boundary tests |
| Baseline model chooses real tool | Real `lookup_contact` request for the Registrar; generic tool and entity/field contracts, no canned office answer |
| Parameterized retrieval and evidence validation | Release-pinned SQL; published aliases, source/entity identity, field coverage, freshness, applicability and conflicts checked |
| Exact eligible answer without synthesis/review | Browser contact: `exact_contact`, one model call, zero review calls; extra fetched fields do not expand the user's request |
| Render fields and source in browser | Verified department, email, phone, office, directory citation and mailto link |
| Actual accounting linked to request and evidence | Browser receipts include settled provider usage, request ID, configuration hash, tool result/evidence IDs and timings in `context-fix-live-results.json`; historical checks remain in `final-live-results.json` |
| Supported/missing/uncovered | Actual SQL + HTTP tests; unsupported names clarify and unpublished fields produce a limitation |
| Ambiguous/conflicting | Actual duplicate SQL rows with different identities or contradictory values; no unsupported exact output |
| Database failure | Injected retrieval failure returns a safe response and request ID; already performed provider work stays settled |
| Provider failure/uncertain timeout | Safe HTTP errors with request IDs; durable reservations remain uncertain, never treated as free calls |
| Exhausted budget | HTTP 429 with reset/resources; injected provider is never called |
| Additive Data improvements | Published aliases, nullable dietary flags/coverage (migration 013), contact discovery text (migration 014), versioned lexical vocabulary |
| Filters, bounded reads, contracts | Dates and typed filters intersect; maximum 5,000 structured rows; at most five same-source neighboring chunks per read; explicit partial source coverage |
| Calculations and exact rendering | Decimal sum/difference/mean/minimum/maximum with verified operands and units; code renders exact contact facts and calculation values |
| Labeled independent retrieval | 17/17 frozen checks, zero provider calls; all required source qualifiers must match, not merely any expected chunk |
| Reproducible public snapshot | `public-snapshot.json.gz`, copied schema + additive migrations, guarded local-only loader, SHA-256 in result reports |

## Menu-context regression closed

The original short dinner conversation, request
`70d4813d-392f-4615-b8fd-ce2c96d5d2aa`, retrieved 50 menu records and four
hours records. Repeated source metadata pushed the next request over the
conservative input bound; the error incorrectly blamed conversation length.

Tool and reviewer payloads now share identical top-level record metadata in
explicit `defaults`/`records` groups and use compact JSON. Reconstruction is
lossless: each row overrides its group's defaults. No records, field values,
coverage, dietary labels, limitations, conflicting evidence, or accepted
conversation messages are discarded. Server validation and citations still use
the original records. The reviewer still receives all retrieved evidence,
including uncited contradictions. Both model instructions document the encoding.

`test_short_dinner_chat_with_fifty_menu_records_and_hours` forces the exact
50-menu + four-hours retrieval through HTTP, real frozen PostgreSQL, and the
normal paid gateway with only the external provider simulated. It proves the
old representation exceeds the unchanged input ceiling and the complete new
representation fits. It checks every reconstructed record in both writing and
review contexts, source counts/truncation, all menu names/citations, and settled
accounting. It also injects genuinely oversized evidence: the next paid call
and reservation are never made, previous usage stays settled, and HTTP 422
returns `retrieval_context_limit` with a narrowing suggestion. Original-history
overflow retains `context_limit`; neither response invites automatic retries.
Separate regression tests cover overflow during review and preserve null versus
absent fields, different sources, stale records, and differing coverage.

Live revalidation used the normal services and provider:

- Student UI: `hey` → `what is for dinner today` succeeded with a non-exhaustive
  dinner list and the Ramapo Dining citation. Request
  `64cfa2fb-0e8f-44df-bc64-280570a2b674`, 49,486 ms server time, five model calls,
  32 ms retrieval, all usage settled. The live model chose 20 of 51 menu records;
  the forced automated regression covers the larger 50 + four case.
- Exact contact: request `eaf389c0-9615-4028-8605-52be16cb1d7f`, 6,383 ms browser
  time, one model call, zero review calls, 62 ms retrieval, correct directory
  fields/citation, all usage settled.
- The earlier Developer UI dinner attempt
  `21f409ae-3dee-4d64-b5fd-2b19e470ef98` passed context admission but timed out in
  the existing review/repair path: HTTP 504 at 46,368 ms browser time. Its last
  uncertain operation still holds 249,175,000 nUSD ($0.249175). No charge was
  cleared, retried invisibly, or treated as free.

There were five live turns in this revalidation: two greetings, two dinner
attempts, and one contact request; four completed and one timed out. Receipts,
configuration identities, and browser observations are retained in
[context-fix-live-results.json](phase2/context-fix-live-results.json).

## What changed in retrieval

The Data scraper no longer clips sections at 2,000 characters or six paragraphs,
and it retains table content and nested text. Markdown generation no longer cuts
sections at 450 characters or silently limits the default page/section selection.
It preserves section headings, page URLs and original page collection times.
The former housing-only merged meal-plan excerpt was removed: its content now
comes from complete source sections with the correct page attribution.

The official housing and Registrar pages were collected again on September 16.
PostgreSQL contact publication now retains existing service-search vocabulary in
`search_text`, separately from factual fields and identity aliases. Brain uses
English lexical stemming for contact discovery. A small versioned Data vocabulary
expands generic room/dorm and change/switch terms for document discovery; it
contains no question templates or answers. Heading-only matches are searchable.
Discovery terms are never presented as proof that an office provides a service;
service questions also retrieve official process documents.

A bounded detail read stays on the same source URL and reports the number of
returned/source chunks and whether the source context is complete. The frozen
checks verify the room-change fee, relocation and cancellation conditions,
availability, open-period dates and hall-office guidance; meal-plan exceptions
and deadlines; and paper-transcript processing qualifications. Date, meal,
dietary, term/session and shuttle service-day filters retain their negative cases.

The original **10/12** result is preserved in
[initial-retrieval-results.json](phase2/initial-retrieval-results.json). Current
labels use stable source URLs and required text conditions instead of relying on
regenerated chunk UUIDs. Every required qualifier must be retrieved through the
normal bounded search/read contract. See
[retrieval-results.json](phase2/retrieval-results.json).

## Verification

- **Brain:** 229 tests passed using real disposable PostgreSQL, including all nine
  controlled HTTP outcomes, forced large-menu context/overflow cases, all five
  arithmetic operations, and invalid measurement provenance/units. Ruff and strict
  mypy passed. Tests used real disposable PostgreSQL with no skips.
- **Data:** 70 tests passed, with no skips, including PostgreSQL and HTTP tests;
  typecheck, lint and build passed. The new ingestion test checks long sections,
  later conditions, tables, headings, source URLs and original timestamps.
- **Current real browser contact:** 6,383 ms browser wall time; one paid model call,
  no review, 62 ms retrieval; correct fields and source.
- **Real browser policy:** request `a5925732-2aa0-4293-a6f6-33f2466fabcd` returned
  the previously truncated approved-room-change conditions and the official
  Residence Life source. It took 44,485 ms in the browser.
- **Real service evidence:** request `6cd4fed3-9b46-4b39-9206-185c12fb6b92` retrieved
  Registrar process pages and supported its official-transcript responsibility.
  Its generated answer remained conservatively partial about broader duties.
- **Real timeout:** request `561c8abb-e833-4247-9ddf-501fc727c143` timed out during
  review. The browser showed a safe error; the raw upstream response included
  its support ID. Known usage settled; the uncertain operation retained its
  reservation pending a provider receipt/reconciliation. It is recorded as a
  failure, not silently omitted or counted as settled usage.

[Final live results](phase2/final-live-results.json) retain successes, diagnostic
failures and per-operation receipts. Earlier
[live contact checks](phase2/live-contact-results.json) and
[simulated browser checks](phase2/browser-results.json) are historical evidence;
the latter use fixture usage and are not real provider cost measurements.

General prose still follows the existing drafting/review path and can be slow or
time out. Phase 3 changes the controller/general answer paths and repair loop;
Phase 4 measures model choices. The context regression is closed and the Phase 2 exit conditions pass. These
checks do not establish all-question answer quality, p95 latency, embedding
superiority or production release readiness. The observed review timeout remains
explicit evidence for the planned Phase 3 controller/repair changes and Phase 4
model comparison; it is not a successful answer or a claim that speed is fixed.

## Development dataset and services

The normal local Brain on port 8000 reads `phase2-development-20260916` from the
persistent local PostgreSQL container `rockygpt-phase2-dev` (loopback port 55432),
using the SELECT-only `brain_campus_reader` role. The existing UI on port 3000
and Developer UI on 3100 use that normal Brain. Provider credentials and the
approved remote development accounting ledger are unchanged.

This development snapshot retains the published release `v2-20260916152516` for
unchanged collections and replaces the housing/directory source documents with
fresh captures processed by the corrected Data chunker. It adds contact discovery
metadata and the versioned vocabulary without fabricating factual values.
Original collection dates are retained. Other public collections, including
events, clubs and programs, are included in the frozen snapshot.

A full Data publication attempt correctly rejected stale unrelated local crawl
artifacts. No freshness gate was disabled and no shared production dataset was
rewritten. Production publication still requires fresh inputs and the existing
quality gates; deployment belongs to later release work. The snapshot is an
explicit development acceptance fixture, not a claim that production was released.

The previous local connection settings are preserved in ignored
`.env.phase2-before-local-data` with mode 0600. No credentials are committed.
Start the existing local database when resuming development:

```sh
docker start rockygpt-phase2-dev
```

## Reproduce without paid calls

Use a disposable **localhost** database named `brain_accounting_test`. The loader
replaces only that database's campus schema. It refuses remote hosts and other
database names. Do not run the destructive test loader against the development
service database or shared infrastructure.

```sh
export BRAIN_TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55432/brain_accounting_test
.venv/bin/python scripts/phase2_snapshot.py
.venv/bin/pytest -q
.venv/bin/python scripts/evaluate_phase2.py
.venv/bin/ruff check .
.venv/bin/mypy src tests
```

In Data, run `DATABASE_URL` pointing to that same loaded local test database with
`npm test`, followed by `npm run typecheck`, `npm run lint`, and `npm run build`.
The standalone `scripts/phase2_browser.py` remains an explicitly injected local
provider harness; it is not a deployment mode and never calls OpenAI. Stop the
normal Brain before using that harness on port 8000 and restore it afterwards.
