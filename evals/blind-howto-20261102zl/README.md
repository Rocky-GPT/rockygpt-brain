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
