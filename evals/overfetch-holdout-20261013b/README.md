# Field-selection holdout, 2026-10-13 b (blind, frozen)

Question: the same as `evals/overfetch`. Does the Brain look up the right office and fetch what the
student asked for?

**Why this set exists.** `evals/overfetch`, `overfetch-holdout-20261012` and
`overfetch-holdout-20261013` were all run and their misses read. After the third, the Brain was
changed in three ways (brain `2279250` to `0b875e3`): the model is shown the published offices
(`published_offices`), a field that plainly helps may be read beside the ones asked for, and a
question that could mean several offices is asked back with their names. This set was written
after those by writers who never saw the Brain, its prompt, its code, its output, the earlier
cases or the misses.

**How the cases were made (2026-10-13).** Three writers saw only the published offices with their
aliases and the field definitions, and wrote 24 cases each (terse, full sentences, follow-ups). Two
other labelers, who never saw the writer's label, marked the smallest set of fields a good answer
must contain. A case is scored only when the writer and both labelers agree: 70 of 72.
`cases.sha256` is the hash of `cases.json`; a test fails if the file changes after the freeze.

**What is scored.** The runner's verdicts, read the way Dan said on 2026-10-13 ("yes it is ok" to
extra fields, because a time is more useful with its place): `ok` and `OVER` are both fine; a miss
is `NO LOOKUP`, `UNDER`, `BOTH`, or a lookup of an office other than the case's `office`.

**Pass bar, set before the run.** At most 5% of the scored cases are misses (3 of 70), and no
lookup is of a wrong office.

**Run.**

    python3 evals/overfetch/run.py <port> out.json "" evals/overfetch-holdout-20261013b/cases.json

About 72 model calls, each writing a row to the dev spending ledger. Nothing is changed after a
run: a miss is reported with what the Brain did, and the thing to look for is a repeated general
problem, not a sentence to patch.
