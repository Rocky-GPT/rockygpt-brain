# RockyGPT brain clean-room assignment

Build a new Python implementation of the RockyGPT brain from first principles.

## Clean-room rule

This repository is the complete context for the work. Do not inspect, search,
clone, fetch, import, compare with, or ask for access to any previous RockyGPT
brain implementation, its Git history, generated artifacts, prompts, tests, or
packages. Do not inspect sibling RockyGPT repositories. Do not query the
currently deployed brain as an implementation oracle.

The immutable files under `spec/` describe only externally observable product
requirements and HTTP boundaries. They do not prescribe architecture,
framework, prompts, retrieval strategy, model-calling strategy, database
layout, or module structure. Make those choices independently and explain them
in design notes you create outside `spec/`.

If required behavior is missing or contradictory, stop and ask the user. Do
not resolve ambiguity by seeking the old implementation.

## Non-negotiable boundaries

- Python is the implementation language.
- The service must work from a standalone checkout.
- Other RockyGPT applications are HTTP services, never source dependencies.
- Campus data is read only through `DATA_URL` using the contract in `spec/`.
- The brain owns model calls, grounding, safety decisions, conversation state,
  its persistence, chat logs, and feedback.
- The UI owns browser state, browser validation, and presentation.
- Do not access the data service database or schema directly.
- Preserve `/v1`; breaking experiments belong behind a new version.
- Never put secrets in source control or log them.

## Delivery order

1. Write an independent architecture proposal and threat model.
2. Scaffold the Python project, formatter, linter, type checker, and tests.
3. Implement liveness/readiness and strict request validation.
4. Implement the data HTTP client and provenance model.
5. Implement safety and grounded answer behavior.
6. Implement chat, feedback, privacy-preserving persistence, and operator APIs.
7. Pass the contract and acceptance gates in `spec/`.
8. Produce a container/deployment definition and rollback notes.

Do not switch any deployed UI or overwrite the existing brain service. This
repository produces a candidate replacement that must pass black-box gates
before it receives traffic.
