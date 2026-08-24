# RockyGPT Brain — BASE

This repository contains the small Python brain for RockyGPT.

```text
QUESTION
  -> AI #1 UNDERSTAND
  -> strict lane-specific semantic Intent
  -> Python chooses CODE | RAG | MEMORY | GENERAL | SAFETY
  -> Python validates and compiles CODE against its capability registry
  -> CODE executes the compiled filter / order / limit operations
  -> result JSON
  -> AI #2 COMMUNICATE
  -> answer
```

CODE and RAG are the heart of the hybrid design. CODE sends objective campus
questions—menus, hours, contacts, events, programs, maps, and shuttles—to
structured DATA, then finishes the requested record operations in Python. RAG
sends campus policy and document questions to DATA retrieval. MEMORY reads recent
process-local turns. GENERAL lets AI #2 answer non-campus questions. SAFETY
supplies a short emergency result.

AI #1 describes meaning; it never chooses DATA field paths. Every lane has its
own strict output shape, and every CODE action has action-specific filters. A
single Python capability registry maps semantic concepts such as `time`, `date`,
or `calories` to real structured fields. Unsupported computations return an
explicit structured result instead of silently selecting an arbitrary record.

There is no agent loop, planner, verifier, repair pass, database, claim ledger,
or orchestration framework in BASE.

## Package layout

```text
rockygpt_brain/
├── api/          HTTP routes and public contracts
├── core/         hybrid pipeline and the two AI calls
├── services/     DATA client and process-local memory
├── config.py     environment settings
├── errors.py     shared API error
└── main.py       process entry point
```

## Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
rockygpt-brain
```

Set `OPENAI_API_KEY`, `OPENAI_CHAT_MODEL`, and `DATA_URL` in `.env`.

The current UI surface remains available at `/v1/chat`, `/v1/feedback`, and the
three `/v1/admin/logs*` endpoints. Logs and memory reset when the process restarts.
