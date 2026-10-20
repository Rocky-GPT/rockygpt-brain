# Seventeenth blind "how do I" and policy check 2026-10-20: the page's own search terms

The stronger-model trial (set p, GPT-6.1 Sol for picking and writing) scored 64% with 1 serious
answer at ~15x the cost, inside gpt-6-luna's 51-67 range, so the trial was not adopted and both
calls are back on gpt-6-luna. Set p's misses were sorted by where the answer was lost: about half
were pages never offered (the student's words are not the page's: plagiarism vs academic integrity,
freshman vs first-year, cost vs fees), a third were details on a picked page that the writer was
not shown. Brain 0d9bda3 + 570c141: the lookup names the page's own terms beside the student's
(page_terms), the pages and the other-office search read both, up to three other offices with eight
sections each are offered, and an entry carries up to three parts of its page (2,400 characters).

Same method, nothing reused: four writer agents from writer-brief.md alone, new personas (transfer
students and commuters q1; graduate, adult, evening students and families q2; academic rules and
trouble q3; athletes, international students, student workers and support services q4); exact
repeats of any earlier eval question dropped; frozen and committed before any run; the person
running the fixes did not read the questions. Run once on the Brain in release-under-test.txt, two
graders per batch, worst of two. Nothing changed after.

Pass bar (the same): no answer "wrong"/"serious" by both graders; correct + declined_ok at least 70%.
