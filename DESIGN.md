# Design

```text
    the question
         │
         ▼
    BRAIN #1        understand it — what is it actually asking?
         │          the only stage that is shown the conversation
         ▼
    BRAIN #2        plan it — what should be done about that?
         │          given the resolved question and nothing else, and
         │          checked against the registry before it goes on
         ▼
    PYTHON          run the lane the plan names
         │          GENERAL answers from what the model knows, or from the
         │          web when the answer has a shelf life; CODE looks it up,
         │          and `shuttle` is the only capability with an executor
         ▼
    BRAIN #3        translate what came back into an answer
```

Three brains and a lane, run in that order, each turning the one before it into
something else: words into an understanding, an understanding into a plan, a
plan into rows, rows into prose.

BRAIN #3 comes last because it writes from what PYTHON produced, on every lane:
it is handed `answerFrom` — `campusData` with the rows a lookup returned, or
`ownKnowledge` — and never has to infer what to do from a field that is not
there.

The trace and BRAIN #3 are told different things on purpose. The trace says
exactly why a lane did not run, because that is for a person debugging. BRAIN
#3 is told only where to answer from, because a model told its lookup failed
apologises for the capability rather than answering the question.

A resolution is checked before it is planned from. BRAIN #2 sees `resolved`
and nothing else, which holds only while `resolved` really stands on its own —
so a reading that says it used the conversation and then shows no sign of it
ends the turn at that seam, rather than after three more stages have built on
it. `_unresolved` in `brain.py` holds the two tests.

## Modules

```text
brain/                the four stages, in the order they run
  brain.py            the lifecycle — what calls what, and what fails the turn
  values.py           the constrained scalars every stage schema is built from
  prompt.py           reads a stage's prompt.md, minus its notes
  understand/         BRAIN #1: run, prompt.md, schema, validate
  plan/               BRAIN #2: run, prompt.md, schema, validate
  execute/            PYTHON: run (safety, then the lane), schema
  write/              BRAIN #3: run, prompt.md, schema
lanes/                where an answer comes from
  code/               a capability lookup, then the plan's operation
  general/            what the model knows, or a web search
capabilities/         what Rocky can look up
  registry.py         the only such list, and an entry needs its code
  shuttle/            execute and normalize
safety/               the concerns, what to do about each, and applying them
context/              the conversation, and the record of it
services/             the outbound calls: openai, data, web
api/                  the HTTP surface
config.py             settings from .env
```

Each stage directory holds the same four things, so the same question is always
asked in the same place: `run.py` makes the call, `prompt.py` is what the model
is told, `schema.py` is what comes back, `validate.py` decides whether it can
be used. A stage without one of those does not have the file — `execute` has no
`prompt.py` because it calls no model, and no `validate.py` because a plan is
checked before it runs, not after.

Prompts are `prompt.md`, not Python, because they are prose and the highest-risk
text here. A paragraph added to the planning instruction has twice moved lane
routing on questions it was not about, and that change should diff as sentences
rather than as a quoted string — and a markdown file cannot quietly acquire an
f-string or a conditional, which is how a prompt starts behaving differently
from what the file appears to say.

Each one is two halves split by a `---` rule. Above it is for whoever edits the
file: what the instruction is for and what it has broken before. Below is what
the model is sent. Keeping the rationale in the same file is what makes it
likely to be read; keeping it above the rule is what stops the model reading it
too, since a model told about a past routing bug will try to be helpful about
it.

A directory exists when there is code for it. There is no `lanes/rag/` and no
`capabilities/dining/`, because an empty package claims the product does
something it does not — which is exactly the failure the registry rule below
exists to prevent.

## The plan

A plan is a lane, and whatever that lane needs.

```text
safety      what is wrong with the question, and empty when nothing is:
            emergency | privacy | secret | harmful
lane        CODE | RAG | GENERAL
capability  CODE: which lookup, from the registry
filters     CODE: field/value pairs, drawn from that capability's filter fields
operation   CODE: orderBy + direction, limit, count, compare
topic       RAG: what to find in the documents
freshness   GENERAL: `stable` answers from what the model knows, `current`
            searches the web
query       GENERAL: what a `current` question means to search for, in
            words, with no date in it
effectiveQuery
            what Python actually searched: `query` plus today's date. Set by
            `validate`, never by the planner, and never on the response schema
```

Two things that look like lanes are not, because a lane says where an answer
lives and neither of them is a place.

There is no MEMORY lane. A question about the conversation is answered from the
conversation, and routing to such a lane would have meant choosing it in BRAIN
#2, the one stage denied sight of the conversation. It is `usesContext` on
BRAIN #1 instead.

There is no SAFETY lane either. It was one, and it had no executor, so the one
turn that must never fail was the only one guaranteed to — a question routed
there came back as "Rocky cannot look that up yet." It is `safety` on the plan
instead: a list, because a question can be more than one of these at once, and
Python acts on every entry before any lane runs. What it does about each is
`CONCERNS` in `execute.py`, written in Python because that is the part that
must not vary with how the question was phrased.

The vocabulary is fixed and small on purpose. It grows by capability — a new
kind of thing Rocky can look up — and never by question. A question Rocky has
not seen before should need no code, because the plan for it is the same
capability with different filters and a different sort.

Filters are a list of pairs rather than a map because a strict response schema
cannot describe an object with arbitrary keys. `Plan.filter_values` gives back
the map, and `Plan.summary` is the shape a human reads in the log.

## The trace

`brainTrace` carries seven entries: `question`, `memory`, `understanding`,
`context`, `plan`, `execution`, `answer`.

`question` is what was asked and nothing else. Everything it was read against
is `memory` beside it — the clock, the earlier turns, the modes the client
asked for. The clock is there rather than in the question because the browser
never sends a time and the proxy would drop one, and because `today` means
nothing until an instant fixes it.

`understanding` is what BRAIN #1 made of the question; `context` is what it had
to borrow from the conversation to get there, and is empty unless BRAIN #1 says
the question needed it.

The dev inspector orders them for reading rather than for the wire: `context`,
BRAIN #1, BRAIN #2, PYTHON, then `memory` and the leftovers shut by default,
with `question` and `answer` as the header and footer because prose reads badly
as a one-line JSON string.

The `execution` stage leads with `answerFrom` — the same value BRAIN #3 was
handed, so the handoff is visible — and then takes one of three shapes, which
is what says what happened: `{"note": ...}` did not run, `{"count": n}` ran and
counted, `{"results": [...]}` ran and listed. It carries no lane — the plan
stage already names it — and no `ran` flag, because the shape is the flag.

The distinction that matters is `{"results": []}` against `{"note": ...}`:
"Rocky looked and there is nothing" against "Rocky never looked". Those are
different answers, and the empty list is what says so.

## What Python contributes

The current date and time, in the campus timezone, because the model does not
know it — and so the resolution of every time word a plan carries.

The date on every `current` search. BRAIN #2 writes what the search means;
Python appends the server clock's date, with no condition on it — and strips
any date the planner wrote anyway, so the query carries exactly one and it is
the server's. Left to the planner the date appeared four times in five, which
is the worst possible rate: often enough to look correct, rarely enough that
the turns it missed came back years stale and looked like nothing in
particular.

The citations, when the answer came off the open web. `title` is the URL host,
because the search returns no page title and the host is the part a reader
recognises anyway. Only the web lane produces them: campus rows are Rocky's own
records and carry no page to point at.

Then the decision about whether a plan runs at all. The registry is the whole
authority: a plan naming a capability, a filter, a sort field, or a comparison
field that is not listed is rejected with a reason, and the turn is answered
without it.

## Where it grows

Two places, and they are different.

A new thing Rocky can look up is an entry in `capabilities.py`. Nothing else
changes.

A capability earns an executor with an entry in `execute._EXECUTORS`, plus the
method on `DataPort` it calls. `shuttle` is the worked example: it translates
the plan's field names into the data service's request, and `orderBy`, `limit`
and `count` are then applied in Python over what came back. The data service
has its own selection vocabulary — `first`, `next`, `current` — and the
executor never uses it, asking for everything and narrowing it here. That is
what keeps those words out of a plan.

Earlier architectures are on the `backup/*` branches.
