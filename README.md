# RockyGPT Brain

A small hybrid brain: one model call to understand, one lane of Python to do the
work, one model call to write the answer.

```text
question
  -> AI #1 understands and picks one lane
  -> CODE | RAG | GENERAL | SAFETY | MEMORY
  -> a small JSON result
  -> AI #2 writes the answer
```

CODE is structured campus facts plus the deterministic work — resolving the date,
asking DATA, ordering records, picking the one that was asked for. RAG is policies
and documents. GENERAL is anything not about the college. SAFETY covers
emergencies, privacy, credentials, and actions Rocky cannot take, with wording
fixed in code. MEMORY is what was said earlier.

AI #1 never resolves a date, sorts, or picks a record — it is not even told the
current time, so it has to name "today" rather than work it out. AI #2 never adds
a fact, re-picks a record, or decides what an empty result meant.

`DESIGN.md` lists the known limits.

## Package layout

```text
rockygpt_brain/
├── api/          HTTP routes and public contracts
├── core/
│   ├── intent.py           what AI #1 returns
│   ├── capabilities.py     where each CODE topic lives in DATA
│   ├── lanes.py            the five lanes and their results
│   ├── safety.py           fixed refusal and emergency wording
│   ├── model.py            the two AI calls
│   └── brain.py            the request lifecycle
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

`tests/` checks behaviour, not question cases. A test that named a question from
an evaluation suite would only prove that a case had been special-cased.

The UI surface remains `/v1/chat`, `/v1/feedback`, and the three
`/v1/admin/logs*` endpoints. Logs and memory reset when the process restarts.
