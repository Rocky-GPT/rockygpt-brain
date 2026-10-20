# Thirteenth blind "how do I" and policy check 2026-10-20

blind-howto-20261020l was run on brain c64a855 (51%, 2 both-serious: answers said Residence Life's
pages do not cover items their prohibited-items list bans). Fixed for the general causes: brain
5543f6a (a long list without sentence ends is read in ~300-character pieces so the piece naming the
item is said; page-only asked words count; a page whose listed entries share no asked word offers
its sections; the writer never says a page or office does not cover something). Same method,
nothing reused: four writer agents from writer-brief.md alone, new personas (sophomores m1;
financial aid paperwork m2; campus life rules outside housing m3; technology and online systems
m4); exact repeats of any earlier eval question dropped; frozen and committed before any run; the
person running the fixes did not read the questions. Run once on the Brain in
release-under-test.txt, two graders per batch, worst of two. Nothing changed after.

Pass bar (the same): no answer "wrong"/"serious" by both graders; correct + declined_ok at least 70%.
