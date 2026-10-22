# Thirty-fourth blind "how do I" and policy check 2026-10-21: per-page cap

Set zg scored 75% (44/59), 0 serious by both graders, on brain d290f65 (one call, second look,
office cards). This set runs brain 661b295: each question part keeps its best 10 units with at
most two from one page record (the next page instead of a page's near-copies), and the
"pages disagree" rule applies only to the same people, place and term.

Same method as zg, smaller by Dan's word: four writer agents from writer-brief.md alone, 15
questions each, new personas (juniors and seniors planning ahead zh1; parents and family zh2;
international students and student veterans zh3; technology and campus services zh4); exact
repeats of any earlier eval question dropped; frozen and committed before any run. Run once on
the Brain in release-under-test.txt, two graders per batch, worst of two.

Pass bar (raised 10-20 by Dan): no answer "wrong"/"serious" by both graders; correct + declined_ok
at least 90%.
