# RockyGPT brain implementation rule

Keep it minimal. The whole request lifecycle is `core/brain.py`, and it should
stay readable in a minute.

```text
IN   the question + the current time
     -> one model call
OUT  the answer
```

There are no lanes, no lookups, no filters, and no campus knowledge. Every
question is answered by the model from its own knowledge. Do not add routing,
planners, compilers, agent loops, repair passes, or an orchestration framework.

Two rules that survived every earlier version, because each was a real bug:

**Anything deterministic belongs in Python.** Dates and times especially — the
model is told what time it is rather than working it out, which is how a Monday
once became a Sunday.

**Don't tell the model what it cannot do.** A prompt describing missing
capabilities makes the model apologise for them, which reads as a broken product
rather than a small one. Say what it should do and stop.

No case-by-case behaviour. No phrase, entity, or expected answer from any test
suite belongs in production code or prompts.

Preserve the existing `/v1` UI response shape.
