# RockyGPT brain implementation rule

`spec/brain-contract.md` is normative. Read it before changing anything in
`core/`. It defines the semantics every capability must obey; this file only
says how to work within it.

The hardening phase is open. The BASE rule that forbade typed policy, claim
state, and verification no longer applies — those are now contract requirements,
not additions. What is still forbidden is unbounded machinery: no agent loops, no
repair passes, no re-planning, no orchestration framework, no database.

The invariant:

```text
The Listener interprets.
The Worker decides and computes.
The Writer communicates.
```

Two rules that decide most questions:

**Fail closed, never widen.** A missing, unresolvable, or undeclared value
produces a typed outcome. It never produces a broader query, a default, or a
nearby endpoint. There is no `or <default>` on a model-supplied value anywhere in
the execution path.

**No case-by-case behaviour.** Repairs are made at the layer whose invariant was
violated. No phrase, entity, or expected answer from any test suite appears in
production code or prompts. A condition that names a test case is a contract
defect that has been hidden rather than fixed.

The brain reads campus information only through DATA HTTP APIs, and preserves the
existing `/v1` UI response shape.
