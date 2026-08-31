# RockyGPT Brain

The current clean-room Brain exposes three endpoints:

- `GET /health`
- `GET /readiness`
- `POST /v1/chat`

Chat accepts one ordered `messages` array whose entries contain only `role` and
`content`. Normal chat passes that array to one OpenAI model call. A direct
next-shuttle question adds one deterministic fact calculated from Ramapo
College's official Fall 2026 schedules in the campus time zone, then lets that
same model call phrase it. The response remains `{"answer":"...","model":"..."}`
for normal chat and includes `shuttleFact` for a shuttle answer so its source
and calculation can be inspected. There is no server memory or generic
capability, routing, tool, or data framework.

## Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
uvicorn rockygpt_brain.api.app:app --host 127.0.0.1 --port 8000
```

Set `OPENAI_API_KEY` in `.env`. `OPENAI_CHAT_MODEL` is optional and defaults to
`gpt-4o-mini`.

## Checks

```bash
ruff check .
mypy src tests
pytest
```
