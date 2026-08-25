# Design

```text
IN    the question, plus the current time
        │
      one model call
        │
OUT   the answer
```

No router, no lanes, no lookups, no filters. Every question is answered the same
way, by the model, from its own knowledge.

## Modules

```text
core/brain.py       the request lifecycle — IN, the call, OUT
core/model.py       the model call and its one instruction
services/memory.py  turns kept for follow-ups, and the admin log
api/                the HTTP surface
config.py           settings from .env
```

## What Python contributes

The current date and time, in the campus timezone, because the model does not
know it. That is the only thing.

## Where it grows

A second way of answering — looking something up — goes in `Brain.answer`,
between building IN and returning OUT. That is also the point at which a router
starts to be worth its own model call; with one way of answering there is nothing
to route between.

Earlier architectures are on the `backup/*` branches.
