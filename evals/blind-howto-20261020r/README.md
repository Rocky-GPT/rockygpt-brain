# Eighteenth blind "how do I" and policy check 2026-10-20: page terms alone

Set q (brain 570c141) scored 53% with 1 serious answer and 7 wrong; three of the wrong answers came
from its wider page text (three parts of a page, 2,400 characters), which handed the writer rules
from unrelated parts of the same page. c7563ce puts page words back to the one best-matching part
(1,400 characters) and keeps the rest of 0d9bda3/570c141: the lookup's page_terms beside the
student's words for every page search, three other offices with eight sections each.

Same method, nothing reused: four writer agents from writer-brief.md alone, new personas (first-year
students and their first weeks r1; seniors, graduation and careers r2; money, bills, aid and
refunds r3; housing, dining, safety and getting around r4); exact repeats of any earlier eval
question dropped; frozen and committed before any run; the person running the fixes did not read
the questions. Run once on the Brain in release-under-test.txt, two graders per batch, worst of two.

Pass bar (the same): no answer "wrong"/"serious" by both graders; correct + declined_ok at least 70%.
