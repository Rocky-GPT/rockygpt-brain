# RockyGPT brain implementation rule

Keep it minimal. The whole request lifecycle is `brain/brain.py`, and it should
stay readable in a minute.

```text
the question
  -> BRAIN #1  read it alone — what is it asking?      (brain/understand/)
  ->           fill what it pointed at, if it did      (brain/resolve/)
  -> BRAIN #2  plan it — what to do about that?        (brain/plan/)
  -> PYTHON    run the lane the plan names, or fail    (brain/execute/)
  -> BRAIN #3  turn what came back into an answer      (brain/write/)
```

**The first reading never sees the conversation.** It is handed the question
and the clock, and answers with the spans it cannot account for on its own.
Only when it names one does the conversation open, and then only to fill those
spans. Two calls rather than one for the same reason the planning call is
separate: a question that can be read alone cannot be coloured by an earlier
turn when there is no earlier turn to read. Asked about breakfast and dinner
after a turn about breakfast and lunch, the single call came back naming all
three, and the lookup answered a question nobody asked.

The rule the split enforces: **history resolves ambiguity and never creates
intent.** `understand/validate.py` holds one half — a reading that needs the
conversation must name what for, and one that names a gap may not also claim
to stand alone, because either way round there is nothing to act on.
`resolve/validate.py` holds the other — what the question stated survives, and
nothing an earlier turn named enters the question except as the filling of a
named span. The second call has no box of its own in the trace; what it
produced is `context`, beside the `understanding` the two compose into.

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

**An empty result is a fact about the search until nothing was narrowed.** A
lookup with no filters lists everything there is, so nothing coming back means
there are none — `foundNoneOf`, and "there are none left today" is the answer
worth having. A lookup with filters means nothing matched *those*, which is
`matchedNothing` and says far less. Under one name BRAIN #3 read both the first
way: `subject: "CS"` matched no courses because the catalogue files them under
`CMPS`, and the answer was "there are no computer science courses listed in the
current database" over sixty-three of them. **Python writes that sentence**, in
`nothing_matched`, for the same reason it writes `INSUFFICIENT_EVIDENCE` — told
the distinction in a prompt instead, the model denied the thing existed in five
answers out of six.

**Rocky's vocabulary describes what it can do, never what anyone may ask.**
Three lanes, four safety concerns, a registry of capabilities, the fields each
capability allows, and a few generic operations — filter, sort, limit, count,
compare. That is all of it. There is no list of intents and there must never be
one: no `next_shuttle`, no `menu_lookup`, no enum whose members are questions.
"The first shuttle" and "the last shuttle" are one
capability with a different sort, and a new question
should need no code at all. `brain/plan/schema.py` holds the vocabulary,
`capabilities/registry.py` the registry.

**The registry lists only what can run.** An entry requires its executor, so a
capability cannot be declared without the code behind it. The planner is shown
this list, so anything on it is something it may plan — and a plan Rocky
cannot run fails at execution, after the question was understood and a plan
was made, where nothing recovers. A declared-but-unbuilt capability is not a
smaller product, it is a broken one. Never add a second list beside this one.

**Capability executors take the filters, not the plan.** Nothing under
`capabilities/` imports `Plan`: a lookup has no business knowing what a lane
is or which operations exist. An entry also carries how to read and sort its
own records, so adding a capability never means editing the lane.

**A directory exists when there is code for it.** No `lanes/rag/`, no
`capabilities/dining/`. An empty package claims the product does something it
does not, and that claim is what the registry rule above exists to prevent.

**Each stage keeps the same four files** — `run.py`, `prompt.py`, `schema.py`,
`validate.py` — so the same question is always asked in the same place. A
stage missing one does not have the file rather than an empty one. **Every
instruction sent to a model lives in a `prompt.md`** — the three stages
and the web search alike, loaded with `beside(__file__)`. A paragraph added to
the planning instruction has twice moved lane routing on questions it was not
about, and that belongs in a diff you read as sentences; a markdown file also
cannot quietly acquire an f-string, which is how a prompt starts behaving
differently from what it appears to say. The file is the whole instruction
and nothing else: what it reads as is what is sent, with nothing stripped on
the way out.
Notes for whoever edits one go in DESIGN.md, where they cannot be sent by
construction — the source itself carries no commentary at all. A test enforces
the loading rule: inline an instruction as a Python string and the suite fails.

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

**A stage that failed is not planned around.** BRAIN #2 is shown `resolved`
alone, so it cannot tell a resolution that failed from a question that is
merely vague — it plans something plausible either way, and the turn returns a
confident answer to a question nobody asked. `_unresolved` ends the turn at
that seam instead. Both its tests read what BRAIN #1 said about its own work;
neither knows anything about the subject.

**Python decides what runs.** The model writes a plan; `validate.check` decides
whether Rocky acts on it. A plan naming a field the capability does not list is
rejected, not repaired — guessing what it meant is how a taxonomy starts.

**BRAIN #2 says what data is needed; Python says how much of it is shown.**
`limit` is what the question asked for, and a result cut to it is the answer.
How much of that result fits in one message is a different question with a
different answer, and `execute.present` decides it from the row count alone:
ten or fewer described one by one, fifty or fewer a line each, more than that
a page of twenty-five that says which page it is. BRAIN #3 is handed one page
and told how to lay it out. Left to judge the number itself it got it wrong in
both directions on the same data — writing "I cannot provide a complete list"
over a result that was complete, and elsewhere describing the first few and
stopping without saying it had. Neither is visible in the answer.

**Sorting is not ranking.** Not one field any capability can sort by is a
judgement — they are names, codes, dates, times, categories, credits, calories
— so the first row of a sorted result is the earliest or the smallest, never
the best. Arriving at BRAIN #3 the two are identical, which is how "what are
the best clubs at Ramapo" came back as the alphabetically first five, led by
`#WeAreRCNJ`. `Execution.ordering` is what says which, set only where a sort
actually ran. It cut the claim from eight answers in eleven to two in twenty;
what survives is a hedge, "notable" in place of "best", and three attempts to
close that measured no better or worse. Do not reach for prompt prose again
without a measurement — `sufficientEvidence` was `True` on every ranking
question, and a `ranked: false` field in the grounding made it slightly worse.

The planner is asked nothing about presentation, and the rule below about
case-by-case behaviour is why. A single field asking whether the question
wanted everything at once came back set on "when is the next shuttle" and
unset on "show me 100 courses", and the sentence describing it dropped a
question it had nothing to do with from 5/5 to 2/5. Measure the routing probe
before and after any edit to `plan/prompt.md`.

Three rules that survived every earlier version, because each was a real bug:

**Anything deterministic belongs in Python.** Dates and times especially — the
model is told what time it is rather than working it out, which is how a Monday
once became a Sunday. Time words in a plan (`today`, `now`) are resolved by
`validate`, never by the model. So is the date on a web search: BRAIN #2 writes
what the search means, `validate.anchor` adds the clock's date unconditionally
and removes any the planner wrote. Left to the model that date appeared four
times in five, and the missing fifth came back years stale in a way nothing
downstream could see.

**Don't tell the model what it cannot do.** A prompt describing missing
capabilities makes the model apologise for them, which reads as a broken
product
rather than a small one. Say what it should do and stop.

**No case-by-case behaviour.** No phrase, entity, or expected answer from any
test suite belongs in production code or prompts. The planner instruction in
particular contains no example question, and a test holds it to that — prose
added there to fix one question reliably breaks three others.

Preserve the existing `/v1` UI response shape.
