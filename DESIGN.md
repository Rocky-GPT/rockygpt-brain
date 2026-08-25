# Design

```text
question
   │
 AI #1 ── understands the request
   │
 Python ── runs a lane      (only GENERAL exists so far)
   │
 result JSON
   │
 AI #2 ── writes the answer
```

## Modules

```text
core/intent.py   what AI #1 returns
core/brain.py    the request lifecycle
core/model.py    the two AI calls
services/memory.py  turns and the admin log
api/            HTTP surface
```

## Lanes

| Lane | State |
| --- | --- |
| GENERAL | built |
| CODE | not built — structured campus facts |
| RAG | not built — policies and documents |
| SAFETY | not built — emergencies, privacy, secrets, unsupported actions |
| MEMORY | not built — what was said earlier |

Adding one back is a variant on `Decision` in `intent.py` and a branch in
`Brain._run`. Nothing else in the pipeline changes.

## What this means today

The brain makes no DATA calls, so it can answer nothing about the college. AI #2
is told to say so rather than guess at hours, menus, shuttles, staff or policies.

There is no SAFETY lane, so an emergency question is answered by the model's
general knowledge with no fixed wording. It currently does say to call 911, but
that is the model's choice on the day, not a guarantee.

Earlier architectures are on the `backup/*` branches.
