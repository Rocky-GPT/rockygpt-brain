# RockyGPT Brain

`spec/brain-contract.md` is the normative document. This file is orientation.

```text
QUESTION
  -> LISTENER      interprets meaning into a typed Interpretation
  -> SAFETY        code-assembled block when a danger class is present
  -> WORKER        compile -> execute -> select -> seal, once per task
  -> OUT           one discriminated outcome per task
  -> WRITER        communicates the sealed outcomes
  -> ANSWER
```

The invariant the whole design serves:

```text
The Listener interprets.
The Worker decides and computes.
The Writer communicates.
```

The Listener emits no execution — no resolved dates, no weekday names, no sort
fields, no limits, no endpoints, no query predicates. It names relations,
references and time the way the reader did. The Worker compiles that against a
declarative capability registry, resolves every temporal and entity value,
executes, performs any selection the transport did not, and seals exactly one
outcome. The Writer receives only sealed outcomes and adds no facts.

Absence is typed. `entity_unknown`, `no_qualifying_records`,
`no_supporting_evidence`, `no_capability`, `out_of_scope` and `incomplete_source`
mean different things and render differently; none of them describes the world. A
measured zero is a `success`, not an absence.

Capabilities are declarations, not branches. A domain states its relations,
entity roles, constraints, accepted time references, orderings and absence causes;
anything undeclared is `no_capability`. Extremal relations are computed
generically against a declared ordering, and only over a result set the source
reports as complete.

## Package layout

```text
rockygpt_brain/
├── api/          HTTP routes and public contracts
├── core/
│   ├── interpretation.py   the Listener's schema
│   ├── capabilities.py     declarations
│   ├── compilation.py      Interpretation -> operations; all time arithmetic
│   ├── selection.py        ordering and completeness
│   ├── executor.py         the Worker
│   ├── outcomes.py         the discriminated OUT union
│   ├── safety.py           code-assembled emergency replies
│   ├── model.py            the two model calls
│   └── brain.py            the turn
├── services/     DATA client and process-local memory
├── config.py     environment settings
├── errors.py     shared API error
└── main.py       process entry point
```

## Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
rockygpt-brain
```

Set `OPENAI_API_KEY`, `OPENAI_CHAT_MODEL`, and `DATA_URL` in `.env`.
`CAMPUS_TIMEZONE` defaults to `America/New_York` and is where every date is
resolved.

## Checks

```bash
ruff check src tests && mypy src/rockygpt_brain && pytest
```

`tests/` holds contract properties, not question cases. A test that names a
question from an evaluation suite would only prove that a case had been
special-cased; see `spec/brain-contract.md` section 13.

The UI surface remains `/v1/chat`, `/v1/feedback`, and the three
`/v1/admin/logs*` endpoints. Logs and memory reset when the process restarts.
