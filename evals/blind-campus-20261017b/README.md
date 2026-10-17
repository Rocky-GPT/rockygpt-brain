# Second fresh blind check 2026-10-17, after the 10-17 blind check's fixes

blind-campus-20261017 was run once on 9205515 (88%, 93% with fair questions back, one serious miss:
bar not met) and fixed in e949cb7, so it is no longer blind. Same method, nothing reused:

1. Four writer agents wrote 25 questions each from writer-brief.md alone (no code, data, logs or
   earlier sets), with personas no earlier set used: student employees, RAs and club leaders (w1);
   seniors close to graduating (w2); veterans and student parents (w3); residents during midterms
   and finals (w4).
2. Exact repeats of any earlier eval question (2,516 compared, case and punctuation folded, any
   script) are dropped: w1-05, w2-07, w2-11, w2-14, w2-19. 95 questions are frozen in
   questions-frozen.json with the sha256 in questions-frozen.sha256, committed before any question
   is sent. The person running the fixes did not read the questions before the run.
3. run_blind.py (unchanged) sends each question once on the Brain in release-under-test.txt.
4. Two independent graders per batch (grader-prompt.md, graph read from the Brain under test);
   merge.py takes the worst of the two. Nothing is changed after the run.

Pass bar (set before the run, the same as before): no answer graded "wrong" with severity "serious"
by both graders, and correct + declined_ok at least 80% of the scored answers; the share with
asked_ok is reported beside it, and repeated kinds of misses (two or more questions) are listed.
