# Twenty-fourth blind "how do I" and policy check 2026-10-20: long sections and more linked pages

Meaning-search sets: v 72%, w 70% (0 serious). In w, 12 of 30 misses were pages never offered:
some linked to no office (681 of 2,574 pages), some an item deep in a long list. Data 245a9dc: a
college policy with no Responsible Unit header is linked by the office its Contacts section names
(14 more, among them student complaints of a non-academic nature), and the Adult Students site to
Admissions (marked not reviewed by Dan); release dev-campus-daily-20261020-1528 (20,721 units).
Brain 341b1e5: long units are also embedded in 1,000-character pieces and said from the piece that
matched (47,729 vectors).

Same method, nothing reused: four writer agents from writer-brief.md alone, new personas (students
asking whether something is allowed in housing or on campus x1; students with a problem with a
person or an office x2; students asking about deadlines and what happens if they miss them x3;
students asking about services they have never used x4); exact repeats of any earlier eval
question dropped; frozen and committed before any run; the person running the fixes did not read
the questions. Run once on the Brain in release-under-test.txt, two graders per batch, worst of two.

Pass bar (raised 10-20 by Dan): no answer "wrong"/"serious" by both graders; correct + declined_ok
at least 90%.
