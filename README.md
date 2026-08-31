# RockyGPT Brain

The current clean-room Brain exposes three endpoints:

- `GET /health`
- `GET /readiness`
- `POST /v1/chat`

Chat accepts one ordered `messages` array whose entries contain only `role` and
`content`. It passes that array to one OpenAI model call and returns
`{"answer":"...","model":"..."}`. It has no server memory, prompts, safety,
routing, tools, data, capabilities, or other Brain logic yet.

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
