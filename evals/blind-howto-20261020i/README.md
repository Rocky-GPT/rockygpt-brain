# Ninth blind "how do I" and policy check 2026-10-20

blind-howto-20261020h was run on brain 5434f4c with pre-built how-to entries (66%, 1 both-serious:
a general study abroad question got one program's application and a contact note's instruction
for international students). Fixed for the general cause: brain c429c0b (a section about one
program, place or event answers only a question naming it; a contact note's instruction for
another group is never a step) and 989e29f (each picked how-to entry's steps are said in full; the
misses mostly had the detail in the writer's facts and dropped it). Same method, nothing reused:
four writer agents from writer-brief.md alone, new personas (registration problems i1; housing
logistics i2; careers and internships i3; campus rules and safety i4); exact repeats of any earlier
eval question dropped; frozen and committed before any run; the person running the fixes did not
read the questions. Run once on the Brain in release-under-test.txt, two graders per batch, worst
of two. Nothing changed after.

Pass bar (the same): no answer "wrong"/"serious" by both graders; correct + declined_ok at least 70%.
