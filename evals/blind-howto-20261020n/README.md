# Fourteenth blind "how do I" and policy check 2026-10-20

blind-howto-20261020m was run on brain 5543f6a (58%, 0 both-serious; 11 wrong declines). Fixed for
the general causes: brain 5801cf7 (the section picker reads the part of each candidate that best
matches the question, has 18 s instead of 12 and gets up to 16 candidates), 5c0b0cc (a page offers
its own sections beside its listed entries, each kept only when it matches better than all of the
page's entries) and f3d8b47 (the picker's snippet leaves kept values and their assertions alike, so
a picked long section is said, not failed). Same method, nothing reused: four writer agents from
writer-brief.md alone, new personas (first-years in spring n1; juniors and seniors n2; rules and
what happens when they are broken n3; people outside the student body n4); exact repeats of any
earlier eval question dropped; frozen and committed before any run; the person running the fixes
did not read the questions. Run once on the Brain in release-under-test.txt, two graders per batch,
worst of two. Nothing changed after.

Pass bar (the same): no answer "wrong"/"serious" by both graders; correct + declined_ok at least 70%.
