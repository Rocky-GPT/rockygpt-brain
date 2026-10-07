# RockyGPT Brain

The service behind RockyGPT, the Ramapo College student assistant.

The current rebuild implements a small complete answering path: one bounded
assistant traverses Ramapo → Offices → office and reads its published records, and code
renders the published facts and citations. Code also handles account limitations, safety
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

## Release graph (optional, off by default)

Postgres stays the source of truth. With `BRAIN_GRAPH_DIR` set to a writable directory and the
`graph` extra installed (`pip install -e '.[graph]'`, which adds LadybugDB), the Brain keeps a
read-only graph-database copy of the active release (`retrieval/graph_store.py`). The graph hands
the shared reader the same entities and the same linked evidence rows the source returned, joined
by edges; the reader still resolves every value, conflict, unknown and date boundary.

- **Built in the background.** The first question after a new release (or a wake-up) is answered
  straight from Postgres while one background thread builds the graph, so no student waits for
  it. The build reads the whole release in one transaction with its own 60-second limit, writes
  one file, and renames it into place. Older releases' files are deleted.
- **Never costs an answer.** If the graph cannot be built, opened or read, questions are answered
  from Postgres, and the graph is put aside for 60 seconds before the next try. A missing
  LadybugDB install is logged at startup and the Brain keeps using Postgres.
- **Follows the data.** Each question first asks Postgres which release is active (a cheap
  probe). Every 30 seconds the graph is also checked against a digest of the release's evidence
  rows, so an in-place edit of the active release shows up within about 30 seconds.
- **Small.** The engine runs with a 64 MB buffer pool, one thread and a 1 GB file limit. In a test
  against the development release the graph file was about 6 MB, built in about half a second,
  and the whole process peaked near 160 MB.
- **Visible.** `GET /v1/dev/runtime` reports `factsBackend` (what is serving now) and
  `factsGraph` (questions served by the graph and by Postgres, builds, last build time, last
  error, seconds until the next retry).

On Render's free tier the disk is erased on every sleep, so the file is rebuilt after each wake.
Several processes may share one directory: each build writes its own temporary file, and an
unfinished build file is only deleted once it is an hour old.

## Root-first traversal

Each chat turn receives the Ramapo navigation root. One `graph_lookup` request
supplies an office name, nickname or service query and the requested fields. Code
walks Ramapo → Offices → matched office and reads its published records, without a model
call between nodes. Independent queries can be batched. A normal answer uses one model
call to request the traversal and another to finish; the existing four-call and
spending ceilings remain in place. Every query, including a follow-up, starts at
the root. Unknown or ambiguous matches stay explicit.

Root/category edges organize navigation; linked published records supply facts.
Only the office branch is implemented. All reads use the shared reader and the
turn's dataset/identity pins. Emergency contacts use the same path in code, require
an exact office name, and retain their separate time limit.

Development traces record full paths, pins, the lookup time, answer contributions
and citations. `GET /v1/dev/graph/node` lets the developer UI inspect a reached node
through the same root path, without model calls. Trace links carry the publication,
fields and lookup time; a publication change returns 409 instead of mixing releases.

Development diagnostics also partition request time into non-overlapping measured
steps, with a total header that includes response encoding. See [the timing
contract](docs/contract.md#developer-diagnostics).

The single-request traversal, graph inspector and request timing have not been tested or built at
the user's request. Earlier saved results do not verify these changes.
