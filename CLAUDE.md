# RockyGPT brain implementation rule

Keep this brain small. The whole request lifecycle lives in
`core/brain.py` and should stay readable in one sitting.

```text
question
  -> AI #1 understands the request
  -> Python runs a lane          (GENERAL only, for now)
  -> the lane returns a small JSON result
  -> AI #2 writes the answer from that result
```

Do not add planners, compilers, agent loops, repair passes, outcome hierarchies,
precedence lattices, evidence registries, claim ledgers, or an orchestration
framework. If a change adds a layer between the lane and the result, it is
probably the wrong change.

Only the GENERAL lane exists right now. Adding a lane is a variant on `Decision`
in `intent.py` and a branch in `Brain._run` — if it needs anything more than
that, it is the wrong shape.

Three rules that survive any refactor, because each one was a real bug:

**AI #2 reports, it does not compute.** It says what came back. If it starts
picking a record out of a set, or working out a date, the fix belongs in Python,
not in the prompt.

**An empty result is not a fact about the world.** `nothing` and `cannot` are
different words with different meanings, and AI #2 is told what each one means.
Not knowing whether somewhere is busy is not the same as it being empty.

**A refusal is never explained as missing data.** When SAFETY comes back, its
wording belongs in code. Saying "no records found" implies Rocky would hand it
over if it had them.

No case-by-case behaviour. No phrase, entity, or expected answer from any test
suite belongs in production code or prompts.

The brain reads campus information only through DATA HTTP APIs, and preserves the
existing `/v1` UI response shape.
