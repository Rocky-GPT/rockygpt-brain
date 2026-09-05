# RockyGPT Brain

A student assistant for Ramapo College, grounded in the currently published
campus dataset. One model-driven conversation loop searches and reads official
campus evidence, then produces a cited draft. A separate evidence review must
approve every part before the answer is returned. There is no intent classifier,
phrase routing, vector service, process conversation memory, or generated SQL.

## Run

```sh
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
uvicorn rockygpt_brain.api.app:app --host 127.0.0.1 --port 8000
```

`.env` loads from the working directory without overriding exported variables.
Set `OPENAI_API_KEY` and `DATABASE_URL`. Use a read-only database role with SELECT
access to the published `rockygpt_v2` tables. `OPENAI_CHAT_MODEL` defaults to
`gpt-5.4`; use a model supporting Responses function calls and structured output.
If `STAGING_SERVICE_TOKEN` is set, chat requires the matching
`x-rockygpt-environment-token` header. Keep it identical in UI and Dev.

## HTTP contract

- `GET /health` and `HEAD /health`: process liveness, independent of dependencies.
- `GET /readiness`: checks model-key configuration and a read-only database
  connection with an active release. This does not call the model or prove every
  source is fresh; freshness is checked when records are retrieved.
- `POST /v1/chat`: one JSON response, with the complete conversation supplied
  on every request. The generated schema is served at `/openapi.json`.

```json
{"messages":[{"role":"user","content":"How do I contact Financial Aid?"}]}
```

The response has `answer` (Markdown with validated source links), `status`
(`answered`, `partial`, `clarification`, or `unavailable`), `citations`, `model`,
`requestId`, `datasetVersion`, `elapsedMs`, `trace`, and `metrics`. Each citation includes
its evidence ID, title, URL, collection, collection timestamp, freshness, trust,
validity, and limitations. Trace exposes only tool names, arguments, result
counts, search coverage, statuses, and duration; it does not contain model reasoning.
Metrics distinguish draft and review model calls, requested and executed tools,
and fixed validation-failure codes. Rejected answer text and review explanations
are not returned. Invalid-output logs contain only the request ID and reason code.

Clients append the returned answer as an assistant message before the next user
message. Prior assistant text resolves references but is not authoritative;
campus evidence is retrieved anew on each turn. Failed requests are not appended.
No student text or conversations are persisted by Brain. Model calls use
`store=false`; this does not alter the provider's account-level retention policy.

Requests are capped at 64 KiB, 80 messages, 16,000 characters per message, and
48,000 total content characters. Oversized histories are rejected explicitly.
Answers are capped at 12,000 characters so they fit in a subsequent request.
There are at most four active turns per process, eight model calls in total
(at most six draft/tool calls, with capacity reserved for review), twelve admitted tool attempts, a
50-second execution budget, and a 52-second HTTP deadline. Retrieval has a
30-second deadline, leaving time for synthesis and review. Exhausting retrieval
does not disable answer repair. Provider connection attempts are capped at two
seconds per address; model reads share the remaining turn budget. Timed-out
workers retain their slot until provider and database cleanup finish. Validation errors use HTTP 422; upstream
errors use 429/502/503/504 with a safe structured error and a request ID.

Campus panel, feedback, admin-log, and classifier endpoints are not part of this
Brain. The student and Dev chat interfaces use the contract above. No database
migration or write is performed.

## Verification

```sh
ruff check .
mypy src tests
pytest -q
```

Run realistic live conversations from the sibling eval repository:

```sh
python ../rockygpt-evals/brain-reset/run.py --base-url http://127.0.0.1:8000 \
  --output ../rockygpt-evals/brain-reset/results/checkpoint.json
```

The harness checks contracts and replays actual generated conversation history.
Review answers against retrieved facts as well: citation presence does not prove
that a source supports every assertion. See [architecture](spec/system-boundaries.md)
and [checkpoint verification](docs/verification.md).
