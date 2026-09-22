# Graph projection v1 — dining proof

Stage one adds an opt-in projection API. The current explorer still consumes
`/v1/dev/graph/knowledge` and `/properties`; their payloads and rendering do not
change. No schema migration, source publication, identity changes, or model calls
are involved.

## Contract and entry points

- Brain: `GET /v1/dev/graph/projection/v1`
- Dev proxy: `GET /api/brain/graph/projection/v1`
- Response schema: `retrieval/projection_models.py` (also exposed in OpenAPI).
- Collection specifications and builder: `retrieval/projection.py`.

Required parameters: `entity_id`, `dataset_version`, `identity_hash`. Obtain both
pins from the existing knowledge endpoint. Optional `limit` defaults to 8 (1–100).
An initial request includes mapped direct properties, one page per mapped record
group, and incoming/outgoing published relationships.

The response has `schema_version: 1`, an independently versioned mapping
`projection_version`, entity identity, `properties`, `record_groups`,
`relationships`, and `coverage`. New kinds do not require entity-name branches.

Each property contains separate assertions with exact values, limitations, and
source provenance. Provenance includes the source key/record key, collection,
original row ID, field path, source URL, original capture time, validity interval,
and freshness. A null capture time remains unknown. Assertions never select a
winner among conflicting values. Explicit false, zero, empty text, empty lists,
and null remain distinct.

Each contextual record has a release-scoped ID, type, label, context fields,
properties, and space for explicit relationships. IDs use the dataset version and
original collection/row ID, not a dish name or ordinal. Assertions never cross a
record boundary. Their field locators point to original published SQL rows,
including metadata columns such as `valid_from`; they do not claim an underlying
website was independently reverified.

Relationships preserve the existing graph's predicate, source, target and exact
evidence references, including pinned source row IDs. `subject` distinguishes
entity and contextual-record subjects; stage one emits only existing entity
relationships. Incoming traversal changes `direction`, not the actual subject or
predicate. `registry_locator` pins the identity hash, source entity and original
relationship array index. Evidence references remain references: the API does not
invent capture times or SQL field locations for artifact-backed organizer or
convener evidence. Names inside properties never create relationships.

## Stage-one mappings

| Collection | Entity properties | Contextual records |
| --- | --- | --- |
| contacts | name, email, phone, title, office, department, contact note | none |
| menu | none | One offering per original row: validity dates, meal, station, item name, calories, portion, dietary flags, allergens, dietary publication status |
| dining_hours | none | One row per weekday/validity interval, retaining the published schedule text |

A venue's name is not combined with its menu item names. Repeated appearances of
the same dish on different dates/meals remain separate. Regular and seasonal
hours coexist; this endpoint does not select a currently effective schedule or
turn unknown hours into Closed. Dietary publication status is retained, so an
empty stored allergen array without a published source assertion cannot imply
absence. Nested label coverage accepts only declared dietary keys/statuses.

Direct contact properties are a small generic allowlist to prove the property
attachment. Derived contact fields do not become independent corroboration. Other
contact fields produce `fields_not_migrated` issues. Known operational fields
(search text, normalization metadata, raw phone and normalization status) are not
public properties. Unknown fields and unsupported nested shapes are excluded,
with explicit coverage instead of copying arbitrary storage objects.

Other collections (faculty, courses, clubs, events, programs, campus hours) report
`collection_not_migrated`. Their existing properties and all published entity
relationships remain available on the legacy path. Stage one deliberately does
not attach previously omitted artifact fields without approved exact bindings.

`properties_complete` is false when there are unmigrated linked collections,
contact coverage issues, a direct-property source-record limit, or a group-only
request. Direct contact assertions have a 100-record guard; reaching it produces
`property_limit` and points to the legacy reader. It is not silent truncation.
Record-group completeness is separate: inspect `total`, `returned`, `next_cursor`
and coverage. Unavailable records are diagnosed even when a page advances.

## Pagination and filtering

To continue, send `record_group` and that group's `next_cursor`, keeping the same
pins, filters, and limit. A continuation returns only the selected record group;
`selected_record_group` identifies this partial response. Do not replace an
entity's complete attachment state with a continuation response.

Menu filter fields: `date`, `meal`, `station`. Hours filter field: `day`. `filters`
is a JSON object of exact string/null values; no fuzzy matching. Group-scoped
filtered requests start without a cursor. An empty result returns total 0 and no
cursor. No current-date filter is applied implicitly.

Cursors bind projection version, release, identity hash, entity, group, filter
selection and page size. Malformed cursors produce 422; changed scope produces
409. They are opaque continuation state, not authorization tokens. Exact entity
record scoping is enforced again on every request. Ordering follows the existing
reader's deterministic title/row-ID ordering within an immutable release.

## Verification and next stages

Unit tests cover repeated dish names, zero/false/null/empty values, conflicting
contact assertions, interval boundaries, source dates, unknown dietary status,
field allowlists, malformed and mismatched cursors, partial coverage, and exact
incoming/outgoing relationship evidence. All legacy graph tests remain in place.

The opt-in live test uses only the campus reader role:

```sh
GRAPH_TEST_DATABASE_URL=postgresql://brain_campus_reader@127.0.0.1:55434/rockygpt_profiles_dev_organizers_20260922 \
  .venv/bin/pytest tests/test_projection.py -q
```

It traverses all 887 Birch menu offerings with unique IDs and preserved record
boundaries, checks hours and field locators against the original records, and
compares legacy properties before/after for people, courses, clubs, events,
programs, offices and facilities. Relationships are compared with the existing
knowledge index; the index must remain unchanged.

Before moving the renderer, add an opt-in generic attachment consumer. Additional
collection mappings require explicit field semantics and exact artifact bindings,
with artifact-hash/path provenance when those sources are enabled. Bump the
mapping version for changed projection semantics. Avoid replacing the stable old
path until each collection's coverage has been validated.

The new path reuses the current exact readers, including their per-record reads
and catalog projection. It does not introduce a cache or solve the existing
large-catalog query costs in this stage. Source-data and operational SQL migrations
are not required. Rollback is removing the opt-in route/proxy; legacy rendering is
unaffected.

Verification checkpoint: the full Brain suite passes (673 passed, 40 opt-in tests
skipped); the projection suite with the local reader database passes all 17 tests.
Changed Python files pass Ruff and mypy. Repository-wide checks still report 62
Ruff issues and 104 mypy errors in unchanged files; none are in the new projection
modules or tests. Dev typecheck, lint and all 28 existing graph helper tests pass.
