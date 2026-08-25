# RockyGPT Brain

A question goes in, an answer comes out.

```text
IN   the question + the current time
     -> one model call
OUT  the answer
```

No routing, no lookups, no filters. `DESIGN.md` says where it grows.

## Package layout

```text
rockygpt_brain/
├── api/          HTTP routes and public contracts
├── core/
│   ├── model.py            the model call
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

Set `OPENAI_API_KEY` and `OPENAI_CHAT_MODEL` in `.env`. `CAMPUS_TIMEZONE`
defaults to `America/New_York` and is the clock the model is given.

## Checks

```bash
ruff check src tests && mypy src/rockygpt_brain && pytest
```

`tests/` checks behaviour, not question cases. A test that named a question from
an evaluation suite would only prove that a case had been special-cased.

The UI surface remains `/v1/chat`, `/v1/feedback`, and the three
`/v1/admin/logs*` endpoints. Logs and memory reset when the process restarts.
