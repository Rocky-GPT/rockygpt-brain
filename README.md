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

Postgres is where the campus data is published. A release can also be published as a read-only
graph-database file (LadybugDB, `retrieval/graph_store.py`), joined by edges, which the shared
reader reads exactly as it reads Postgres: the graph hands over the same entities and the same
linked evidence rows, and the reader still resolves every value, conflict, unknown and date
boundary. Install the extra first: `pip install -e '.[graph]'`. There are two ways to use it.

### Graph only: the Brain never connects to the campus database

1. **Publish** (the one step that reads the campus database; run it wherever it can be reached,
   after every data release): `python scripts/build_graph.py --out DIR [--dbname NAME]`. It builds
   the active release into one file, then writes `DIR/active.json` (last, renamed into place), and
   keeps the newest three releases.
2. **Serve**: start the Brain with `BRAIN_GRAPH_ONLY_DIR=DIR`. It needs no `DATABASE_URL`, never
   opens a Postgres connection for campus data, and serves whatever release `active.json` names. A
   newly published release is served from the next question on. (The AI spending ledger is a
   separate database and is still used.)
3. **No fallback**: no published release, a damaged file, a pointer that disagrees with its file,
   or a failed read is an error for the question, `/readiness` fails, and the log says why. A
   graph that failed a read is opened afresh for the next question.

### Self-building copy: the Brain follows Postgres

With `BRAIN_GRAPH_DIR=DIR` the Brain keeps a copy of the active release itself, reading Postgres to
learn the release and to build it.

- **Built in the background.** The first question after a new release (or a wake-up) is answered
  straight from Postgres while one background thread builds the graph, so no student waits for
  it. The build reads the whole release in one transaction with its own 60-second limit.
- **Never costs an answer.** If the graph cannot be built, opened or read, questions are answered
  from Postgres and the graph is put aside for 60 seconds. A missing LadybugDB install is logged
  at startup and the Brain keeps using Postgres.
- **Follows the data.** Each question first asks Postgres which release is active (a cheap
  probe); every 30 seconds the graph is also checked against a digest of the release's evidence
  rows, so an in-place edit shows up within about 30 seconds.

### Both modes

- **Small.** The engine runs with a 64 MB buffer pool, one thread and a 1 GB file limit. In a test
  against the development release the graph file was about 6 MB, built in about half a second,
  and the whole process peaked near 160 MB.
- **Visible.** `GET /v1/dev/runtime` reports `factsBackend` (what is serving now) and
  `factsGraph` (counters, the release served, the last error).
- **Safe to share.** Each build writes its own temporary file and removes every side-file the
  engine leaves; an unfinished build file is only deleted once it is an hour old.

On Render's free tier the disk is erased on every sleep, so a graph-only Brain there needs the
published directory delivered to it (nothing does that yet); the self-building copy simply
rebuilds after each wake.

## Fact Packet output (optional, off by default)

The design is two steps with a hard wall between them: the Brain understands the question,
resolves entities, reads the graph, applies dates and rules, and ends with a validated **Fact
Packet**; a writer (a template first, a model later) turns the packet into words and sees nothing
else. The writer has zero context, so the packet stands alone, and it may not discover, infer,
calculate or retrieve a fact. No factual answer reaches a writer until a packet has passed
`validate_packet`; if it fails, the turn is a `503 invalid_fact_packet` error, never a guess.

With `BRAIN_OUTPUT=json` a turn returns that packet and writes no answer text (no template and no
writing model yet). `answer` is `""`; `status`, `citations`, `datasetVersion` and `requestId` are
as in text mode, and the body gains `facts` (the packet). Errors are unchanged, and text mode stays
the default, so the student UI is unaffected. `GET /v1/dev/runtime` reports `output`.

A writer is never handed the full packet. The packet is the Brain's record (ids, hashes, the pages
that were read); a template or a model with no other context only needs what it can say.
`writer_view(packet)` (`src/rockygpt_brain/writer_view.py`) derives that: values, which are not
current or conflict, what is unknown and what is confirmed not published, what to ask, the fixed
wording to repeat, and numbered sources to cite. It drops the ids, the dataset and its hash, and
leaves out whatever holds the usual value (`status` "known", `current` true, `freshness` "fresh",
empty lists), so a missing key means the usual. It adds and infers nothing, and a packet field it
does not know is carried through. In JSON output the body carries it as `writerInput` beside `facts`
(about 60-70% smaller). A test fails if a packet value is neither in the view nor on the list of
what is dropped on purpose.

Packet version `"1.0"` (`src/rockygpt_brain/fact_packet.py`):

| key | what it holds |
|---|---|
| `request` | `intent` (`contact`, `hours`, `contact_and_hours`, `office_facts`, `office_lookup`, `safety`, `recall`, `clock`...), `entities [{id, name, kind, query}]`, `fields`, `asOf` (the campus time every freshness check used), `dataset {version, identityHash}` |
| `status` | `complete`, `partial` (something missing, stale, conflicting or unanswerable), `insufficient`, `ambiguous` (the Brain needs a follow-up answered: an office name matched several offices, or the message named no office), `not_found`, `emergency`, `no_facts_needed` |
| `facts` | `[{id, subject {id, name, kind}, predicate, value, status, current, source_ids}]`. `value` is exactly what the shared reader returned; `status` is `known`, `conflicting` or `multiple` (conflicts are listed side by side, none is chosen); `current` is false when no source is fresh and inside its published validity. Campus numbers sent for an emergency carry `purpose: "emergency_contact"` |
| `derived_facts` | reserved for values the Brain computes (for example "open at 8 pm"); empty for now |
| `missing` | `[{subject, predicate, reason}]`: a requested fact we hold no information about (reason `unknown`: nobody confirmed whether the office publishes it) |
| `not_published` | `[{subject, predicate, checked_at, current, checks [{url, section, checked_at}], source_ids}]`: a requested fact the office's own pages were read for and do not state. It is an answer with its proof, so it does not make the packet `partial` unless the check is no longer current |
| `ambiguities` | `[{query, candidates [{id, name, match}], truncated}]` |
| `unresolved` | `[{query, reason}]`: a name that matched no office |
| `notices` | replies that carry no facts, each with a `type`: `greeting`, `thanks`, `okay`, `about`, `unsupported`, `account_limit`, `clarification`, `clock` (`campusNow`), `recall` (the quoted message), `safety` (`situation`, `contacts`), `incomplete` (`code`: a provider failure cut the turn short; the facts found are kept), `emergency_reminder` (the standing reminder on a cut-short reply; not an emergency) |
| `sources` | `[{id, title, collection, urls, captured_at, freshness, validity, valid_from, valid_until, current, limitations}]`; only `https` links are listed |

A notice that stands for words the Brain used to write itself carries them as `approved_text`
(the emergency guidance, "I can't see your student record", the greeting). The writer states them
and never composes them; a `safety` notice without its `approved_text` is refused.

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
