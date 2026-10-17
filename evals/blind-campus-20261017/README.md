# Fresh blind check 2026-10-17, after the 3e4765e rerun's fixes

Dan's goal (10-17): fix the repeated misses and make the Brain solid enough to start other areas of
knowledge. blind-campus-20261016 was run on f5f0384 and rerun on 3e4765e, and fixes were made from
those runs, so it is no longer blind. Same method as the checks before, nothing reused:

1. Four writer agents wrote 25 questions each from writer-brief.md alone (no code, data, logs or
   earlier sets), with personas no earlier set used: international students and English learners,
   some writing in Spanish, Hindi or Korean or Chinese (v1); commuters who drive or come by train
   (v2); visiting parents and newly hired staff (v3); stressed or rushed students with a problem (v4).
2. Exact repeats of any earlier eval question (2,416 compared, case and punctuation folded, any
   script) are dropped: none were. 100 questions are frozen in questions-frozen.json with the
   sha256 in questions-frozen.sha256, committed before any question is sent.
3. run_blind.py (unchanged) sends each question once, single-turn, on the Brain in
   release-under-test.txt, and keeps every full response.
4. make_batches.py splits the answers into four batches; two independent graders per batch follow
   grader-prompt.md (graph read from the Brain under test); merge.py takes the worst of the two
   verdicts. Nothing is changed after the run.

Pass bar (set before the run, the same as the checks before): no answer graded "wrong" with
severity "serious" by both graders, and correct + declined_ok at least 80% of the scored answers.
The share with asked_ok counted as good is reported beside it. For "solid" the run is also read for
repeated misses: a kind of miss on two or more questions is a general problem still open.

The person running the fixes saw these questions when freezing them; nothing in the Brain is changed
because of them before the run.
