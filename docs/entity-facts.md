# Entity facts: the shared read contract

Normal application and Brain fact reads resolve a canonical identity and use
`src/rockygpt_brain/retrieval/entity_facts.py`. Source collections provide discovery
and original evidence; they are not competing attribute authorities. A browser
must not reconcile raw rows or infer its own fact values.

## Current implementation: offices

The current rebuild reads **office contact facts only**. It does not yet implement
fact projections for people, venues, programs, clubs, events, buildings, schools,
subjects, courses, menus, or schedules. It does not implement developer graph
projections, profiles, paginated context groups, or versioned cursors.

The active consumers are:

- The chat `office_facts` tool, which discovers a canonical office and reads only
  the requested supported fields. Code renders every retrieved office result;
  the model does not select which published values or conflicts survive the answer.
- `GET /v1/entities/{entity_id}/facts`, which returns the same office facts for
  applications. It requires `dataset_version` and `identity_hash` query parameters.

The reader exposes schema version `3`, mapping version `entity-facts-1`. Those
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
`prefers_email`, `preferred_contact`, `contact_note`, and `website`.

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
