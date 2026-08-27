# RockyGPT Brain

A question goes in, an answer comes out — and a trace saying how Rocky
understood, routed, looked up, and wrote the answer.

```text
the question -> BRAIN #1 understands -> BRAIN #2 plans -> PYTHON normalizes + runs -> BRAIN #3 answers
```

A plan answers two routing questions, then supplies the fields its derived
lane needs. CODE names a capability, filters, and a generic operation — never
an intent. Every filter declares one shared value type — `enum`, `entity`,
`date`, `instant`, or `text`. Python validates those semantics, resolves
canonical execution values, and only then runs the capability.

The implemented CODE capabilities are `transportation`, `dining`, `events`,
`hours`, `courses`, `directory`, `calendar`, `clubs`, `locations`, and
`programs`. `shuttle` remains an accepted compatibility alias for
`transportation`. RAG retrieves cited campus-document passages. GENERAL
answers stable questions from model knowledge and searches the web for current
ones.

RAG is temporarily gated off by default while CODE is tested. A RAG-routed
turn returns `RAG is working progress` without document retrieval or an answer
model call. Set `RAG_ENABLED=true` to exercise the implemented RAG path.

## Package layout

```text
rockygpt_brain/
├── api/          HTTP routes and public contracts
├── brain/        understand, plan, execute, and write stages
├── capabilities/ CODE registry plus one executor per capability
├── lanes/        CODE, RAG, and GENERAL execution
├── context/      conversation memory and trace records
├── safety/       deterministic concern handling
├── services/     OpenAI, campus data, document retrieval, and web ports
├── config.py     environment settings
├── errors.py     public service-error taxonomy
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
sets the model behind BRAIN #1 and defaults to the same. `CAMPUS_TIMEZONE`
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
