# RockyGPT brain — rewrite in progress

`src/rockygpt_brain/` holds an empty package and nothing else. That is
deliberate, not an unfinished checkout. The previous implementation was
deleted in full on this branch and the replacement is being built one step at
a time.

**Nothing about the architecture has been decided.** There are no stages, no
lanes, no capability registry, no HTTP surface, no chosen model client. If you
are about to add one because the code seems to be missing it, stop and ask.
The brief for this branch is to build deliberately and in order; filling in a
plausible skeleton ahead of that decision is the one way to get it wrong.

## Do not reconstruct the old implementation

The prior version is preserved at tag `v1.0.0-pre-rewrite`, and on branches
`dev` and `main`. It is there to be *read*, not copied:

```bash
git show v1.0.0-pre-rewrite:src/rockygpt_brain/brain/brain.py
git log v1.0.0-pre-rewrite
```

Read it to learn what already failed and why. Do not port its module layout,
its stage split, its schemas, or its prompts into this branch. If some piece
of it turns out to be genuinely right, it gets rebuilt as a decision made
here, with that reasoning written down — not restored because it existed.

## What is actually fixed

Two things constrain the rewrite from outside. Neither is architecture.

**The API contract.** `spec/brain-api.openapi.yaml` describes `/health`,
`/readiness`, `/v1/chat`, `/v1/feedback` and the three `/v1/admin/logs*`
endpoints. Two separate front ends consume it — the student UI and the dev
control room — and neither is being rewritten. Changing a response shape here
breaks a product that is running.

**One endpoint the spec does not document.** The monitoring workflow polls
`https://rockygpt-brain.onrender.com/readiness/chat-logs`, which appears
nowhere in the OpenAPI file. Rebuild the surface from the spec alone and this
one goes missing; once deployed the monitor fails and reports "production is
running code older than the readiness endpoint", which is not what went wrong.
It is part of the contract regardless of the spec being silent on it.

**The system boundaries.** `spec/system-boundaries.md` — the brain talks to
OpenAI and to Neon/PostgreSQL directly. There is no campus-data service to
proxy to; it was retired on 2026-08-28.

Both files are the contract the rewrite must still satisfy. They survived the
clear for that reason.

## Checks

```bash
source .venv/bin/activate && pytest && ruff check . && mypy src tests
```

All three pass on the empty skeleton. Keep it that way — a red baseline on a
branch this young hides whatever you add next.

**There is no CI.** `.github/workflows/chat-log-persistence.yml` is not a test
workflow; it polls the *live production* `/readiness/chat-logs` every 15
minutes and mails the repo owner when it fails. Nothing runs on push. That
local command above is the only gate before a Render deploy, so run it.

`pyproject.toml` carries no runtime dependencies. FastAPI, the OpenAI SDK and
asyncpg were the old version's choices and were removed with it; whatever this
version needs gets added when it is chosen, not before.

## Findings carried over from v1

These are not a design. They are things that were measured on this product and
cost real answers to discover, and they constrain *how* you build whatever
gets built, not *what* gets built.

**Anything deterministic belongs in Python.** Dates and times above all — the
model is told what time it is rather than working it out, because left to
derive it a Monday once became a Sunday. The same held for the date on a web
search: written by the model it was wrong or years stale often enough to
matter, and nothing downstream could see it.

**Never declare a capability with no code behind it.** A feature announced to
the model but unimplemented fails after the question was understood and a plan
was made, which is the point where nothing recovers. An empty package makes
the same false claim.

**No enum of intents, ever.** A vocabulary describes what the system can do —
never what a person may ask. The moment there is a `next_shuttle` or a
`menu_lookup`, every new question needs new code, and the list becomes a
taxonomy of phrasings nobody can keep complete.

**Don't tell the model what it cannot do.** A prompt that lists missing
capabilities produces apologies for them, and a small product reads as a
broken one. Say what to do and stop.

**No case-by-case behaviour.** No phrase, entity, or expected answer from any
evaluation suite belongs in production code or in a prompt. Prose added to fix
one question reliably broke three others; this was measured repeatedly, in
both directions.

**Sorting is not ranking.** No field worth sorting by is a judgement — names,
codes, dates, times, credits, calories. The first row of a sorted list is the
earliest or the smallest, never the best. Handed a sorted list with nothing
saying which, the model answered "the best clubs at Ramapo" with the
alphabetically first five.

**An empty result is two different facts.** Nothing found when nothing was
narrowed means there are none. Nothing found when filters were applied means
nothing matched *those* — far weaker. Under one name the model read both the
first way and denied that sixty-three courses existed, because the filter said
`CS` and the catalogue says `CMPS`. Whatever draws that distinction, Python
writes the sentence, not the model.

**Every instruction sent to a model lives in its own `.md` file, loaded
verbatim.** A prompt built by f-string starts behaving differently from how it
reads, and a prompt change should be reviewable as sentences in a diff.

**Measure prompt edits before and after.** Every instinct about prompt prose
recorded in the old version was wrong at least once, including the confident
ones. A change that looks obviously good has moved unrelated behaviour.

## Known stale

`README.md` still describes the deleted implementation — its package layout,
capabilities and run instructions are all wrong on this branch. Do not trust
it, and rewrite it when the shape of the new version is real enough to
describe.
