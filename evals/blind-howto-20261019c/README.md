# Third blind "how do I" and policy check 2026-10-19: the model picks the sections

blind-howto-20261019b was run on brain 440218e (45.5%, bar 70% not met); Dan chose to let a model
keep the sections that answer (brain 5371cf5), so that set is no longer blind. Same method, nothing
reused: four writer agents wrote 25 questions each from writer-brief.md alone, with new personas
(program students in nursing, education, business, sciences and arts m1; students in trouble m2;
incoming students and families m3; employees and graduate assistants m4). Exact repeats of any
earlier eval question are dropped. Frozen with its sha256 and committed before any question is
sent; the person running the fixes did not read the questions before the run. Run once on the Brain
in release-under-test.txt, two graders per batch (grader-prompt.md), worst of two. Nothing changed.

Pass bar (set before the run, the same as before): no answer graded "wrong"/"serious" by both
graders; correct + declined_ok at least 70%. Repeated kinds of misses and the answer time are listed.
