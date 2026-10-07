# Entity facts: the shared read contract

Normal application and Brain fact reads resolve a canonical identity and use
`src/rockygpt_brain/retrieval/entity_facts.py`. Source collections provide discovery
and original evidence; they are not competing attribute authorities. A browser
must not reconcile raw rows or infer its own fact values.

## Current implementation: offices

The current rebuild reads **office contact facts and office schedules**. It does not
yet implement fact projections for people, venues, programs, clubs, events, buildings,
schools, subjects, courses, or menus. The development graph-node inspector exposes
this same office branch. It does not yet implement broad developer graph projections,
profiles, paginated context groups, or versioned cursors.

The active consumers are:

- The chat `graph_lookup` tool (one root/category/office traversal in code), which
  discovers a canonical office and reads only the requested supported fields.
  Code renders every retrieved office result;
  the model does not select which published values or conflicts survive the answer.
- `GET /v1/entities/{entity_id}/facts`, which returns the same office facts for
  applications. It requires `dataset_version` and `identity_hash` query parameters.
- Development-only `GET /v1/dev/graph/node`, which reconstructs the selected node's
  root path and uses the same fact reader for its records.

`list_offices` returns published office names with their aliases, sorted by name, from
the same pinned publication, so a caller can choose a published name. It returns at most 200
offices by default (500 at most) and sets `truncated` when more exist; the chat engine does
not yet handle a truncated listing, so a directory larger than 200 offices needs a design
change first. It adds no synonyms and does not change how `search_offices` matches.

Adapters supply the same snapshot to the one shared reader: Postgres (the source of truth), an
in-memory fixture for tests, and optional graph-file adapters (`retrieval/graph_store.py`). One
follows Postgres and builds its own copy (`ReleaseGraphFacts`, which falls back to Postgres). The
other serves a release a publisher built (`GraphOnlyFacts`, `scripts/build_graph.py`) and never
connects to Postgres. A graph only stores and returns what the source adapter returned; it never
chooses, merges or normalizes a value, and it is never an authority: the self-building copy sends
any doubt to the source, and the graph-only adapter reports it as an error.

The reader exposes schema version `3`, mapping version `entity-facts-3`. Those
identifiers describe the response envelope, not support for every entity type.
The [previous broader contract](historical/entity-facts-pre-office-slice.md) is a
historical record of an earlier implementation and must not be used as a current
capability list.

## Identity, publication, and evidence

`EntityFacts.search_offices` discovers office candidates from the published
canonical names and aliases. Discovery text cannot establish a phone number,
room, or other attribute. An ambiguous search remains ambiguous; no search match
is treated as proof that a campus entity does or does not exist.

`EntityFacts.get_office_facts` selects original contact records through exact
published identity links: source key, source-record key, and optional original-row
ID pins. It never joins records by a convenient matching name or email. Duplicate
evidence IDs, inconsistent ownership, and unsupported publication structures are
rejected. A missing linked record produces an explicit incomplete result.

`PostgresEntityFacts` reads the active dataset's `campus-identities` publication,
its alias provenance when present, and the linked contact records. Reads use a
bounded, read-only repeatable-read transaction. They do not modify the registry,
sources, or publication. No second facts table or identity registry is created.

Readers pin the dataset version and identity hash. A changed publication cannot
silently mix two versions: the HTTP fact endpoint returns 409, and chat abandons
the old turn's fact set. The office read is bounded to 128 original records and
128,000 serialized evidence bytes; an oversized read fails instead of silently
truncating supporting evidence. Search results separately expose truncation.

`MemoryEntityFacts` runs the same resolver over explicit fixtures. It is for
tests and offline regressions, never a fallback when production data is absent.

## Properties and boundaries

Supported fields are `name`, `department`, `email`, `phones`, `offices`,
`prefers_email`, `preferred_contact`, `contact_note`, `website`, and `hours`. The first
nine come from the office's linked contact records. `hours` comes from its linked schedule
records (see "Hours" below). An empty value stays empty, with status `unknown`. A placeholder
such as "N/A" is never a value.

Each property carries a key, label, category, status, canonical `values`, and
original `assertions`. Values retain every supporting assertion ID and source ID.
Sources retain original record identity, source key, capture time, citation URLs,
validity dates, freshness, normalization metadata, and caveats.

Statuses describe observations, not official authority or confidence:

- `known`: one distinct nonempty value, possibly alongside empty observations.
- `unknown`: no nonempty value is published.
- `conflicting`: different values have overlapping or unspecified validity.
- `multiple`: different values have fully specified, disjoint validity intervals.

The resolver chooses no winner. Empty observations remain visible without erasing
a populated value. Primitive types, text case, and array order remain significant;
JSON object key order does not. Source freshness is separate from value agreement.
`evidence_count` counts original records, not independent confirmations or votes.

Date-only validity uses the campus calendar in `America/New_York`. Capture-time
freshness uses elapsed time and the source's published freshness allowance. Chat
passes its single turn clock into the reader. Missing dates, unknown freshness,
expired or future records, and absent citation URLs remain explicit.

The fact API exposes the original observations and these boundaries. The chat
renderer presents current values only with fresh, usable HTTPS evidence. It shows
conflicts and unknowns, and does not promote stale values or prior conversation
claims into current facts. A provenance URL alone does not establish an office's
own website.

## Hours

The graph already links an office to its schedule records: the `campus_hours` collection,
one record per weekday, with a name, the published hours text, the official sentence it was
read from (`notes`), the page it came from (`source_url`), its capture time, and an optional
validity window. The identity registry links them by exact `source_key` and
`source_record_keys`, like contacts. The reader reads them only when `hours` is requested,
bounded to 128 records, and a record that is not linked to the office fails the read.

The `hours` property has category `schedule`. One **value** is one named schedule for one
validity window: `{schedule, days: [{day, hours}], notes: [...]}`. The weekdays are published
text, never parsed, expanded or merged by the reader, and they are not values of their own
(Monday and Tuesday do not disagree). One **source** backs one value: its `record_ids` are the
weekday records, its validity window is theirs, its capture time is the oldest of them, and its
citation is the record's own `source_url`, else the source's page.

Statuses follow the contact rules, but are decided per schedule name. Schedules with different
names (a library's circulation desk and its research desk) are separate answers and never
conflict. The same name with overlapping validity and different content is `conflicting`. The
same name with fully specified, disjoint windows is `multiple`. Two different texts for one
weekday inside one window are a conflict too, and each reading is kept. An office with no
schedule link has `unknown` hours, which is not missing evidence. A linked record that cannot
be found is a caveat, but only on a read that asked for `hours`.

Hours are dated, not current by default. A window that has ended is `expired`, one that has
not begun is `future`, and an old capture is `stale`. The chat renderer shows each of those as
a dated observation, shows runs of weekdays with identical text as one span ("Monday to
Friday"), and shows the official sentence as a published note. The reader never decides that an
office is open now, and it adds no summer, holiday or walk-in rule that the records do not hold.

The collector writes the text "Hours unavailable" for a weekday the page does not list, and a
withheld schedule (one the collector could not verify) is written as seven such days with its
reason in the note. The reader passes both through as published, so a withheld schedule counts as
a known value and is shown with its note. It is a placeholder that should be an empty value with a
reason, and that belongs in the collector, not the reader.

Runs of weekdays are drawn only over days that follow each other in the week and have the same
text. A weekday with no record is never inside a span. A schedule's own name is shown when there
are several schedules or when the name says more than the office's name (a trailing abbreviation
such as "(CSI)" says nothing more).

## Field observations and freshness

A publisher can attach a fresh observation of the complete raw `email`, `phones`,
or `offices` projection without changing the contact row's `collected_at`. It
cannot refresh names, departments, preferences, notes, or other fields this way.
An absent observation leaves the original source and freshness behavior unchanged.

The optional `normalization_metadata.contact_observations` object has exactly:

- `schema_version: 1`, `artifact_key: development-office-contact-evidence`, the
  artifact's SHA256 `artifact_hash`, a nonempty `base_version`, and `fields`.
- `fields` maps only the supported contact field names to `captured_at`,
  `value_sha256`, and a nonempty `pages` list.
- Each page has `url`, `section`, `fetched_at`, and `html_sha256`, with optional
  `near` text. URLs must be safe HTTPS URLs. Capture/fetch timestamps must include
  a timezone, not lie in the future, and the field capture must equal its oldest
  supporting page time. There are at most 16 pages per field; section and nearby
  text are bounded to 1,000 and 2,000 characters respectively.

The raw projection is `row.email`, `{phone: row.phone, phones: row.phones}`, or
`{office: row.office, offices: row.offices}`. Its hash uses compact UTF-8 JSON with
recursively sorted object keys, unescaped Unicode, and preserved array order.
Only strings, nulls, arrays, and objects are accepted in observed projections;
no cleaned or partial value substitutes for the complete raw hash.

The Postgres adapter obtains the observation artifact hash through a hash-only
join to `release_artifacts` in the **same dataset version** as the contact row.
It does not load the artifact's full page payload. The metadata hash must match
that trusted join, and the raw projection hash must match the current row. Invalid,
unsupported, or incomplete supplied observation metadata fails the read, even if
that field was not requested; it never silently falls back to stale evidence.

The reader also requires 64-character lowercase hex hashes, a nonempty `base_version` of
at most 256 characters, a nonempty `fields` map, no control characters in `section`, `near`
or `base_version`, a nonempty raw projection of at most 12 nesting levels, and it fails the
whole read, not just one field, when any observation metadata on a row is invalid. An
observation whose capture is not newer than the record's own `collected_at` is validated
and then ignored for that field.

The publisher is responsible for proving that the cited pages support the complete
projection. The reader checks the publication binding and metadata, without fetching
pages or independently interpreting their text. A hash alone is not semantic proof.

For each valid observation the reader retains the original source and adds
`<original-record-id>:contact_observation:<field>`. Assertions and canonical values
use this derived source only for that field. It carries `original_record_id`,
`observation_field`, the unchanged `original_collected_at`, original freshness and
caveats, plus the field's capture/page provenance. Chat citations expose the
original record ID, field, and original capture too.

The field capture and the source's existing freshness allowance determine that
derived source's freshness. Original validity dates still apply. Reobserving a
phone does not make its office name fresh, resolve a disagreement, extend a dated
record, or refresh the full row. `evidence_count` still counts original contact
rows; derived sources are not additional independent records or votes.

## Implemented representation normalization

The shared backend applies only the following source-format rules:

- HTML-entity decoding and whitespace cleanup preserve text case and original
  assertions. Email text receives the same cleanup; no address is inferred.
- `phone` and `phones` map to `phones`. Unambiguous ten-digit North American
  numbers normalize to `+1...`. Structured extension/type fields remain distinct.
  An explicit extension-only value such as `ext. 1234` becomes an extension field;
  other unparsed text remains visible without guessed digits.
- `office` and `offices` map to a list of rooms. Explicit room-code variants such
  as `ASB312` and `ASB-312` normalize alike. Slash-separated text is split only
  when every component is an explicit room code.
- Historical `prefers_email: false` means no observed preference and becomes
  canonical unknown. Its original flag and caveat remain. No preference is
  inferred from an email address or ordinary contact-note text.
- For the entity's own `name`, a published alias can normalize to the registry
  name only when another linked record publishes that registry name and alias
  provenance identifies an `identity_map`, `human_reviewed`, or `department`
  basis. Other names remain distinct. Original alias assertions keep a caveat.

There are no current catalog-credit, retirement-title, event-time, dietary-flag,
or derived-profile normalization rules in this office projection.

## Extension rule

Future fact capabilities must extend this shared reader and explicit projection
mapping. All normal application and Brain reads must consume those resolved values,
statuses, source evidence, and dated boundaries. Source-specific discovery may
remain separate, but no UI or separate answering route may independently choose
an authority, merge identities, reconcile conflicts, or infer missing values.

Add a field or entity kind only with a concrete use case, exact published identity
links, provenance-preserving mappings, and regression coverage for agreement,
conflict, unknowns, missing evidence, and date boundaries. Source publication
quality and model interpretation must be evaluated separately from reader behavior.
