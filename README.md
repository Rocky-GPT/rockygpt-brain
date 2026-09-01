# RockyGPT Brain

The current clean-room Brain exposes three endpoints:

- `GET /health`
- `GET /readiness`
- `POST /v1/chat`

Chat accepts one ordered `messages` array whose entries contain only `role` and
`content`. It passes that exact ordered conversation to one OpenAI model call
with six transportation-only strict operations covering next trips, schedules,
clock-time availability, comparisons, clarification, and unsupported shuttle
requests. A selected operation is validated against the Step 5A contract and
executed deterministically against the active trusted shuttle rows in the
RockyGPT database. The API returns the interpretation, structured result,
source provenance, and a grounded answer that is explicitly labeled as
scheduled timetable data rather than live GPS or ETA data. No operation call
keeps the model's normal chat answer. There is no server memory or generic
routing/tool framework.

## Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
uvicorn rockygpt_brain.api.app:app --host 127.0.0.1 --port 8000
```

Set `OPENAI_API_KEY` and `DATABASE_URL` in `.env`. `OPENAI_CHAT_MODEL` is
optional and defaults to `gpt-4o-mini`.

## Checks

```bash
ruff check .
mypy src tests
pytest
```
