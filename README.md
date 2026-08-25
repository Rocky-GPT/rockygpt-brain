# RockyGPT Brain

A question goes in, an answer comes out — and a plan saying what Rocky would
have done to look it up.

```text
the question -> BRAIN #1 plans it -> PYTHON runs the lane -> BRAIN #2 answers
```

A plan is a lane, a capability, filters, and a generic operation — never an
intent. Python checks it against the capability registry before it would run.
Nothing executes a plan yet. `DESIGN.md` says where it grows.

## Package layout

```text
rockygpt_brain/
├── api/          HTTP routes and public contracts
├── core/
│   ├── plan.py             the vocabulary a plan is written in
│   ├── capabilities.py     what Rocky can look up, and with which fields
│   ├── planner.py          AI #1 — question in, plan out
│   ├── validate.py         the check applied before a plan runs
│   ├── execute.py          PYTHON — run the lane
│   ├── model.py            BRAIN #2 — the answer call
│   └── brain.py            the request lifecycle
├── services/     turns and the admin log
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

Set `OPENAI_API_KEY` and `OPENAI_CHAT_MODEL` in `.env`; `OPENAI_PLANNER_MODEL`
sets the model behind AI #1 and defaults to the same. `CAMPUS_TIMEZONE`
defaults to `America/New_York` and is the clock both calls are given, and the
clock every time word in a plan is resolved against.

## Checks

```bash
ruff check src tests && mypy src/rockygpt_brain && pytest
```

`tests/` checks behaviour, not question cases. A test that named a question from
an evaluation suite would only prove that a case had been special-cased.

The UI surface remains `/v1/chat`, `/v1/feedback`, and the three
`/v1/admin/logs*` endpoints. Logs and memory reset when the process restarts.
