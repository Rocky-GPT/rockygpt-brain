# Third fresh blind check 2026-10-17, on the final code of the 10-17 fixes

blind-campus-20261017b passed (88.4%, 0 serious); its minor misses were fixed in 52455ff, 28cfb52
and 4c43477. This set checks that code on questions nothing was tuned to. Same method:

1. Four writer agents wrote 25 questions each from writer-brief.md alone, with personas no earlier
   set used: freshmen in their first weeks (x1); nursing and science students on odd hours (x2);
   students looking for wellness, identity and support resources (x3); keyword-style, very short
   queries with slang and nicknames (x4). (x1-x3 first started before the brief was copied into
   this folder; x2 was resumed after it was, x1 and x3 finished after it was in place.)
2. Exact repeats of any earlier eval question (2,611 compared) are dropped: x1-05, x1-22. 98
   questions are frozen with their sha256, committed before any question is sent. The person
   running the fixes did not read the questions before the run.
3. run_blind.py sends each question once on the Brain in release-under-test.txt; two independent
   graders per batch; merge.py takes the worst of the two. Nothing is changed after the run.

Pass bar (set before the run): no answer graded "wrong"/"serious" by both graders, and correct +
declined_ok at least 80%; asked_ok share and repeated kinds of misses are reported beside it.
