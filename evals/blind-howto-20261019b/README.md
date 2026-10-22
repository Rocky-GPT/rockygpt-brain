# Second blind "how do I" and policy check 2026-10-19, after the fixes from the first

blind-howto-20261019 was run on brain dacccfb (42%, bar 70% not met) and fixes were made from its
misses (data 359d335/2f3bff0 site owners; brain 23fc3bf ranking, ccbfdd0 page addresses, 440218e
writer room and numbered steps), so it is no longer blind. Same method, nothing reused: four writer
agents wrote 25 questions each from writer-brief.md alone, with new personas (first-years and
sophomores k1; juniors and seniors k2; money, housing and health k3; club officers, student
employees, visitors, vendors and parents k4). Exact repeats of any earlier eval question are
dropped. The set is frozen with its sha256 and committed before any question is sent; the person
running the fixes did not read the questions before the run. Run once on the Brain in
release-under-test.txt, two graders per batch (grader-prompt.md), worst of two. Nothing changed after.

Pass bar (set before the run, the same as the first set): no answer graded "wrong"/"serious" by
both graders; correct + declined_ok at least 70%. Repeated kinds of misses are listed.
