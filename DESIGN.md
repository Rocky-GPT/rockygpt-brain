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
         │          using one of the registry's implemented capabilities
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
it.

A test enforces this rather than leaving it to memory: any module that sends
`instructions=` must load them with `beside(__file__)`. A new stage that
inlines
its prompt as a Python string fails the suite. `_unresolved` in `brain.py`
holds the two tests.

## Modules

```text
prompt.py             reads a prompt.md, minus the notes above its rule
brain/                the four stages, in the order they run
  brain.py            the lifecycle — what calls what, and what fails the turn
  values.py           the constrained scalars every stage schema is built from
  understand/         BRAIN #1: run, prompt.md, schema, validate
  plan/               BRAIN #2: run, prompt.md, schema, validate
  execute/            PYTHON: run (safety, then the lane), schema
  write/              BRAIN #3: run, prompt.md, schema
lanes/                where an answer comes from
  code/               a capability lookup, then the plan's operation
  rag/                cited passages from the campus document index
  general/            what the model knows, or a web search
capabilities/         what Rocky can look up
  registry.py         the only such list, and an entry needs its code
  shuttle/            timetable filters and temporal ordering
  dining/             today's menu items and dietary facts
  events/             upcoming event search and chronological filtering
  hours/              campus and dining opening hours
  courses/            course catalog search
safety/               the concerns, what to do about each, and applying them
context/              the conversation, and the record of it
services/             the outbound calls
  openai.py           the one way this brain talks to a model
  data.py             the campus data service
  web/                client and prompt.md — a search is a model call too
api/                  the HTTP surface
config.py             settings from .env
```

Each stage directory holds the same four things, so the same question is always
asked in the same place: `run.py` makes the call, `prompt.py` is what the model
is told, `schema.py` is what comes back, `validate.py` decides whether it can
be used. A stage without one of those does not have the file — `execute` has no
`prompt.py` because it calls no model, and no `validate.py` because a plan is
checked before it runs, not after.

Every instruction sent to a model is a `prompt.md` — the three stages and the
web search alike — because they are prose and the highest-risk text here. A
paragraph added to the planning instruction has twice moved lane
routing on questions it was not about, and that change should diff as sentences
rather than as a quoted string — and a markdown file cannot quietly acquire an
f-string or a conditional, which is how a prompt starts behaving differently
from what the file appears to say.

Each file is the whole instruction and nothing else — no header, no notes, no
section stripped on the way out. What it reads as is byte for byte what the
model is sent, which is the same reason these are not Python: any rule for
subtracting part of a file is one more difference between what it says and what
it does. Notes for whoever edits one live in the docstring of the module that
loads it, where they cannot be sent by construction.

A directory exists only when there is code for it. The RAG lane and the five
current CODE capability directories therefore correspond to implementations,
not roadmap placeholders. This is exactly the failure the registry rule below
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

A new thing Rocky can look up gets a capability directory containing its
executor and boundary normalization, a required entry in `capabilities/registry.py`,
and one method on `DataPort`. The registry cannot advertise a capability
without executable code.

Every executor translates the plan's published filter names into the data
service's request, then the CODE lane applies `orderBy`, `limit`, and `count`
over the records that came back. `shuttle` is the clearest example: the data
service has its own selection vocabulary — `first`, `next`, `current` — while
the executor asks for the full bounded set and leaves selection to the generic
operation. That keeps service-specific verbs out of a plan.

Earlier architectures are on the `backup/*` branches.
