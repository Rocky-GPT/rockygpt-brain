# RockyGPT Brain — BASE

This repository contains the small Python brain for RockyGPT.

```text
QUESTION
  -> AI #1 UNDERSTAND
  -> structured Intent
  -> Python chooses CODE | RAG | MEMORY | GENERAL | SAFETY
  -> result JSON
  -> AI #2 COMMUNICATE
  -> answer
```

CODE and RAG are the heart of the hybrid design. CODE sends objective shuttle
questions to structured DATA. RAG sends campus policy and document questions to
DATA retrieval. MEMORY reads recent process-local turns. GENERAL lets AI #2
answer non-campus questions. SAFETY supplies a short emergency result.

There is no agent loop, planner, verifier, repair pass, database, claim ledger,
or orchestration framework in BASE.

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
