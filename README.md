# RockyGPT brain — clean-room Python workspace

This is an independent workspace for a from-scratch Python brain. It contains
requirements and external contracts only. It contains no previous brain source,
prompt, architecture, package manifest, generated client, or Git history.

## Implementation

A candidate Python implementation now lives under `src/rockygpt_brain/`
(FastAPI + `asyncpg` + the OpenAI SDK), built independently from `spec/`
per `CLAUDE.md`'s clean-room rule. Start here:

- **`DESIGN.md`** — architecture proposal: stack choices, the chat
  pipeline, and the anti-hallucination citation-provenance design.
- **`THREAT_MODEL.md`** — assets, trust boundaries, and mitigations.
- **`DEPLOYMENT.md`** — configuration, running locally/in a container,
  readiness semantics, and the promotion rule.
- **`ROLLBACK.md`** — the rollback procedure and rehearsal required before
  promotion.

Quick start:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
uvicorn rockygpt_brain.main:app --reload
```

See `DEPLOYMENT.md` for which environment variables each route needs.
Nothing here switches production traffic — see the promotion rule in
`spec/acceptance.md` and `DEPLOYMENT.md`.

## Start Claude Code in the clean room

From this directory:

```bash
./start-claude-cleanroom
```

Claude Code is installed on this machine, but it currently needs account
authentication. Before the first session, run:

```bash
/Users/danielrajakumar/.local/bin/claude auth login
```

Alternatively, provide `ANTHROPIC_API_KEY` in the shell that launches Claude.
Do not put that credential in this project's `.env`; sandboxed child commands
are configured not to receive it.

The launcher disables browser/MCP access, loads only project settings, enables
Claude Code's strict filesystem sandbox, blocks all sibling RockyGPT
repositories, and blocks GitHub network access. Start Claude through this
launcher rather than from the parent RockyGPT directory.

A mode-`600`, Git-ignored `.env` has already been created from `.env.example`.
Its secret values are blank; add credentials only when the new implementation
is ready to run, and never commit that file.

## What Claude receives

- `CLAUDE.md`: clean-room working rules.
- `spec/system-boundaries.md`: what the other applications own.
- `spec/brain-api.openapi.yaml`: the required consumer-facing brain API.
- `spec/data-api.openapi.yaml`: an exact snapshot of the campus-data API.
- `spec/data-service.md`: brain-oriented guidance for that data API.
- `spec/acceptance.md`: black-box completion gates.
- `.env.example`: local variable names with no secrets.
- `.env.staging.example`: staging variable names with no secrets.

## What is intentionally absent

- Existing brain source or tests.
- Existing prompts, tool definitions, database schema, and architecture.
- A Python framework or dependency choice.
- A preselected retrieval or agent design.
- A Git remote.

Claude should first propose its own architecture. No traffic is switched until
the new service passes the external contract, security, and answer-quality
gates.
