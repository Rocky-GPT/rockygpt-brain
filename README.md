# RockyGPT Brain

The clean-room Brain currently acts only as a capability classifier. It exposes:

- `GET /health`
- `GET /readiness`
- `POST /v1/chat`

Chat accepts one ordered `messages` array whose entries contain only `role` and
`content`. One constrained OpenAI Responses API call assigns the latest request,
using its conversation context, to exactly one label:

`transportation`, `dining`, `events`, `hours`, `directory`, `locations`,
`courses`, `programs`, `clubs`, `academic_calendar`, `campus_documents`,
`student_services`, `it_support`, `personal_account`, `general`, or
`clarification`.

The selected label is returned in the existing `answer` field. The Brain does
not execute capabilities, query campus data, or answer the underlying question.
Model instructions live in `src/rockygpt_brain/capabilities/prompt.md`; runtime
Python contains no embedded prompt text.

The reusable labeled classifier evaluation lives at
`evals/capability_classifier.json`. Every case owns its ordered conversation,
so independent questions never inherit history from another case.

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

Run the fixed live-model evaluation once with:

```bash
python scripts/evaluate_classifier.py
```
