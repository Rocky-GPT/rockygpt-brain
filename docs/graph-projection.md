# Graph projection v2 (compatibility)

The normal explorer now uses [shared entity facts, schema 3](entity-facts.md).
This document describes the retained raw-assertion projection for diagnostics
and compatibility.

The development explorer shows each campus entity through one projection: its
published properties, its contextual records (menus and hours), its identity
relationships, and the source record behind every value. Version 2 covers every
collection an identity can link, so the explorer no longer falls back to the
retired `/properties` reader. No schema migration, source publication, identity
change or model call is involved.

## Entry points

- Brain: `GET /v1/dev/graph/projection/v2` (development only)
- Dev proxy: `GET /api/brain/graph/projection/v2`
- Response schema: `retrieval/projection_models.py` (also in OpenAPI)
- Collection mappings and builder: `retrieval/projection.py`

Required parameters: `entity_id`, `dataset_version`, `identity_hash`; take both pins
from `/v1/dev/graph/knowledge`. Optional `limit` defaults to 8 (1–100).

Retired: `/v1/dev/graph/projection/v1` and `/v1/dev/graph/properties`, and with them
the explorer's legacy `EntityGraph` view.

## The response

`schema_version: 2`, with an independently versioned mapping `projection_version`.

- **`sources`** lists every original record the response reads, once:
  - its collection and original row ID;
  - its source key, source record key and source URL;
  - for artifact-backed records (faculty, courses, buildings, schools, subjects), the
    artifact key and the exact path of the item;
  - the capture time, validity interval and freshness;
  - the record's limitations: stale data, the menu's allergy caveat, the
    undated faculty course list, the unresolved schedule applicability.

  A null capture time stays unknown. A record whose source cannot be established is
  not shown, and coverage reports `source_unavailable`.
- **`properties`** are the entity's own published fields. Each property holds
  separate assertions, one per source value. An assertion carries:
  - its value;
  - `source_id` and `field_path`, which locate it in `sources`;
  - its publication status (dietary fields);
  - only its field-specific limitations.

  Conflicting values stay side by side; no winner is chosen. Explicit false, zero,
  empty text, empty lists and null remain distinct.
- **`record_groups`** hold repeated records: menu offerings, dining hours and
  operating hours. Each record has a context (dates, meal, weekday), its own
  properties and its `source_id`. Assertions never cross a record boundary.
- **`relationships`** keep the knowledge index's predicate, direction, exact
  evidence references and registry location (identity hash, owner, array index).
  Names inside properties never create relationships.
- **`coverage`** reports anything withheld or incomplete.

The model validates that every assertion and record names a listed source.

## Mappings

Every collection in the identity registry's link vocabulary has an explicit mapping;
a test fails if one is added without one. Keys are snake_case; the source field is
recorded in `field_path`.

| Collection | Becomes | Fields |
| --- | --- | --- |
| contacts | properties | name, type, title, status, department, email, phone, phones, office, offices, preferred contact, prefers email, contact note, contact aliases |
| faculty | properties | name, title, school, email, phone, office, profile URL, image URL, bio, education, profile courses, teaching and research interests, published research |
| programs | properties | name, degree, program kind, school, description, program URL |
| clubs | properties | name, category, website URL |
| events | properties | title, date label, start, start and end time, organizer, description, event URL |
| buildings | properties | name, category, map URL, room prefixes, Concept3D ID, identity basis |
| schools | properties | name, abbreviation, official URL, section, former names |
| courses | properties | code, name, description, credits, attributes |
| subjects | properties | code, catalog name, display name, search terms, course count |
| menu | record group `menu_offerings` | context: validity, meal, station; item name, calories, portion, vegan, vegetarian, allergens, dietary label coverage |
| dining_hours | record group `dining_hours` | context: weekday, validity; schedule |
| campus_hours | record group `operating_hours` | context: weekday, validity; schedule, structured hours |

Properties from several collections merge by key, so a person's email from the
directory and from the faculty profile appear as two assertions with two sources.
A catalog course node has no identity links; its one catalog record supplies its
properties.

Not published as properties:
- contacts' search text, normalization metadata, raw phone and phone
  normalization status (processing fields);
- faculty `imagePath`, this repository's former local copy of the photo;
- the `name` of hours rows, which repeats the entity.

Any other unmapped field is reported as `fields_not_migrated`. Nested values are
checked against an allowlist of keys and types:
- phones: type, number, extension;
- hours: open, close, close-day offset;
- credits: a number, or min, max and operator;
- former names: name and evidence;
- dietary label coverage.

A value of another shape is withheld and reported as `unsupported_field_shape`.

`properties_complete` is false when:
- a property collection reports any coverage issue;
- a linked collection has no mapping;
- more than 100 records of one property collection are linked (`property_limit`);
- the response is a group continuation.

Record-group completeness is separate: read `total`, `returned` and `next_cursor`.

## Pagination and filtering

To continue a group, send `record_group` and its `next_cursor` with the same pins,
filters and limit; the continuation returns only that group and its sources.

Filters are a JSON object of exact string or null values:
- menu: `date`, `meal`, `station`;
- dining hours and operating hours: `day`.

No current-date filter is applied implicitly.

Cursors bind the projection version, release, identity hash, entity, group,
filters and page size. A malformed cursor is 422; a changed scope is 409. Ordering
is the reader's deterministic title and row-ID order within an immutable release.

## Performance

A projection page reads its records in one query, plus a count, instead of one
query per row, and computes each record's freshness once.

The validated registry, identity hash and graph index are cached per release in
`retrieval/release_cache.py`. The cache key is a fingerprint of:
- the database;
- the active release;
- every artifact's content hash.

Freshness depends on the clock and is never cached. The complete export builds its
own graph, because it pins every read to one database snapshot.

Measured on the local release `dev-profiles-alias-sources-20260923`, by
`GET` against a warm Brain:

| Request | v1 | v2 |
| --- | --- | --- |
| Birch Tree Inn, 100 menu offerings | 837 KB, 1.34 s | 399 KB, 0.04 s |
| Registrar | 5 KB, 0.21 s | 6 KB, 0.01 s |

The first request after a release changes builds the cache, about 0.3 s.

## Verification

Unit tests cover:
- conflicting assertions and the source list;
- field-specific versus record-level limitations;
- artifact paths, course nodes and nested shapes;
- unmapped collections, cursors and partial coverage;
- relationship direction and location;
- batched reads, and the cache's reuse, eviction and fingerprint.

The opt-in live test uses only the campus reader role:

```sh
GRAPH_TEST_DATABASE_URL=postgresql://brain_campus_reader@127.0.0.1:55434/rockygpt_profiles_dev_alias_sources_20260923 \
  .venv/bin/pytest tests/test_projection.py -q
```

It pages through all 887 Birch menu offerings with unique IDs. For one entity of
every kind:
- properties must be complete;
- every value must equal its original record, read through `/v1/dev/graph/record`;
- relationships must equal the knowledge index's edges.
