# Record cleanup — September 21, 2026

Applied against the local `dev-profiles-full-20260921` release. Contacts, campus hours, and dining hours are outside this pass. Raw evidence, occurrence IDs, dates, and source timestamps are preserved. This is normalization of available evidence, not a fresh verification of every campus source.

## Menu

- 887 stored dated occurrences; 869 food offerings after excluding 18 exact `Have a Nice Day` source messages. Components remain available.
- 846 published calorie values are integers, including zero. Nutrition is never joined by food name.
- Export has `name` without a redundant `title`. Every returned offering has Birch Tree Inn's persistent venue ID `c98a4db7-9948-4e2c-98b5-4fe3f73ae488`; offerings remain separate records.
- The release snapshot contains explicit vegan/vegetarian booleans for all original occurrences. The backfill restores their label coverage from exact date/meal/station/name matches, so false is not discarded as unknown. Missing labels in other snapshots are not inferred false.
- Missing allergens remain unknown (`null` in the export). A published empty list means none listed; neither establishes allergy safety.
- Raw collector samples expose `portionSize`/`portion`, now preserved on future ingestion. The current normalized September snapshot already discarded those fields; they cannot be recovered from it and are not invented.
- Raw `course` repeats the station; no supported dish/component hierarchy was found in the inspected payload. No AI classifier or calorie threshold assigns an item type. Source spellings such as `French Toash` are preserved.
- Broad dinner questions use reviewed prose, with instructions to select a few recognizable prepared dishes. Exact menu/list requests retain components and citations. Regression tests verify that broad meal questions cannot use the automatic exhaustive formatter.
- `scripts/normalize-menu.ts` supports preview and `--apply --backup <new-file.json>`. Migration `020_menu_nutrition.sql` converts calories and adds optional portions. The script also removes the exact non-food message from menu artifacts, the menu document, and search passages without replacing their IDs.

## Other collections

| Collection | Records | Cleanup / decision |
| --- | ---: | --- |
| Critical facts | 14 | ISO timestamp serialization. Preserve units, money, phone values, and historical applicability; no blanket numeric conversion. |
| Academic calendar | 106 | ISO timestamps; preserve source date labels, semester/session distinctions, and historical entries. |
| Events | 316 | 76 `-` location placeholders become unknown. 27 sign-in-only and 21 registration-only locations become unknown with explicit access requirements. Keep recurring dates separate. |
| Clubs | 254 | Remove redundant export title; retain published categories and source URLs. These records include departments and offices as well as student organizations; do not relabel all as student clubs. |
| Programs | 267 | Distinguish 146 program records from 121 catalog convener evidence records. Decode text entities. Same-name enrichment requires an unambiguous source URL. Preserve legacy career snippets as partial `program_page_excerpt`, not a verified Careers section. |
| Program requirements | 1,007 | Include the specific program source URL. Four apparently duplicated rule sets belong to different Nursing catalog URLs and are retained. Nested AND/OR rules and selection counts are untouched. |
| Courses | 3,344 | Numeric fixed credits; retain explicit ranges, including zero. The 47 min-only zero defaults do not establish a credit total and become unknown. Preserve fractional credits, course codes, and original descriptions. Fix collector behavior that previously selected `min` and discarded the rest of a range. |
| Faculty | 226 | Blank scalar fields become unknown; empty profile lists have unpublished coverage. Preserve real job titles, undated teaching lists, email preference notes, and extensions. Export formatting standardizes complete US phone numbers while identity evidence retains original source formatting. |
| Shuttle | 51 | No substantive cleanup needed. Keep route/day/sequence, outbound/return meaning, and stop order intact. |
| Documents | 14 | No empty titles or content found. Remove the known menu artifact from the menu document and its search passages; preserve other source documents. |

The nine non-document collections total **5,585 records**. The before/after audit confirmed unchanged record-ID sets in every collection. Cleanup for these collections occurs in retrieval/export, leaving source tables and artifacts available for audit. Internal evidence keeps its citation title; redundant presentation titles are removed only from the public record export. Source URLs distinguish otherwise similar records.

## Verification

Brain regression suite: 460 passed, 36 environment-dependent tests skipped. Data suite: 104 passed, 4 environment-dependent tests skipped. Data build, TypeScript checks, and ESLint error checks pass. New normalization module passes Ruff and mypy. Existing unrelated repository lint findings are not part of this change.

The local database audit verified 869 returned menu offerings, 869 venue links, 846 numeric calorie values, and 465 explicit false vegan labels. The approved live test against the updated code passed: the answer selected the eggplant bowl, chicken stir-fry, noodle stir-fry, rice, egg roll, and tofu, omitting onions and ginger even though those components were retrieved. It used reviewed prose with citations and identified the selection as a sample. The first test reached the previously pinned older local server build and still enumerated garnishes; the updated-code test used the matching release fingerprint.
