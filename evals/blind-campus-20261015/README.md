# Blind check 2026-10-15: offices, topics, dining, menus and shuttle

Asked by Dan on 2026-10-15 ("can you test again but do a blind test?") after the all-offices,
topics and daily-refresh work. Release under test: the daily dev release on :8013 (written mode).

1. Four writer agents (students; parents and visitors; faculty and staff; fast phone typers) wrote 25
   questions each from writer-brief.md alone: no code, data, logs or earlier sets.
2. The set is frozen as questions-frozen.json with its sha256 before any question is sent.
3. run_blind.py sends each question once (refuses a changed file) and keeps every full response.
4. Two independent graders per batch judge each answer against the published data only
   (grader-prompt.md). Their disagreements are shown to Dan; nothing is changed after the run.

Pass bar (set before the run): no answer graded "wrong" with severity "serious" by both graders,
and correct + declined_ok at least 80% of the scored answers (round 2 on 10-14 was 79%).

Dropped before freezing: w1-08 and w2-04 ("bursar email?", the same as an earlier set's case) and
w1-22 ("public safety non emergency number", also in an earlier set). 97 questions are frozen.
