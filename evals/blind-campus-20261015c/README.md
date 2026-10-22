# Fresh blind check 2026-10-15 (night), on dev after the structured-comparison fixes

Asked by Dan ("run the fresh blind holdout on the new code", then "freeze a new fresh set"):
blind-campus-20261015b had already been run once (16:48, 78%, two serious wrongs) and a fix was
made from its results (9bff422), so it is no longer blind. Same method as the two blind checks
before it, nothing reused:

1. Four writer agents wrote 25 questions each from writer-brief.md alone (no code, data, logs or
   earlier sets), with personas no earlier set used: transfer students (t1), student-athletes and
   club leaders (t2), students with a disability, health or wellness need (t3), high-school
   seniors and prospective students (t4).
2. Exact repeats of any earlier eval question (2,136 compared, case and punctuation folded) are
   dropped: t1-11 "When does the next shuttle leave" (evals/shuttle) and t4-04 "admissions
   email?" (evals/ambiguity). 98 questions are frozen in questions-frozen.json with the sha256 in
   questions-frozen.sha256, committed before any question is sent.
3. run_blind.py (unchanged from the earlier checks) sends each question once, single-turn, on the
   Brain in release-under-test.txt, and keeps every full response.
4. make_batches.py splits the answers into four batches; two independent graders per batch follow
   grader-prompt.md (the prompt the previous check used, graph read from :8014); merge.py takes the
   worst of the two verdicts. Disagreements are shown to Dan; nothing is changed after the run.

Pass bar (set before the run, same as the two checks before): no answer graded "wrong" with
severity "serious" by both graders, and correct + declined_ok at least 80% of the scored answers.
The share with asked_ok counted as good is reported beside it.
