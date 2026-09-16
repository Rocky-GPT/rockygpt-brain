# Phase 2 — implementation and verification

Implemented locally on September 16, 2026. The real-provider milestone is still
blocked by missing dedicated development configuration. No paid model calls or
remote database writes were made during this work. No deployment was performed.

## Behavior

- The model can request `lookup_contact` with a published name/alias and all
  requested fields. PostgreSQL performs a bounded, parameterized, release-pinned
  equality lookup. Evidence carries stable source/entity identity and field
  coverage. Old published releases remain readable before the alias migration.
- Complete single-contact question shapes can return server-rendered fields and
  citations after that one model call, with zero synthesis/review calls.
  Missing fields produce an explicit limitation; ambiguous identities require
  clarification. Conflicts, incomplete coverage, stale evidence, invalid source
  identity, truncation and inapplicable dates prevent exact factual output.
  Follow-ups, mixed requests and other wording keep the existing reviewed path.
- Typed filters intersect name, meal, dietary flags, term/session, route and
  campus-local dates. SQL reads cap structured rows at 5,000; document detail
  reads cap neighboring chunks at five. Search absence does not prove nonexistence.
- `calculate` provides decimal sum, difference, mean, minimum and maximum with
  explicit operand provenance. Evidence operands must match current scalar
  calorie/credit fields. Mixed units, invented values and ranges are rejected.
  Arithmetic does not establish a complete set or policy eligibility.
- Request summaries connect tool outcomes, evidence IDs, latency and spending
  through `requestId`, without storing student text or tool arguments.
- Data's additive migration `013_contact_aliases_and_label_coverage.sql` stores
  published office aliases and dietary-label coverage. New publication preserves
  unknown flags as null; Brain treats legacy negative flags as unknown without
  coverage metadata. The migration has only been applied to disposable local
  PostgreSQL. The deployed Data API's existing boolean projection is unchanged.
- UI email links are protected from subsequent domain auto-linking. Budget
  responses display the reset time in New York and official resource links.

## Verification

- Brain: 207 tests passed against disposable PostgreSQL 16, including actual SQL
  retrieval and HTTP/engine/ledger tests with injected provider responses.
- Read-only verification against the existing campus database returned the
  Registrar by its published department alias with correct stable identity and
  field coverage, before applying any remote Data migration.
- Data: 69 tests passed, including its PostgreSQL tests on the frozen local data;
  TypeScript build, typecheck and lint passed.
- UI: typecheck and changed-file lint passed; browser checks used the existing
  interface and API proxy. Verified contact values/citation, a real mailto link,
  missing fax, timeout support ID, and budget reset/resources without retry.
- The injected provider reported fixed fixture usage. The real ledger settled
  successful fixture calls, kept the timeout reservation uncertain, and made no
  provider call after the budget was exhausted. These are **simulated provider
  costs**, not actual OpenAI usage. See [browser results](phase2/browser-results.json).
- Frozen release: `v2-20260916152516`. Public records and chunks are compressed in
  [the snapshot](phase2/public-snapshot.json.gz). Its fixture schema is copied from
  the current Data workspace so Brain checks need no sibling checkout.
- Independent retrieval evaluation retrieved the labeled evidence in 10/12 cases.
  Raw paraphrases “Who handles my transcript?” and “Can I switch dorms?” missed;
  published-name/policy-keyword cases worked. The room-change source excerpt also
  omits some conditions. These gaps are retained in
  [retrieval results](phase2/retrieval-results.json); no embedding comparison or
  full policy-coverage claim is made.

## Reproduce offline

Use a disposable **localhost** database named `brain_accounting_test`. The loader
replaces only that database's `rockygpt_v2` test schema. Never point it at campus
or production infrastructure.

```sh
export BRAIN_TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55432/brain_accounting_test
python scripts/phase2_snapshot.py
pytest -q
python scripts/evaluate_phase2.py
ruff check .
mypy src tests
```

`scripts/phase2_browser.py` is an explicit local test harness, outside the
installed package. It injects only provider/failure fixtures and uses actual
HTTP, engine, retrieval and ledger code. It is never enabled by a deployment flag.
Stop the regular local Brain before running it on port 8000; restore the regular
service afterwards. Do not interpret its fixture response as a model capability test.

## Live development activation — September 16, 2026

The local `.env` now supplies all four deployment settings. The approved
accounting migration was applied to the configured PostgreSQL database, and a
dedicated `rockygpt_brain_development` login was created with only the
`brain_development` membership. Runtime checks verified that it can see only the
development account and cannot change its $10 cap. The ledger connection uses
verified TLS. Credentials are stored only in the ignored local `.env`.

The existing **RockyGPT Staging** OpenAI project showed $0 current-month spend
before testing. A new, explicitly approved Brain development key has Responses
read/write permission and no expiration. Existing provider credentials were not
revoked. Other services do not receive the new key. External use of other keys
in that project still requires the reconciliation described in the Phase 1 guide.

After restarting the normal Brain service, `/readiness` returned HTTP 200 with
dataset `v2-20260916152516`. Two real browser requests completed successfully:

| Surface | Question | Request ID | Settled cost |
| --- | --- | --- | --- |
| Developer UI, port 3100 | How can I contact the Registrar? | `70940a3a-b41c-419d-9fa4-297f64e2ce94` | $0.0227675 |
| Student UI, port 3000 | What is the Registrar’s phone number? | `308b25c7-d8f3-45fb-aa33-48b4b276d31b` | $0.0125575 |

The first answer included phone, email, office, and the Campus Directory citation.
The student answer rendered the phone as a telephone link and included the same
source. All six real provider operations settled successfully, totaling
**$0.035325**, with no unsettled reservations from these checks. These requests
used the normal search-and-reviewed-prose path; they do not establish live
selection of the optional exact-contact path or full Phase 2 evidence coverage.
Earlier fixture browser results remain explicitly simulated.
