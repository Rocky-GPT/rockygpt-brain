# Twenty-sixth blind "how do I" and policy check 2026-10-20: the more capable model picks and writes

Set y (GPT-6.1 Sol writing, gpt-6-luna picking) scored 82% with 0 serious, up from v/w/x 72/70/68
on Luna alone. Of its 17 misses, 5 were the right section offered and not picked. This set runs the
section pick on Sol too (BRAIN_OPENAI_STRONG_CALLS=write_answer,pick_sections), and brain 2bfa343
says an entry's own section from where its meaning matched (an item deep in a list its steps sum up).

Same method, nothing reused: four writer agents from writer-brief.md alone, new personas (students
asking two-part questions: how to do something and what happens if they don't z1; students asking
about forms, portals and where to click z2; students who are worried or upset and ask in a long
message z3; staff-adjacent students: student employees, RAs, tutors and TAs asking about rules
that apply to them as students z4); exact repeats of any earlier eval question dropped; frozen and
committed before any run; the person running the fixes did not read the questions. Run once on the
Brain in release-under-test.txt, two graders per batch, worst of two. Cost and time reported.

Pass bar (raised 10-20 by Dan): no answer "wrong"/"serious" by both graders; correct + declined_ok
at least 90%.
