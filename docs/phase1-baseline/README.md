# Pre-implementation baseline

Captured from the current checkout on September 11, 2026, before source changes.
`manifest.json` records its code/configuration identity and the pre-existing
modified plan. No prior branch or commit was restored.

- `openapi.json`: public API schema before Phase 1.
- `conversations.json`: current 20-conversation / 27-turn regression corpus.
- `evidence-gate-cases.json`: current 27-case synthetic review corpus with dated
  public evidence, expected outcomes, and provenance.
- `representative-data.json`: 77 distinct public evidence representations from
  those cases, retaining differing excerpts and source metadata.

This is frozen component evidence, not a fresh Brain run, a full dataset export,
or a new verification of campus facts. It can be replayed without provider calls.
