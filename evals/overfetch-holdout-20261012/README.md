# Field-selection holdout, 2026-10-12 (blind, frozen, not yet run)

Question: the same as `evals/overfetch`. When a student asks for one thing about an office, does the
Brain fetch only that thing, and when they ask for more, does it still fetch all of it?

**Why this set exists.** The 69 scored cases in `evals/overfetch` were run on 2026-10-12 and every
miss was then read and fixed, so they are a regression check now, not a measure. This set was
written after those fixes by writers who never saw the Brain, its prompt, its code, its output, the
earlier cases or the misses.

**How the cases were made (2026-10-12).** Three writers saw only the list of published offices with
their aliases and the field definitions, and wrote 24 cases each from three angles: terse
one-detail messages, full sentences with context (including general "how do I get in touch"
requests), and multi-turn follow-ups with slang. Each writer also gave the one office and the
smallest set of fields a good answer must contain. Two other labelers, who never saw the writer's
label, marked that set for every case. A case is scored only when the writer and both labelers agree:
71 of 72 are scored (`multiturn-13` is disputed: one labeler also wanted `contact_note`). The case
files carry `disputed` for it; it is run and reported, never scored. Two questions repeat an earlier
case's text ("registrar email", "and the phone?") because both are what a student would type.
`cases.sha256` is the hash of `cases.json`; a test fails if the file changes after the freeze.

**What is scored.** The same as `evals/overfetch/README.md`: the fields in the packet's
`request.fields` against the agreed `needs`, as `ok`, `OVER`, `UNDER`, `BOTH` or `NO LOOKUP`. A general
request to contact an office (9 scored cases) needs `email` and `phones`; `offices` is also allowed
there.

**Pass bar, set before any run.** On the scored cases: at most 1 case that leaves out a needed field
(`UNDER` plus `BOTH`); at least 90% `ok`; at most 5% `NO LOOKUP` (3 of 71). There is no baseline run
of the old Brain on this set.

**Run.** Serve the release that includes the `Res Life` alias (the cases use it) with
`BRAIN_OUTPUT=json`, then

    python3 evals/overfetch/run.py <port> out.json "" evals/overfetch-holdout-20261012/cases.json

Each case is sent once. Nothing is changed after a run: a miss is reported with what the Brain
fetched, and the thing to look for is a repeated general problem, not a sentence to patch. It costs
about 72 model calls, each writing a row to the dev spending ledger, so it waits for Dan's `run`.
