# Design

```text
    the question
         │
         ▼
    BRAIN #1        understand it, and write a plan
         │          checked against the registry before it goes on
         ▼
    PYTHON          run the lane the plan names
         │          `shuttle` looks its trips up; the rest do not run yet
         ▼
    BRAIN #2        write the answer, from what the lane returned
```

Four stages, run in that order, with one trace entry each. BRAIN #2 comes last
because what the lane returned is what there is to write about: it is handed
those results as `campusData` and told to answer from them. A capability with
no executor records that it did not run, hands over nothing, and BRAIN #2
answers from its own knowledge instead.

## Modules

```text
core/brain.py         the request lifecycle — the four stages, in order
core/planner.py       BRAIN #1 — question in, plan out
core/plan.py          the vocabulary a plan is written in
core/capabilities.py  the registry — what Rocky can look up, and with which fields
core/validate.py      the check, and the clock, applied to a plan before it runs
core/execute.py       PYTHON — run the lane, and apply the generic operations
core/model.py         BRAIN #2 — the answer call and its one instruction
services/data.py      the data service, and the one outbound call that is not a model
services/memory.py    turns kept for follow-ups, and the admin log
api/                  the HTTP surface
config.py             settings from .env
```

## The plan

A plan is a lane, and whatever that lane needs.

```text
lane        CODE | RAG | GENERAL | SAFETY | MEMORY
capability  CODE: which lookup, from the registry
filters     CODE: field/value pairs, drawn from that capability's filter fields
operation   CODE: orderBy + direction, limit, count, compare
topic       RAG: what to find in the documents
query       MEMORY: what was said earlier
```

The vocabulary is fixed and small on purpose. It grows by capability — a new
kind of thing Rocky can look up — and never by question. A question Rocky has
not seen before should need no code, because the plan for it is the same
capability with different filters and a different sort.

Filters are a list of pairs rather than a map because a strict response schema
cannot describe an object with arbitrary keys. `Plan.filter_values` gives back
the map, and `Plan.summary` is the shape a human reads in the log.

## The trace

`brainTrace` carries one entry per stage: `question`, `plan`, `execution`,
`answer`. The dev inspector renders them as four boxes in that order, so
reading down the modal is reading the request.

`question` is what was asked and nothing else. The clock leads `plan`: it is
not part of the question — the browser never sends a time and the proxy would
drop one — it is what Python read the question against, and `today` means
nothing until an instant fixes it.

A stage that did nothing reports that it did nothing rather than being
omitted. `execution` on an unexecuted lane is `{lane, ran: false, note}` — the
distinction between "Rocky looked this up" and "the model knew it" is the one
a reader most needs, and a missing box would hide it.

## What Python contributes

The current date and time, in the campus timezone, because the model does not
know it — and so the resolution of every time word a plan carries.

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
