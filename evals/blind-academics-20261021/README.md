# Blind academics check 2026-10-21: majors, minors, requirements and courses

The first check of the academics area (Dan, 10-21: academics, then events and clubs, then production,
then people and faculty). Written and frozen BEFORE any academics code was built, so the set cannot
be tuned to the build: four writer agents from writer-brief.md alone, 15 questions each (undecided
first-years ac1; declared majors asking about courses and prerequisites ac2; minors, second majors,
certificates and transfer students ac3; prospective students and parents ac4); exact repeats of any
earlier eval question dropped.

Run once on the Brain build named in release-under-test.txt when academics is ready; two graders per
batch (grader-prompt.md, to be written from the how-to grader prompt with the catalog as evidence),
worst of two. Pass bar as for the other areas: no answer "wrong"/"serious" by both graders; correct +
declined_ok at least 90%.
