# Twentieth blind "how do I" and policy check 2026-10-20: three repeated misses fixed at the root

Sets r (74%) and s (65%) on brain c7563ce averaged about 70, with 0 serious answers by both
graders; s did not confirm r. Three misses repeated across sets p-s, each traced to a general
cause and fixed where it starts:
- data 4f1f93d: a how-to step's quote is matched without the page's link addresses ("x@ramapo.edu
  (mailto:x@ramapo.edu)"); 321 of 18,328 steps had been dropped, among them the lost ID card's
  first step. Release dev-campus-daily-20261020-1035 (8,722 entries).
- brain 475f870: an entry's term ("Fall 2026") reaches the picker and the writer, and the picker is
  told today's date (the Fall 2026 withdrawal deadline had no term beside the other terms').
- brain 7c7e744: words lose -ing/-ed/-es/-s and a final e before their first six letters
  (funded/funds/funding are one word: "allocates funds to clubs" now matches club funding).

Same method, nothing reused: four writer agents from writer-brief.md alone, new personas
(sophomores and juniors in the middle of their degrees t1; students with health, wellness,
disability or family emergencies t2; students in trouble with fees, fines, holds, tickets and
conduct t3; students using technology, library, printing, email and accounts t4); exact repeats of
any earlier eval question dropped; frozen and committed before any run; the person running the
fixes did not read the questions. Run once on the Brain in release-under-test.txt, two graders per
batch, worst of two.

Pass bar (the same): no answer "wrong"/"serious" by both graders; correct + declined_ok at least 70%.
