# Entity facts: the shared read contract

Normal fact retrieval starts with a canonical entity ID. Source tables and
artifacts remain the immutable evidence layer. Applications must not choose a
contact row instead of a faculty row to decide a person's email, or implement
value reconciliation in a browser.

The rule applies to every mapped entity kind: people, offices, venues, programs,
clubs, events, buildings, schools, subjects and catalog courses. Discovery still
uses collection-specific indexes. Repeated menus and schedules retain individual
record IDs and context; this contract does not make an undated schedule current.

## Implementation

`retrieval/entity_facts.py` owns `canonical_properties` and `EntityFacts`.
`retrieval/projection.py` supplies the existing explicit field mappings and exact
identity-bound readers. No persistent facts table or second identity registry is
introduced. The read model applies immediately to existing published releases.

Consumers use schema 3, mapping `entity-facts-1`:

- `GET /v1/entities/{entity_id}/facts`: published entity details for applications.
- `GET /v1/dev/graph/projection/v3`: the same facts plus paginated contextual
  records for the developer explorer.
- Brain entity/contact/profile retrieval: the same resolver, with original records
  retained for evidence verification and citations. Targeted profile requests
  resolve only their requested evidence, without fetching all entity collections.
- Student directory: canonical entity discovery, with fact details loaded when
  selected. Listing the directory does not run one detail query per person.

HTTP readers require `dataset_version` and `identity_hash`. A changed pin returns
409. Context cursors also bind the fact version, entity, group, filter and page
size. Version 2 and raw-record developer APIs remain diagnostic/compatibility
surfaces; they are not the normal fact access contract.

## A fact and its evidence

Each property has one key, a label, category, status, canonical `values`, and the
original `assertions`. A value names every supporting assertion and evidence
record. `sources` gives original row/artifact location, capture time, validity,
freshness and caveats. Validation rejects missing or fabricated support.

Statuses describe the observations, not official authority or freshness:

- `known`: one distinct nonempty value, possibly accompanied by missing values.
- `unknown`: no nonempty value is available.
- `conflicting`: different values have overlapping or unspecified validity.
- `multiple`: different values have disjoint explicit validity intervals.

The resolver chooses no winner. Empty observations remain visible in provenance;
they do not overwrite a populated value. Actual false and zero remain distinct
values. Unpublished dietary flags remain unknown even if storage holds false or
an empty list; the original assertion and caveat remain available.

Equality is deliberately conservative: JSON object key order does not matter,
but primitive types, text case and array order do. Declared source cleanup still
applies: HTML entities/whitespace, catalog credit representation and event date
precision reuse the existing normalization rules. No fuzzy joins or string-based
entity merging take place here. Email text is not rewritten.

Declared representation aliases are shared backend rules:

- `phone` and `phones` become `phones`, a structured list. Unambiguous North
  American formatting is normalized; extensions and types remain significant.
  Unparsed text is retained rather than guessing missing digits or domains.
- `office` and `offices` become `offices`, a list. A joined display string is split
  only when its components are explicit room codes; ASB312 and ASB-312 share one
  canonical room code.
- Historical contact `prefers_email: false` means no observed preference, hence
  canonical unknown. The raw flag is retained. Email mentions only establish
  preference when an explicit supported preference phrase appears in the source
  note. New data publications use nullable preference.
- Explicit retirement suffixes become a clean title plus a `retired` status, both
  retaining the original title assertion. No suffix does not mean active.
- Event dates without a published clock remain date-only values with a caveat;
  parser-generated midnight is never exposed as an established start time.

Department and school are separate concepts; source contact type is not entity
kind. Other property mappings retain their existing meanings.

## Evidence is not a vote

`evidence_count` counts original records, not independent sources or confidence.
A faculty contact may be generated from the same original faculty profile.
Where the publisher's exact derivation key and compatible email identify one
linked original, `derived_from_source_id` records that relationship and its caveat.
Ambiguous lineage remains unspecified. Two representations do not create two
independent confirmations.

The release's original source records, identity links and relationships are not
deduplicated or overwritten. Citation URLs and capture histories remain attached
to each record. One convenient access path does not erase the evidence chain.

## Extension and validation

Add fields through the explicit projection mapping, then add any necessary
semantic aliases here with regression cases. A new browser must consume the
resolved values and statuses; it must not rebuild fact identity from raw rows.
Discovery/list endpoints may return snippets, but canonical IDs lead to the fact
reader for answers. Raw evidence remains available for audits and source review.

Regression coverage includes equal multi-record values, differing values, unknown
observations, type-sensitive equality, phone extensions, validity intervals,
exact derivation, missing evidence, contextual record boundaries, release pins,
versioned cursors and evidence-budget truncation. Source-record reads for an
entity are reused by citation hydration; property-only requests skip menu/hours
queries. Publication and source-quality issues remain separate from read-model
correctness.
