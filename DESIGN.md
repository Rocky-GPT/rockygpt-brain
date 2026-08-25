# Design

```text
IN    the question, the current time, and the plan
        │
        ├── AI #1 ──► a plan ──► checked against the registry ──┐
        │                                                       │
        └── the answer model ──► the prose ────────────────────┐ │
                                                               │ │
OUT   the answer, and whatever acting on the plan produced ────┘ │
      IN carries the plan ──────────────────────────────────────┘
```

Two model calls, made concurrently. AI #1 translates the question into
operations; the answer model writes the answer. Nothing executes a plan yet.

## Modules

```text
core/brain.py         the request lifecycle — IN, the two calls, OUT
core/planner.py       AI #1 — question in, plan out
core/plan.py          the vocabulary a plan is written in
core/capabilities.py  the registry — what Rocky can look up, and with which fields
core/validate.py      the check, and the clock, applied to a plan before it runs
core/model.py         the answer call and its one instruction
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

## Where the plan sits in the trace

`brainTrace.in` carries the question, the clock, and the plan. `brainTrace.out`
carries the answer. The plan is an input: it is what Rocky understood, and what
an executor will act on. When lanes grow executors, what they return joins the
answer in `out` — which is where the pre-simplification architecture put its
results too.

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

Executing a plan goes in `Brain.answer`, where `checked` is in hand and before
the answer is composed. Each lane earns its executor separately; today every
lane is still answered by the model, and the plan is recorded rather than run.

Earlier architectures are on the `backup/*` branches.
