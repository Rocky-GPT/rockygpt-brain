# RockyGPT brain implementation rule

Keep it minimal. The whole request lifecycle is `core/brain.py`, and it should
stay readable in a minute.

```text
the question
  -> BRAIN #1  understand it — what is it actually asking?   (planner.py)
  -> BRAIN #2  plan it — what should be done about that?     (planner.py)
  -> PYTHON    run the lane the plan names, or fail          (execute.py)
  -> BRAIN #3  translate what came back into an answer       (model.py)
```

**The planning call never sees the question as typed.** It is given the
resolved question and nothing else — no conversation, no original wording. Two
calls rather than one so that is a fact about what the model can read rather
than a line in an instruction it may or may not heed. A plan that would have
needed the conversation is a plan built on a resolution that failed, and this
is where that shows instead of being quietly patched over.

Four stages, in that order. The order is the point: what the lane returned is
handed to BRAIN #3 as `campusData` and is what it answers from. Do not make the
two calls concurrent to save latency — BRAIN #3 depends on what PYTHON
produced.

**Every lane grounds BRAIN #3.** PYTHON hands it `answerFrom` on every turn —
`campusData` with the rows, or `ownKnowledge` — so it never infers what to do
from a missing field. `answerFrom` is an instruction, never a status: a lane
with no executor is indistinguishable from a question that needed no lookup,
because told a lookup failed BRAIN #3 apologises for the capability instead of
answering the question.

**A lookup that did not happen must never look like one that did.** `campusData`
is present only when a lookup ran, so an empty list means it ran and matched
nothing — an answer in itself. The execution stage draws the same line for a
human: `{"answerFrom": "campusData", "results": []}` is "Rocky looked and there
is nothing", `{"answerFrom": "ownKnowledge", "note": ...}` is "Rocky never
looked". Do not drop `results` when it is empty — the empty list is the
message.

**Rocky's vocabulary describes what it can do, never what anyone may ask.**
Three lanes, four safety concerns, a registry of capabilities, the fields each
capability allows, and a few generic operations — filter, sort, limit, count,
compare. That is all of it. There is no list of intents and there must never be
one: no `next_shuttle`, no `menu_lookup`, no enum whose members are questions.
"The first shuttle" and "the last shuttle" are one
capability with a different sort, and a new question
should need no code at all. `plan.py` holds the vocabulary, `capabilities.py`
the registry.

Two things that look like lanes are not, and must not become lanes again. A
lane says where an answer lives; neither of these is a place.

Recalling what was already said. A question about the conversation is answered
from the conversation, and routing to a MEMORY lane meant deciding to route
there from BRAIN #2 — the one stage deliberately denied sight of the
conversation. It is `usesContext` on BRAIN #1 instead.

Something being wrong with the question. As a lane, SAFETY had no executor, so
the one turn that must never fail was the only one guaranteed to. It is
`Plan.safety` instead — a list of `emergency`, `privacy`, `secret`, `harmful`,
because a question can be several at once. **Python acts on every entry before
the lane runs, and a plan carrying one is never rejected**: what Rocky does
about a concern depends on no capability, no executor, and no network, so it
still happens when campus data is down. `CONCERNS` in `execute.py` says what
each one does, in Python, because that is the part that must not vary with
phrasing.

Four concerns, and they stay four. This is a list of things Rocky must handle,
not a taxonomy of things people ask; it grows only when Rocky learns to handle
something new.

**The trace is the whole pipeline, not two halves.** `question`, `memory`,
`understanding`, `context`, `plan`, `execution`, `answer` — one box each in
the dev inspector. `question` holds what was asked and nothing else;
everything the turn was read against — the clock, the earlier turns, the modes
the client asked for — is `memory` beside it, which leaves `understanding` and
`plan` as purely what the brains decided.
A stage that did nothing says so rather than going missing: `answerFrom` leads
the execution entry on every turn, so a turn answered from the model's own
knowledge can never be mistaken for one answered from campus data. The shape
that follows it is the flag; there is no `ran` field to read.

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
