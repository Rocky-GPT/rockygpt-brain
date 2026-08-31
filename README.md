# RockyGPT Brain

The current clean-room Brain exposes three endpoints:

- `GET /health`
- `GET /readiness`
- `POST /v1/chat`

Chat accepts one ordered `messages` array whose entries contain only `role` and
`content`. Normal chat passes that array to one OpenAI model call. A direct
next-shuttle question adds one deterministic fact calculated from Ramapo
College's active trusted database dataset in the campus time zone, then lets
deterministic code render the final answer so user wording or model output
cannot contradict the selected trip. The response remains
`{"answer":"...","model":"..."}` for normal chat and includes `shuttleFact`
for a shuttle answer so the selected trip, dataset version, source, and
calculation can be inspected. If trusted data is unavailable, the Brain does
not fall back to model knowledge. There is no server memory or generic
capability, routing, tool, repository, or data framework.

## Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
uvicorn rockygpt_brain.api.app:app --host 127.0.0.1 --port 8000
```

Set `OPENAI_API_KEY` and `DATABASE_URL` in `.env`. `OPENAI_CHAT_MODEL` is
optional and defaults to `gpt-4o-mini`; `CAMPUS_TIME_ZONE` defaults to
`America/New_York`.

## Checks

```bash
ruff check .
mypy src tests
pytest
```
