# Fresh blind check 2026-10-16, on dev after the 10-16 fixes

Asked by Dan ("do a fresh blind test"). blind-campus-20261015c was run once (blind5) and fixes
were made from its results and reruns, so it is no longer blind. Same method as the checks before,
nothing reused:

1. Four writer agents wrote 25 questions each from writer-brief.md alone (no code, data, logs or
   earlier sets), with personas no earlier set used: first-generation students and their families
   who don't know college words (u1), graduate, adult and evening/online students (u2), alumni,
   neighbors and event guests (u3), students staying on campus over weekends and breaks, late at
   night (u4).
2. Exact repeats of any earlier eval question (2,134 compared, case and punctuation folded) are
   dropped: u1-24 "where is the shuttle right now" and u2-22 "Where is the shuttle right now?"
   (evals/shuttle). 98 questions are frozen in questions-frozen.json with the sha256 in
   questions-frozen.sha256, committed before any question is sent.
3. run_blind.py (unchanged) sends each question once, single-turn, on the Brain in
   release-under-test.txt, and keeps every full response.
4. make_batches.py splits the answers into four batches; two independent graders per batch follow
   grader-prompt.md (graph read from the Brain under test); merge.py takes the worst of the two
   verdicts. Nothing is changed after the run.

Pass bar (set before the run, same as the checks before): no answer graded "wrong" with severity
"serious" by both graders, and correct + declined_ok at least 80% of the scored answers. The share
with asked_ok counted as good is reported beside it.
