# Ambiguity evaluation (blind, frozen)

Question: when a message is ambiguous, does the Brain ask a follow-up instead of guessing, and when
one office is clearly meant, does it still answer?

**How the cases were made (2026-10-07).** Three writers who saw only the definition and the list of
published offices (not the prompt, the code or any earlier case) wrote 24 cases each from different
angles (bare messages, multi-turn follow-ups, single-message wording). Two other labelers, who never
saw the writer's label, labelled every case. A case is scored only when the writer and both labelers
agree (56 cases: 26 ask, 30 answer). The 16 others (15 disputed plus 1 the writer called soft) are
run and reported but never scored. `cases.sha256` is the hash of `cases.json`; a test fails if the
file changes after the freeze.

**Pass bar, set before any run.** On the scored cases: overall at least 90%, the "answer" group at
least 90% (asking too much is a failure too), the "ask" group at least 85%, and the overall score no
lower than the Brain before the change (8847bce, the commit before the ambiguity rules).

**Run.** Start the Brain with `BRAIN_OUTPUT=json`, then `python3 evals/ambiguity/run.py <port> out.json`.
Each case is sent once. Nothing is changed after a run: a miss is reported with what the Brain did,
and the thing to look for is a repeated general problem, not a sentence to patch.
