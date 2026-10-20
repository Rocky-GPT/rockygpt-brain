# Twenty-third blind "how do I" and policy check 2026-10-20: the entry's own section for the writer

Set v (meaning search, 9d947c2) scored 72% with 0 serious; of its 28 misses 12 had the right
how-to entry picked and still left a detail out, because the entry's steps (written once from its
section) had dropped it (the aid policy's undergraduate 2.0 GPA beside the graduate 3.0). Thinking
harder ("high" effort for the pick and the writer) was tried and dropped: writer calls of 12-20 s
put 2 of 5 answers past the 52 s turn limit. Brain 17af5bf + 0a000c3: each picked entry carries its
own section's words (section_words), and the writer gives every rule, number, date, amount or
contact in them that answers what the student wants, each group's when a rule differs by group.

Same method, nothing reused: four writer agents from writer-brief.md alone, new personas
(students writing in a rush about a deadline today or tomorrow w1; graduate and professional
students w2; students who ask about rules for a specific group they belong to (athletes, EOF,
honors, commuters, veterans) w3; parents and family members asking for their student w4); exact
repeats of any earlier eval question dropped; frozen and committed before any run; the person
running the fixes did not read the questions. Run once on the Brain in release-under-test.txt, two
graders per batch, worst of two.

Pass bar (raised 10-20 by Dan): no answer "wrong"/"serious" by both graders; correct + declined_ok
at least 90%.
