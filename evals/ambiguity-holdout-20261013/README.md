# Ambiguity holdout, 2026-10-13 (blind, frozen)

Question: the same as `evals/ambiguity`. When a message could mean more than one office, does the
Brain ask which instead of guessing, and when one office is clearly meant, does it answer with the
right office?

**Why this set exists.** `evals/ambiguity` read 35/56 on 2026-10-07, 44/56 once the model was shown
the published offices, and 54/56 after a clarification could name the offices it might be
(`056fae5`), all on cases whose misses had been read. This set was written after that change by
writers who never saw the Brain, its prompt, its code, its output, the earlier cases or the misses.

**How the cases were made (2026-10-13).** Three writers saw only the published offices with their
aliases and the definition (answer: one office is clearly meant; ask: it could equally be two or
more, or points at something the conversation does not settle), and wrote 24 cases each from
three angles: bare short messages, follow-ups after 2 to 6 messages that name one to three offices,
and single messages describing a need. Two other labelers, who never saw the writer's label,
labelled every case (answer plus the office, or ask). A case is scored only when the writer and
both labelers agree on the label and, for answer, the office: 66 of 72 (30 ask, 36 answer). The 6
others carry `disputed`, expected `soft`, and are run and reported, never scored. `bare-09` and
`followups-02` repeat an earlier case's text. `cases.sha256` is the hash of `cases.json`.

**Pass bar, set before the run.** The same as `evals/ambiguity`: overall at least 90%, the answer
group at least 90%, the ask group at least 85%.

**Run.**

    python3 evals/ambiguity/run.py <port> out.json evals/ambiguity-holdout-20261013/cases.json

About 72 model calls, each writing a row to the dev spending ledger. Nothing is changed after a
run.
