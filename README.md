# RockyGPT Brain

The service behind RockyGPT, the Ramapo College student assistant.

The current rebuild implements a small complete answering path: one bounded
assistant chooses read-only canonical office lookups, and code renders the
published facts and citations. Code also handles account limitations, safety
help, missing context, provider failures, deadlines, and spending. Jev and the
classification-to-planner pipeline are retired from the active runtime.

## What this slice can do

- Answer canonical office contact and room questions, including normal follow-ups.
- Preserve supported public facts in requests that also ask for private accounts.
- Show conflicting published records, unknown fields, and ambiguous office names.
- Quote available conversation history with an explicit distinction from verified facts.
- Return useful limitations when a requested capability is not available.

The model cannot author arbitrary campus facts: code renders every office result
retrieved during the current turn, and the model can add only bounded nonfactual
response parts such as account limitations or clarification. There are no
private-account read or write tools. Dining, shuttles, events, policies, general advice,
and generated explanations
are not implemented by this first slice. This is not yet a broadly capable student
assistant or a production-quality certification.

## Run

```sh
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
# Supply trusted server settings from .env.example through the launch environment.
uvicorn rockygpt_brain.api.app:app --host 127.0.0.1 --port 8000
```

The service needs a published campus dataset and its canonical identity registry,
the environment-isolated accounting database, and the environment's OpenAI project.
Missing configuration or dependencies make `/readiness` fail. `/health` only
establishes that the process is running. Neither probe calls a paid model.

All model calls go through the same gateway. It reserves against the durable
environment allowance before sending, accounts for returned usage, and keeps a
reservation charged when delivery or usage is uncertain. No automated retries are
made. Operators must reconcile uncertain reservations; waiting for a new month
does not erase them.

The bundled [provider release](src/rockygpt_brain/provider-release.json) records the
model, pricing evidence, and expiry. Expired pricing blocks readiness and provider
calls. An override must provide the model, input rate, output rate, and expiry
together; changing only the model is rejected. There is no automatic deployed-price
monitor: CI checks the configuration contract, and operators must renew the release
before its expiry. The obsolete workflow reading a missing `prices.json` is removed.

## Check

```sh
ruff check .
mypy src tests scripts
pytest -q
python scripts/check_chat.py --out /tmp/chat-regression.json
```

The last command exercises `/v1/chat`, the real gateway, and the shared fact reader
with scripted provider responses and synthetic facts. It makes no network or paid
model calls. Its conversations are repeatable regressions, not blind evaluations.
Use a new output path per run so saved evidence is not overwritten.

The saved October 1 offline run passes 16/16 regression turns. A separate final
live smoke run handled three requests, including a follow-up and mixed account
question, but all had `status: unavailable` because the real source capture was
stale. Dated observations and the account limitation remained visible. Earlier
live failures are preserved. These checks establish integration behavior, not
broad student-answer quality; [verification](docs/verification.md) records the
outcomes, costs, and remaining limits.

Read the [current contract](docs/contract.md), [verification scope](docs/verification.md),
and [evaluation notes](evals/README.md). Earlier modules, runners, and reports remain
as [historical evidence](docs/historical/README.md); they do not describe the active
answering path.
