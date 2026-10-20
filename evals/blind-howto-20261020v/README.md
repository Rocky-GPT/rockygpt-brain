# Twenty-second blind "how do I" and policy check 2026-10-20: how-to sections found by meaning

Fresh sets on word matching: r 74%, s 65%, t 65%, u 68%, each with 0 answers serious by both
graders. On 2026-10-20 Dan raised the bar to 90% and chose meaning-based search. On 155 graded
misses with a known answer page, word matching put that page in the top 20 for 54% of questions;
meaning (text-embedding-3-large of each unit with its page, and of three student questions per
how-to entry) put it there for 80%. Brain 9d947c2 embeds the student's message and what they want,
scores every how-to unit of the release (scripts/build_meaning_index.py builds the index), and
ranks units by meaning within the asked office and to choose the other offices offered; gpt-6-luna
still picks the sections and writes.

Same method, nothing reused: four writer agents from writer-brief.md alone, new personas (students
who describe a situation instead of naming what they need v1; transfer, readmitted and
non-traditional students v2; residents dealing with housing, roommates, facilities and safety v3;
students asking about money, aid, jobs and payments v4); exact repeats of any earlier eval
question dropped; frozen and committed before any run; the person running the fixes did not read
the questions. Run once on the Brain in release-under-test.txt, two graders per batch, worst of two.

Pass bar (raised 10-20 by Dan): no answer "wrong"/"serious" by both graders; correct + declined_ok
at least 90%.
