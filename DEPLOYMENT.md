# Deployment

This document describes how to run and deploy the candidate brain. Per
`CLAUDE.md` and `spec/acceptance.md`'s promotion rule: **standing this
service up does not switch any traffic.** `rockygpt-ui`'s `BRAIN_URL` stays
pointed at the existing brain until this service has passed every
deterministic gate, the model-backed answer-quality suite, a separate
preview deployment, and a rollback rehearsal (see `ROLLBACK.md`).

## Topology

```
data (deploy first) -> brain (this repo) -> ui (deploy last)
```

`rockygpt-infra`'s documented deployment order is data, then brain, then
UI. The brain needs `DATA_URL` reachable and its own Postgres schema
available before it can report ready.

## Configuration

All configuration is environment variables — see `.env.example` (local)
and `.env.staging.example` (staging) for the full list with comments.
Nothing here is baked into the container image.

| Variable | Required | Notes |
| --- | --- | --- |
| `APP_ENV` | no (default `development`) | `development`, `staging`, or `production`. |
| `HOST` / `PORT` | no | Bind address; `0.0.0.0`/`8000` is the container default. |
| `OPENAI_API_KEY` | yes, for chat to work | The process starts and `/readiness` still reports on database/dataset without it; `/v1/chat` fails per-request (`503` from `brain/model_client.py`) if it's missing/invalid. |
| `OPENAI_CHAT_MODEL` | no (default `gpt-4o-mini`, `config.py`) | Override to pin a specific model. |
| `DATABASE_URL` | **yes** | Without it, `app.state.db_pool` stays `None` at startup and **`POST /v1/chat` fails fast with `503 SERVICE_UNAVAILABLE` before any model call** (`api/chat.py` checks `db_pool`/`CHAT_LOG_HASH_KEY` up front, ahead of `run_chat_turn` — a chat turn is never computed unless it can also be persisted, so there's no reason to spend a paid model call first only to discover persistence is unavailable). `/readiness` also reports `database` failing. Must be scoped to a brain-owned schema only (spec/system-boundaries.md). |
| `CHAT_LOG_HASH_KEY` | yes, whenever `DATABASE_URL` is set | Startup fails fast (the process refuses to start) if `DATABASE_URL` is configured without this — persisting chat logs without a hash key would mean storing raw identifiers, which is never acceptable. Generate with `openssl rand -hex 32`. |
| `ADMIN_API_TOKEN` | no | Leave unset to keep `/v1/admin/*` entirely unregistered (404) in an environment where operator access isn't wanted. |
| `ABUSE_HASH_KEY` | no, recommended | Must exactly match the value configured in `rockygpt-ui`. Enables trusting the UI's signed abuse identity; without it every caller is treated as unsigned for rate-limiting purposes. |
| `STAGING_SERVICE_TOKEN` | staging only | When set, every functional route (not `/health`/`/readiness`) requires the matching `x-rockygpt-environment-token` header. Leave unset in production. |

## Running locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # already done by start-claude-cleanroom; fill in secrets
uvicorn rockygpt_brain.main:app --reload
```

`GET /health` runs with no configuration at all. `GET /readiness` is
callable with no configuration too, but will correctly report `unready`
(`503`, with `failing: ["database"]` and/or `["dataset"]`) whenever
`DATABASE_URL` isn't configured or the data service isn't reachable — it
is not exempt from needing real configuration to ever report `ready`.
`POST /v1/chat` needs `OPENAI_API_KEY`, a reachable `DATA_URL`, and a
working `DATABASE_URL`/`CHAT_LOG_HASH_KEY` (see the table above for the
exact fail-fast behavior). `/v1/feedback` and `/v1/admin/logs*` likewise
need `DATABASE_URL` and `CHAT_LOG_HASH_KEY`.

## Building and running the container

```bash
docker build -t rockygpt-brain .
docker run --rm -p 8000:8000 --env-file .env rockygpt-brain
```

The image's `HEALTHCHECK` calls `GET /health` on whatever `PORT` the
container was started with. `CMD` runs the `rockygpt-brain` console script
(`main.run()`), which itself reads `HOST`/`PORT` from the environment —
there is no hardcoded port anywhere in the image.

## Database schema

`src/rockygpt_brain/persistence/schema.sql` is applied idempotently
(`CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`) at process
startup — there is no separate migration step to run before deploying. The
brain-owned schema is a single `chat_logs` table; see `DESIGN.md` §6 for
its retention/expiry design.

**The database credential must be scoped to this schema only** — it must
not have access to the data service's schema (spec/acceptance.md). That
scoping is provisioned outside this repository (by whoever creates the
credential), not enforced by application code.

## Readiness and orchestration

- `GET /health`: liveness only, no dependency checks, always fast.
- `GET /readiness`: checks the brain's own database and the data service
  concurrently under one shared ~2.5s deadline; returns `503` with a
  `failing` array (`database` and/or `dataset`) if either isn't
  configured/reachable. Never checks the model provider — see `DESIGN.md`
  §8 for why.

Point the orchestrator's liveness/readiness probes at these two paths
respectively.

## Staging smoke checks

With `UI_URL`, `BRAIN_URL`, `DATA_URL`, and the staging token available to
`rockygpt-evals`/`rockygpt-infra`, `spec/acceptance.md`'s "Deployment
smoke" section is the black-box script to run against a staging
deployment before ever considering promotion. It specifically includes: a
`POST /v1/chat` with body `null` returning `400` without a model call, and
missing staging credentials returning `401` on functional routes.

## Promotion

Do not repoint `rockygpt-ui`'s `BRAIN_URL` at this service until all of the
following have passed, per `spec/acceptance.md`:

1. Every deterministic contract/security/robustness gate in
   `spec/acceptance.md`.
2. The model-backed answer-quality suite (`rockygpt-evals`).
3. A separate preview deployment exercised end-to-end.
4. A rollback rehearsal — see `ROLLBACK.md`.
