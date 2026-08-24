# Deployment

Deploying this candidate does not switch traffic. DATA deploys first, then the
brain, then a preview UI. Production `BRAIN_URL` changes only after the gates in
`spec/acceptance.md` and a rollback rehearsal pass.

## Runtime contract

- `GET /health` is fast process liveness and calls no dependency.
- `GET /readiness` is public, finishes within three seconds, and reports
  required database, DATA, and model-configuration failures.
- Functional routes enforce the staging environment token when configured.
- Chat returns one complete JSON response inside the UI's 60-second timeout;
  the brain's internal deadline is 55 seconds.
- Request bodies are capped at 64 KiB using both declared length and bytes read.
- OpenAI calls use the Responses API with `store=false`.

Readiness validates that a model API key and explicit model name are configured;
it does not make a paid/live model request. A configured credential can still
fail at request time, which maps to the frozen service-unavailable envelope.

## Configuration

Use `.env.example` locally and `.env.staging.example` for staging. Never commit
real secrets.

| Variable | Requirement |
| --- | --- |
| `APP_ENV` | `development`, `staging`, or `production`; defaults are runtime-owned. |
| `HOST`, `PORT` | Bind address and port; local UI defaults to brain port `8000`. |
| `OPENAI_API_KEY` | Required for readiness and chat. |
| `OPENAI_CHAT_MODEL` | Explicit model used by the bounded understanding and communication calls; required for readiness. |
| `DATABASE_URL` | Required brain-owned PostgreSQL; the credential must have no DATA-schema access. |
| `DATA_URL` | Required HTTP origin for campus capabilities and retrieval. |
| `CHAT_LOG_HASH_KEY` | Independent secret for durable pseudonyms; generate at least 32 random bytes. |
| `ADMIN_API_TOKEN` | Required only in an operator/development deployment that registers admin routes. |
| `ABUSE_HASH_KEY` | Must match the UI value to verify its signed pseudonymous client key. |
| `STAGING_SERVICE_TOKEN` | Shared staging credential; unset locally and in production. |

`ABUSE_HASH_KEY` authenticates only the abuse identity. It is not an
authorization credential and must not be reused as the durable identifier key.

## Start locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
uvicorn rockygpt_brain.main:app --reload
```

Then check:

```bash
curl -s http://127.0.0.1:8000/health
curl -s http://127.0.0.1:8000/readiness
```

Health can succeed without configuration. Readiness must remain `503 unready`
until PostgreSQL and DATA are reachable and the model key/name are configured.

## Persistence

The deployment must provision a brain-owned schema before chat is promoted.
Successful chat response, memory updates, and durable evidence/source snapshots
commit atomically. This ensures feedback can refer to the returned request ID and
later conversation-truth turns can recover the exact source/version context.

Accepted failed turns are written as redacted operational records when the
persistence dependency is available. They contribute to error metrics but do
not change recent memory, claim ledgers, entities, or corrections.

Retention jobs enforce:

- 30 days maximum for redacted question, answer, and claim text;
- 90 days maximum for redacted feedback and non-text operational metadata.

The exact migration command, cleanup scheduler, and multi-instance SSE/log
notification mechanism remain implementation choices and must be documented
once the runtime is present. Deployment must not assume startup-created tables
or a particular table layout.

## Security boundaries

- Probes remain public; functional staging routes require the environment token.
- Admin routes require bearer authentication and are not registered as public
  production functionality.
- Invalid signed-client input falls back to an untrusted ephemeral identity.
- Raw IPs, raw visitor/conversation IDs, abuse keys, prompts, and secrets are not
  written to application or operational logs.
- DATA access is HTTP-only through `DATA_URL`; the brain never reads DATA tables.

## Promotion sequence

1. Install, lint, type-check, test, build, and start from this checkout alone.
2. Pass black-box OpenAPI, 64 KiB, auth, privacy, retention, and shutdown gates.
3. Pass the deterministic shuttle vertical slice and model-backed quality suite.
4. Deploy DATA and brain to preview; verify readiness and direct chat.
5. Point a preview UI at the candidate and run the existing-UI smoke.
6. Rehearse rollback.
7. Only then update production `BRAIN_URL`.
