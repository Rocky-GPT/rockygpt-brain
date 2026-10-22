# Blind "how do I" and policy check 2026-10-19: before and after the procedures field

Dan's next area (10-19): policies and "how do I" procedures, from the office that publishes them
(data d6e5fc9 office_sections, brain eee902e procedures). This set measures it on questions no
one tuned anything to.

1. Four writer agents wrote 25 questions each from writer-brief.md alone (no code, data, logs or
   earlier sets): undergraduates (h1); faculty and staff (h2); parents, guests, prospective
   students and alumni (h3); transfer, graduate, international and student-veteran students (h4).
2. Exact repeats of any earlier eval question (2,709 compared, including the 10-15 FAQ headings)
   are dropped: none were. 100 questions are frozen in questions-frozen.json with the sha256 in
   questions-frozen.sha256, committed before any question is sent. The person running the work
   did not read the questions before the runs.
3. run_blind.py sends each question once on each Brain in release-under-test.txt: "before" (brain
   4647507, no procedures field) and "after" (the procedures field on a release with office
   sections). Both runs are graded against the same published pages and records.
4. Two independent graders per batch follow grader-prompt.md; merge.py takes the worst of the two.
   Nothing is changed after the runs.

Pass bar for "after" (set before the runs): no answer graded "wrong" with severity "serious" by
both graders; correct + declined_ok at least 70% of the scored answers (a first version of a new
area, so lower than the 80% of the campus sets); and more correct answers than "before". The
share with asked_ok is reported beside it, and repeated kinds of misses are listed.
