# Design

Superseded by `spec/brain-contract.md`, which is normative. This file records only
what is built and what is not.

## Built

- Typed `Interpretation` with scope, danger, operation, access, relations,
  references and named time; no execution values.
- Declarative capability registry; undeclared means not executable.
- Compilation: capability and relation checks, total time resolution, entity
  mentions confined to declared roles, plan construction. Fails closed at every
  step and never widens.
- Generic extremal selection against declared orderings, gated on source
  completeness.
- Discriminated `Outcome` union with typed absence causes, measured zero as
  success, sealed cardinality assertions, and a precedence order under which
  composition cannot upgrade.
- One result per task, asserted against the task count.
- Code-assembled safety block, prepended regardless of what else the turn holds.

## Not built

Named so they are not mistaken for finished work.

- **Access and operation gate** (contract 4.3). `operation` and `access` are
  interpreted and carried, but nothing yet refuses on them. Write and personal
  requests do not fail closed.
- **Hard trigger** (contract 4.1). Danger classification is currently the
  Listener's alone; the deterministic layer that can only raise it is missing.
- **Turn state** (contract 8). No claims, no active subject, no pending request.
  Anaphors therefore return `clarify` rather than resolving, and the conversation
  domain returns `no_capability`.
- **Final guard** (contract 10). No post-generation value check.
- **Relevance floor** (contract 6.4). Unmeasured, so the documents domain
  reports `no_supporting_evidence` rather than manufacturing success.
- **Composition** (contract 6.1). Tasks are independent and each runs one
  operation; multi-operation tasks are not yet expressible.
