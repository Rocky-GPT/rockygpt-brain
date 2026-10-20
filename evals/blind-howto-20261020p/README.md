# Sixteenth blind "how do I" and policy check 2026-10-20: the stronger-model trial

Fresh sets h to o on gpt-6-luna held near 60% (h66 i58 j60 k67 l51 m58 n59 o62) with serious
answers at 0-1 a set; the misses left are details the section pick or the writer drops. Dan chose
(10-20) to trial a more capable model for those two calls only: brain 9e0180f (optional strong
model priced on its own) run with GPT-6.1 Sol for pick_sections and write_answer, everything else
on gpt-6-luna, per-turn limit raised to $0.25 for the reservation. Same method, nothing reused: four
writer agents from writer-brief.md alone, new personas (everyday undergraduates p1; residents and
parents p2; policies and rules p3; campus services and involvement p4); exact repeats of any
earlier eval question dropped; frozen and committed before any run; the person running the fixes
did not read the questions. Run once on the Brain in release-under-test.txt, two graders per batch,
worst of two. Nothing changed after. Cost and time are reported beside the score.

Pass bar (the same): no answer "wrong"/"serious" by both graders; correct + declined_ok at least 70%.
