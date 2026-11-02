# Blind how-to check zl 2026-11-02: production code vs the 11-02 fixes

Same method as zg-zk: four writer agents from writer-brief.md alone, 15 questions each, new
personas (sophomores with money and paperwork mid-semester zl1; new transfer and commuter students
zl2; residents with everyday problems mid-fall zl3; seniors and graduate students finishing zl4);
exact repeats of any earlier eval question dropped (1), 59 left; frozen and committed before any
run; the builder did not read the questions.

Run once on each of two Brains, graph-only on production's release v2-20261102040147, two at a
time: A = dev ff9bb57 (what production runs), B = this branch (bug fixes c9cfe6b + how-to
055747c). Two graders per batch, worst of two. Pass bar: correct + declined_ok + asked_ok at
least 90%, nothing wrong/serious by both graders.

## Result (graded 2026-11-02 ~05:55Z)

Both runs' 118 answers were mixed under hidden ids into four batches (each question's two answers
in two different batches), two graders per batch on the SQLite export of the same release
(grader-prompt.md, grading/q.py); worst of two; unmasked after all eight label files were in
(grading/merge_ab.py). Graders agreed on 102 of 118.

| run | correct | declined_ok | incomplete | declined_wrong | wrong | good | wrong-serious by both |
|---|---|---|---|---|---|---|---|
| A (production code) | 36 | 7 | 11 | 1 | 4 | 43/59 = 73% | 1 (guest rule) |
| B (this branch)     | 38 | 5 | 14 | 0 | 2 | 43/59 = 73% | 0 |

Per question B was better on 11 and worse on 5. Same share good, fewer wrong answers (4 to 2,
both of B's minor), no answer both graders call seriously wrong. Below the 90% bar either way.

What is left (B): most misses say "the pages don't say" something a page does say: the Fall 2026
withdrawal deadline (Nov 6) on student-accounts/deadlines, the $250 permit on costinfo, commuter
lots B1-B3 on parking-regulations, the 3-nights-in-7 guest rule on reslife/gtcl, the 1098-T
viewing steps on taxinfo. The section that answers it is not among the sections the one call
reads. That is the next root cause (which sections are picked), not wording.
