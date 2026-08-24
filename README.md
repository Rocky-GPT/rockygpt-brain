# RockyGPT Brain — Hybrid V1

This workspace contains the greenfield Python brain for RockyGPT. The public
Brain API is frozen by `spec/brain-api.openapi.yaml`; the internal architecture
is defined by `DESIGN.md` and `CONTRACT_MATRIX.md`.

## The invariant

```text
AI #1 UNDERSTAND
  -> Python dispatch: CODE | RAG | MEMORY | GENERAL | SAFETY
  -> typed JSON
  -> AI #2 COMMUNICATE
  -> Python verify, project, persist
```

AI #1 produces a schema-validated plan. Python, not the model, chooses and
executes allowlisted operations. AI #2 turns the resulting typed JSON into a
student-facing answer. Both are bounded OpenAI Responses API calls with
`store=false`; there is no autonomous tool loop.

The heart of the design is the CODE/RAG boundary:

- **CODE** handles objective, structured operations such as shuttle selection,
  hours status, event ordering, and directory lookup through typed DATA
  capabilities. DATA owns filtering, sorting, bounding, and completeness.
- **RAG** handles prose-heavy material such as policies and handbooks. Retrieved
  text is evidence, never executable instructions.

MEMORY answers conversation-truth questions and resolves genuine follow-ups.
GENERAL handles confirmed non-campus questions without borrowing campus
citations. SAFETY applies deterministic emergency and privacy boundaries.

## Request shape

One request follows a short path:

1. Bound, validate, authenticate, and create one immutable time context.
2. Run deterministic safety/privacy preflight.
3. Ask AI #1 for a typed `RoutePlan`.
4. Dispatch up to four CODE/RAG/MEMORY/GENERAL/SAFETY operations.
5. Assemble typed evidence JSON with completeness and server-owned sources.
6. Ask AI #2 to communicate from that JSON.
7. Verify claims and citations, then project the frozen API response.
8. Persist a successful turn, memory update, and durable evidence snapshot
   atomically before returning.

Accepted failed turns receive privacy-safe operational records but never update
conversation memory. Request bodies are capped at 64 KiB.

## Persistence and privacy

Raw visitor and conversation IDs are never stored; independent keyed HMACs
produce durable pseudonyms. Stored conversation text is redacted and expires
within 30 days. Redacted feedback and non-text operational metadata expire
within 90 days. Evidence snapshots retain the source/version context needed to
preserve provenance across follow-ups after request-local evidence IDs expire.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
uvicorn rockygpt_brain.main:app --reload
```

Configure an explicit OpenAI model and API key, brain-owned PostgreSQL,
`DATA_URL`, and hashing secrets before expecting readiness. See
`DEPLOYMENT.md` for the environment contract and promotion sequence.

No deployment switches UI traffic automatically. `rockygpt-ui` must keep its
current `BRAIN_URL` until the acceptance gates, preview smoke, and rollback
rehearsal pass.

