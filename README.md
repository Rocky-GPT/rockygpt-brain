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
python3.13 -m venv .venv          # production's Python (Dockerfile)
source .venv/bin/activate
pip install --require-hashes -r requirements.lock   # production's exact package versions
pip install -e '.[dev,graph]'
python scripts/check_environment.py                 # confirms the match
# Supply trusted server settings from .env.example through the launch environment.
uvicorn rockygpt_brain.api.app:app --host 127.0.0.1 --port 8000
```

`requirements.lock` is the one list of package versions: the production image, CI and the
laptop all install it, so none of them picks up a newer library on its own. After changing
dependencies in `pyproject.toml`, regenerate it with the command at the top of the file.

The service needs a published campus dataset and its canonical identity registry,
the environment-isolated accounting database, and the environment's OpenAI project.
Missing configuration or dependencies make `/readiness` fail. `/health` only
establishes that the process is running. Neither probe calls a paid model.
Before it takes questions the server reads what the first questions would otherwise wait
for (the meaning index and the once-per-release lists), for at most two minutes; a step
that fails is logged as `brain_warm_failed` and read on first use instead. The log line
`brain_warm` gives each step's seconds. `BRAIN_WARM_AT_START=0` turns it off.
When the nightly data update makes a new release active, a running Brain reads that release's
meaning index before the next question goes on, rather than answering without it while it loads.

All model calls go through the same gateway. It reserves against the durable
environment allowance before sending, accounts for returned usage, and keeps a
reservation charged when delivery or usage is uncertain. No automated retries are
made. Operators must reconcile uncertain reservations; waiting for a new month
does not erase them.

The bundled [provider release](src/rockygpt_brain/provider-release.json) records the
model, pricing evidence, and expiry. Expired pricing blocks readiness and provider
calls. An override must provide the model, input rate, output rate, and expiry
together; changing only the model is rejected. The `Price renewal` workflow checks
OpenAI's model page every day (`scripts/check_provider_price.py`). When the published
price still matches and the expiry is 10 days away or closer, it opens a renewal pull
request into `main` (and the same branch into `dev`) that only moves the dates; merging
it renews production. A changed price, an unreadable page, or 5 days or fewer left opens
a `price-check` issue and fails the run. It never merges anything itself.

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

## Production releases

Promote `dev` to `main` through a reviewed pull request. The `Brain checks`
workflow tests the exact merge commit after a release PR is merged and records
`Release checks` on that commit. The test job has read-only repository access;
only the separate result-reporting job can write checks.

The existing Render service follows `main` with **After CI Checks Pass** enabled.
Its GitHub app must have access to `Rocky-GPT/rockygpt-brain`. A completed release
requires a Render automatic-deploy event for the merged SHA, a live deployment
of that SHA, and successful readiness and student-app checks. A passing GitHub
check or a healthy older deployment alone does not establish that the release
is live. If no automatic-deploy event appears, inspect the GitHub connection
and delivery path before treating the release as complete.

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
   opens a Postgres connection for campus data, and serves the release `active.json` names. When
   `active.json` names a newer release, the Brain opens and checks it, and reads it in full (meaning
   index, how-to sections, every list) in an engine of its own while the engine serving keeps
   answering; only then does it switch (`api/releases.py`). A question already being answered
   finishes on the release it began on. (The AI spending ledger is a separate database and is
   still used.)
3. **No fallback**: no published release, a damaged file, a pointer that disagrees with its file,
   or a failed read is an error for the question, `/readiness` fails, and the log says why. A
   graph that failed a read is opened afresh for the next question.

### One shared copy: production and the laptop serve the same file

Production and the laptop both run graph only, on the same bytes. The daily data run builds the
graph and meaning index once, then `python scripts/publish_graph.py --dir DIR` puts them in a
file store (an S3-style bucket, under `brain-graph/`), the pointer last, each file listed with its
size and sha256. `retrieval/graph_sync.py` downloads that copy into `BRAIN_GRAPH_ONLY_DIR`, checks
every sha256, and renames `active.json` into place last; the store and each Brain keep the
release before it too.

- **Production** sets `BRAIN_GRAPH_STORE_BUCKET`, `_ENDPOINT`, `_REGION`, `_ACCESS_KEY_ID` and
  `_SECRET_ACCESS_KEY` beside `BRAIN_GRAPH_ONLY_DIR`. It downloads before it takes questions
  (`/readiness` fails until a release is in), then checks the store every 5 minutes. A new
  release is downloaded and read in full before it is installed (`active.json` rewritten) and
  served; one that fails to open or pass its checks is never installed, so a restart does not
  serve it either, and it is not tried again until the store shares another.
- **The laptop** gives its Brain no store keys, so its copy changes only when synced by hand:
  `python -m rockygpt_brain.retrieval.graph_sync DIR [--force] [--events]` (the workspace's
  `./run-local.sh sync-prod` and the dev UI's "Sync from production"). Its running Brain looks
  at its folder every 5 seconds and moves to a newly synced copy the way production does: read
  in full first, then switched.

### The graph file

- **Small.** The engine runs with a 64 MB buffer pool, one thread and a 1 GB file limit. In a test
  against the development release the graph file was about 6 MB, built in about half a second,
  and the whole process peaked near 160 MB.
- **Visible.** `GET /v1/dev/runtime` reports `factsBackend` (what is serving now) and
  `factsGraph` (counters, the release served, the last error).
- **Safe to share.** Each build writes its own temporary file and removes every side-file the
  engine leaves.

The Brain never builds a graph itself. The one way it gets campus data is a published file
(above); without `BRAIN_GRAPH_ONLY_DIR` it reads Postgres directly, which is kept only as a
rollback.

### Meaning index on Postgres

A Brain reading Postgres finds programs, courses, people, events and how-to sections by meaning
only with the release's meaning index. The data repository's daily job (`meaning-index` in
rockygpt-data's Daily Data Update) builds the graph and the index from the active release and
stores the index beside it (`rockygpt_v2.release_meaning_indexes`, data migration 034) with
`scripts/publish_meaning_index.py`: 8-bit vectors with one scale each, about 26 MB for 47,000
vectors (on 400 written questions it kept 99.5% of the float index's ten best units). The Brain
loads it on its own thread for the active release and identity publication; until it has, and
when none is stored, questions are answered without it.

The same build gives every event and club card its kinds (`src/rockygpt_brain/kinds.py`: art and
crafts, cultural, sports, volunteering and 22 more). The chat model reads each card three times,
beside different listings each time, and the card keeps the kinds most of its reads give
(`LABEL_READS`). The picks are cached by the card's text in the job's cache (`kinds.json`), so a
day's build pays only for new listings. The model's rules (`RULES`: a club by its main purpose, a
room booking has no kind, and only the listing's own words count, so a name that says nothing
gets no kind), the number of reads and the list are versioned together, so changing any of them
gives every card its kinds again. A
question naming a kind ("any art events", "cultural clubs") lists every event or club of it and
of the narrower kinds it includes (international includes study abroad, academic includes study
help), in code's own words: a club directory entry named for its category ("Honor Societies")
stands for every club listed under it, and a club a ramapo.edu page marks inactive says so in the
list. A question naming a member of a kind ("painting") is still matched on the listings' words,
and the clubs its meaning finds keep the kind most of them share, so a club near only in its
words (the Balkan Club for "ballet") is left out.
An index built before kinds has none, and kind questions are then answered as before.
`build_meaning_index.py --kinds-only` gives an index already built its kinds (a laptop's copy of
production's, for testing).

### Answer cards

A page read lists how-to tasks one page at a time, so one task sits on many pages ("Apply for a
parking permit" is on eleven). Every index build ends by joining the same task across pages into
one card (`scripts/build_answer_cards.py`, which also runs alone on an index already built) and
writes the cards beside the release (`release-<hash>.cards.json`), where `publish_graph.py`
shares them like the index. An index never fails for its cards: a release that could not get
them goes out without them.

The join calls no model. Two tasks are one when the questions written for them mean the same
(`retrieval/card_join.py` has the rule and what it was measured on), the owner is the office the
pages name as handling it, and the winning page is never a past term while a current one is
listed, then the owner's own page, then the newest. What the rules cannot settle is flagged
(`needs_look`), and a card whose only date has passed says so (`date_passed`). The same graph
always gives the same cards; an index whose questions were written again gives slightly
different ones (about 1 card in 100 on 2026-11-11), because the questions are a model's words.
Nothing answers from a card yet: `GET /v1/dev/answer-cards` shows them to the dev UI's Answer
Cards page.

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

With `BRAIN_OUTPUT=written` the same packet is said by a writer. Since 2026-11-02 (Dan: plain code
sentences now, an AI layer later if needed) that writer is code: `src/rockygpt_brain/plain_writer.py`
says the shapes it knows (a day's hours and whether it is open now, a week, phones, emails, rooms,
contact notes, a menu, what is not published or unknown) in plain sentences with their source
marks, and leaves anything else (a conflict, an ambiguity, the shuttle, a menu filter) to the
code-written evidence text. With `BRAIN_LOOKUP_WRITER=ai` (`template` is the default; `1` still
means `ai`) the writer is a model
(`src/rockygpt_brain/writer.py`):
one model call that is handed only `writerInput` (no tools, no conversation, not even the
question) and returns text. A verifier then holds the text to the view: every email and web
address must be in it, every phone number must be one it holds (in any punctuation), and every
other number must be a number it holds. A reply that fails, a writer that errors and a packet
with nothing to say (a question back, an emergency, fixed wording) leave the code-written text as
the answer; `metrics.writer` says `used` or why not. The verifier is the floor, not the whole:
what a number check cannot see (a wrong day, a reversed meaning) is audited by reading the
answers against their views (a 71-answer audit on 2026-10-13 found no invented fact).

Page answers (a policy or how-to from the pages) are written the same two ways, by
`BRAIN_PAGE_WRITER` (Dan's two-stage plan, 2026-11-04; `src/rockygpt_brain/page_facts.py`). With
`template` or `ai`, stage 1 numbers the sentences of the sections code read, a sentence pick
made `BRAIN_PICKS` times at once returns only sentence numbers (the ones most picks choose are
kept), and code adds the sentences of a kind the question asks for (deadlines, amounts, steps,
contacts) next to them. The facts go out with the answer as `pageFacts`, with `writer` and
`fell_back`. Stage 2 is the template (the pages' own words, by office, with their marks) or a
model that sees only that JSON; code checks the model's reply says every fact's numbers,
emails and links and nothing no fact holds, gives it one repair try, then sends the template's
text instead. `one_call` (the default until the check set shows the two stages better) is the
one model call that chooses the facts while it writes. A request's `writer` field (`template`
or `ai`; `rocky ask --writer`) overrides both settings for that turn.

A writer is never handed the full packet. The packet is the Brain's record (ids, hashes, the pages
that were read); a template or a model with no other context only needs what it can say.
`writer_view(packet)` (`src/rockygpt_brain/writer_view.py`) derives that: values, which are not
current or conflict, what is unknown and what is confirmed not published, what to ask, the fixed
wording to repeat, and numbered sources to cite. It drops the ids, the dataset and its hash, and
leaves out whatever holds the usual value (`status` "known", `current` true, `freshness` "fresh",
empty lists), so a missing key means the usual. Inside a schedule, the proof that a weekday or a
validity date is not published (`absence`, `validity_absence`) is cut to when it was checked and which
pages were read; the scope, reason sentence, section and page hash stay in the packet. It adds and
infers nothing, and a packet field it does not know is carried through. In JSON output the body
carries it as `writerInput` beside `facts` (60-90% smaller; a seasonal office such as the Testing
Center goes from about 7 KB to under 2 KB). A test fails if a packet value is neither in the view nor on the list of
what is dropped on purpose.

Packet version `"1.0"` (`src/rockygpt_brain/fact_packet.py`):

| key | what it holds |
|---|---|
| `request` | `intent` (`contact`, `hours`, `contact_and_hours`, `office_facts`, `office_lookup`, `safety`, `recall`, `clock`...), `entities [{id, name, kind, query}]`, `fields`, `asOf` (the campus time every freshness check used), `dataset {version, identityHash}` |
| `status` | `complete`, `partial` (something missing, stale, conflicting or unanswerable), `insufficient`, `ambiguous` (the Brain needs a follow-up answered: an office name matched several offices, or the message named no office), `not_found`, `emergency`, `no_facts_needed` |
| `facts` | `[{id, subject {id, name, kind}, predicate, value, status, current, source_ids}]`. `value` is exactly what the shared reader returned; `status` is `known`, `conflicting` or `multiple` (conflicts are listed side by side, none is chosen); `current` is false when no source is fresh and inside its published validity. Campus numbers sent for an emergency carry `purpose: "emergency_contact"`; an office's contact note read because a requested email or phone is not published carries `purpose: "also_published"`. Neither is a requested field or changes `status` |
| `derived_facts` | what the Brain works out so a writer never computes: the hours of the one weekday the student asked about (`hours_on`: `day`, `date`, `value`, `applies`, `current`, `from`, `source_ids`). The model sets `day` (today, tomorrow or a weekday) on an hours lookup; code turns it into a campus date, reads that weekday from each schedule valid on it (`src/rockygpt_brain/hours_on.py`), and `applies` is false when the date falls outside the schedule's published window. A seasonal schedule whose page publishes no complete date range gets `applicability: "unverified"` and `applicability_reason` instead, with `hours: null` and `listed_hours` (what the schedule lists for that weekday, to be said as listed and unconfirmed for the date): the Brain does not guess which dates a season covers. The writer input carries the day and leaves out the full-week fact it came from. Time of day ("open at 8 pm", "open now") is not worked out yet. |
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
