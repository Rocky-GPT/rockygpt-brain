# Twenty-fifth blind "how do I" and policy check 2026-10-20: the more capable model writes

Meaning-search sets on gpt-6-luna: v 72%, w 70%, x 68% (x had 1 serious: the writer said the
alcohol policy "does not say" what an underage roommate means though the rule was in both picked
sections). Dan chose (10-20) a stronger writer only: brain 807b2df runs write_answer on GPT-6.1 Sol
(BRAIN_OPENAI_STRONG_CALLS=write_answer); search, picking and understanding stay on gpt-6-luna.

Same method, nothing reused: four writer agents from writer-brief.md alone, new personas (students
asking about rules that differ by group: residents vs commuters, undergraduate vs graduate,
full-time vs part-time y1; students asking about money they are owed or charged y2; students in
their first semester asking how things work y3; students about to graduate or leave y4); exact
repeats of any earlier eval question dropped; frozen and committed before any run; the person
running the fixes did not read the questions. Run once on the Brain in release-under-test.txt, two
graders per batch, worst of two. Cost and time are reported beside the score.

Pass bar (raised 10-20 by Dan): no answer "wrong"/"serious" by both graders; correct + declined_ok
at least 90%.
