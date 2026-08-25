# RockyGPT brain implementation rule

Keep it minimal. The whole request lifecycle is `core/brain.py`, and it should
stay readable in a minute.

```text
the question
  -> BRAIN #1  understand it, write a plan   (planner.py + validate.py)
  -> PYTHON    run the lane the plan names   (execute.py)
  -> BRAIN #2  write the answer              (model.py)
```

Four stages, in that order, and the trace carries one entry for each. The order
is the point: BRAIN #2 answers after the lane has run, because once a lane has
an executor its results are what there is to write about. Do not make the two
calls concurrent to save latency — it would make the trace describe a pipeline
that did not happen.

**Rocky's vocabulary describes what it can do, never what anyone may ask.**
Five lanes, a registry of capabilities, the fields each capability allows, and
a few generic operations — filter, sort, limit, count, compare. That is all of
it. There is no list of intents and there must never be one: no `next_shuttle`,
no `menu_lookup`, no enum whose members are questions. "The first shuttle" and
"the last shuttle" are one capability with a different sort, and a new question
should need no code at all. `plan.py` holds the vocabulary, `capabilities.py`
the registry.

**The trace is the four stages, not two halves.** `question`, `plan`,
`execution`, `answer` — one box each in the dev inspector, read top to bottom.
A stage that did nothing says so rather than going missing: an unexecuted lane
reports `ran: false`, so a turn answered from the model's own knowledge can
never be mistaken for one answered from campus data.

**Python decides what runs.** The model writes a plan; `validate.check` decides
whether Rocky acts on it. A plan naming a field the capability does not list is
rejected, not repaired — guessing what it meant is how a taxonomy starts.

Three rules that survived every earlier version, because each was a real bug:

**Anything deterministic belongs in Python.** Dates and times especially — the
model is told what time it is rather than working it out, which is how a Monday
once became a Sunday. Time words in a plan (`today`, `now`) are resolved by
`validate`, never by the model.

**Don't tell the model what it cannot do.** A prompt describing missing
capabilities makes the model apologise for them, which reads as a broken product
rather than a small one. Say what it should do and stop.

**No case-by-case behaviour.** No phrase, entity, or expected answer from any
test suite belongs in production code or prompts. The planner instruction in
particular contains no example question, and a test holds it to that — prose
added there to fix one question reliably breaks three others.

Preserve the existing `/v1` UI response shape.
