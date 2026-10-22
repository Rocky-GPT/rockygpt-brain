# Field-selection holdout, 2026-10-13 (blind, frozen)

Question: the same as `evals/overfetch`. When a student asks for one thing about an office, does the
Brain fetch only that thing, and when they ask for more, does it still fetch all of it?

**Why this set exists.** `evals/overfetch` (69 scored) and `evals/overfetch-holdout-20261012` (71
scored) were both run and their misses read, so they are regression checks now. After the second
one missed its bar (58 of 71), five changes were made to the prompt and the lookup (brain
`1e5e90b` to `338731d`). This set was written after them by writers who never saw the Brain, its
prompt, its code, its output, the earlier cases or the misses.

**How the cases were made (2026-10-13).** Three writers saw only the list of published offices with
their aliases and the field definitions, and wrote 24 cases each from three angles: terse messages
(typos and shortened words allowed), full sentences with context (some general "how do I get in
touch" requests, some asking how or when to approach an office), and multi-turn follow-ups with
slang. Each writer also gave the one office and the smallest set of fields a good answer must
contain. Two other labelers, who never saw the writer's label, marked that set for every case. A
case is scored only when the writer and both labelers agree: 69 of 72 are scored. The three
disputed cases (`natural-15`, `natural-18`, `multiturn-16`) carry `disputed`; they are run and
reported, never scored. `short-12` ("title ix contact") repeats an earlier case's text. The writers
asked for more variety than the first set (at least 18 offices, none more than twice), and the
shape is harder: abbreviations, typos and "how do I approach them" questions are common.
`cases.sha256` is the hash of `cases.json`; a test fails if the file changes after the freeze.

**What is scored.** The same as `evals/overfetch/README.md`: the fields in the packet's
`request.fields` against the agreed `needs`, as `ok`, `OVER`, `UNDER`, `BOTH` or `NO LOOKUP`. A general
request to contact an office (8 scored cases) needs `email` and `phones`; `offices` is also allowed
there.

**Pass bar, set before the run.** The same as the second set. On the scored cases: at most 1 case
that leaves out a needed field (`UNDER` plus `BOTH`); at least 90% `ok`; at most 5% `NO LOOKUP`
(3 of 69). There is no baseline run of an older Brain on this set.

**Run.**

    python3 evals/overfetch/run.py <port> out.json "" evals/overfetch-holdout-20261013/cases.json

Each case is sent once, about 72 model calls, each writing a row to the dev spending ledger.
Nothing is changed after a run: a miss is reported with what the Brain fetched, and the thing to
look for is a repeated general problem, not a sentence to patch.
