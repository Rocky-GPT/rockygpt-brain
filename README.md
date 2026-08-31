# RockyGPT Brain

The current clean-room Brain exposes three endpoints:

- `GET /health`
- `GET /readiness`
- `POST /v1/chat`

Chat accepts one ordered `messages` array whose entries contain only `role` and
`content`. It passes that exact ordered conversation to one OpenAI model call
with six transportation-only strict operations covering next trips, schedules,
clock-time availability, comparisons, clarification, and unsupported shuttle
requests. A tool call is converted to and validated against the preserved Step
5A shuttle contract, then returned as
`transportationInterpretation`; no tool call is an explicit non-selection and
keeps the model's normal chat answer. Shuttle selections return only a Step 5B
status message because database execution and final shuttle answers are not
implemented yet. There is no server memory, database lookup, shuttle answer
generation, or generic routing/tool framework.

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
