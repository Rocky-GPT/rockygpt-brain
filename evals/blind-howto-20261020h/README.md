# Eighth blind "how do I" and policy check 2026-10-20

blind-howto-20261020g was run on brain f892a96 (56%, 2 both-serious: a section sharing words but
for another task, and an outdated orientation page over Public Safety's own). Dan chose (10-20)
pre-built how-to entries: data 3e26aa0/3b378bb (a model reads each office page once per content
and lists entries: task, who it is for, the office that handles it, term, steps each held to a
quote on the page; publish keeps only verified steps, migration 033), brain 5434f4c (a page with
entries offers them instead of its sections; picker and writer see who it is for and the handling
office) and 2ffd12c (the writer's call is reserved; 2,500-character target). Same method, nothing
reused: four writer agents from writer-brief.md alone, new personas (first-generation and EOF h1;
veterans, military families and student parents h2; health and personal issues h3; ambitious
planners h4); exact repeats of any earlier eval question dropped; frozen and committed (9418c43)
before any run; the person running the fixes did not read the questions. Run once on the Brain in
release-under-test.txt, two graders per batch, worst of two. Nothing changed after.

Pass bar (the same): no answer "wrong"/"serious" by both graders; correct + declined_ok at least 70%.
