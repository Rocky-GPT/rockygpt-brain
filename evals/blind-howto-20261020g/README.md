# Seventh blind "how do I" and policy check 2026-10-20

blind-howto-20261019f was run on brain cf1ce22 (58%, 0 both-serious: bar not met; the full-sections
writer rule was reverted in 99dfbf1 for latency). Dan then chose (10-19): split pages into
question-sized sections, narrow the emergency phrase rule, collect the catalog's academic policies,
and always read procedures for deadlines and what-happens-if. Built: data 25a129d (each WordPress
FAQ question with its answer is its own how-to section; reviewed section text unchanged), data
27badc7 (63 academic policies from catalog.ramapo.edu, owned by the Office of the Provost), brain
c0c421c (a thing hurting me is not a person hurting me) and f892a96 (procedures also with
who-to-contact). Same method, nothing reused: four writer agents from writer-brief.md alone, new
personas (demanding majors and academic standing g1; working commuters and money g2; student
leaders, clubs and facilities g3; administrative and technology hassles g4); exact repeats of any
earlier eval question dropped; frozen and committed before any run; the person running the fixes
did not read the questions. Run once on the Brain in release-under-test.txt, two graders per batch,
worst of two. Nothing changed after.

Pass bar (the same): no answer "wrong"/"serious" by both graders; correct + declined_ok at least 70%.
