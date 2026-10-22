# Fifteenth blind "how do I" and policy check 2026-10-20

blind-howto-20261020n was run on brain f3d8b47 (59%, 1 both-serious; 35 incomplete). Across fresh
sets h to n the score held near 60% while serious answers fell to 0-1 a set, and most misses are
incomplete (the right page read, a detail asked left out). Changed: brain db54d7c (the picker may
keep six entries instead of four; each carries up to 1,400 characters of its page's best-matching
words instead of 900). Same method, nothing reused: four writer agents from writer-brief.md alone,
new personas (a mix of undergraduates o1; money with the college o2; academics o3; campus life and
services o4); exact repeats of any earlier eval question dropped; frozen and committed before any
run; the person running the fixes did not read the questions. Run once on the Brain in
release-under-test.txt, two graders per batch, worst of two. Nothing changed after.

Pass bar (the same): no answer "wrong"/"serious" by both graders; correct + declined_ok at least 70%.
