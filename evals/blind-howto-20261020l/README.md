# Twelfth blind "how do I" and policy check 2026-10-20

blind-howto-20261020k was run on brain e4984bb (67%, 0 both-serious, 4 minor wrong: the closest to
the bar). Fixed for the general cause: brain c64a855 (an entry's page words come from its whole
page, not only its own section; the asked rule was in another section of the same page). Same
method, nothing reused: four writer agents from writer-brief.md alone, new personas (part-time and
evening students who work l1; residents asking what is allowed l2; academic records and rules l3;
support services l4); exact repeats of any earlier eval question dropped; frozen and committed
before any run; the person running the fixes did not read the questions. Run once on the Brain in
release-under-test.txt, two graders per batch, worst of two. Nothing changed after.

Pass bar (the same): no answer "wrong"/"serious" by both graders; correct + declined_ok at least 70%.
