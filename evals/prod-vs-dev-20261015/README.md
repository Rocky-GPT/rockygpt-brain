# Production vs dev: what each Brain knows (2026-10-15)

Question: before the graph-only Brain replaces production, does it answer less than the production
Brain does? Production (old Brain, Postgres release v2-20261007174845) has full-text documents,
calendar, events, clubs, programs, courses and faculty; dev (graph-only, dev-campus-topics-20261015)
has offices, dining, shuttle and topics.

## Cases (frozen)

`cases.json`, 120 cases, sha256 `f5779a9a41c5a01cf136cbcbc1fa4a295602165e5b94198b2eaa6c56a978975f`,
built by `build_cases.py` from the 2026-10-15 office-pages and academic-sites captures in
rockygpt-data. Every question is a heading Ramapo publishes on its own page ending in "?", and the
text under it is the official answer. No AI wrote or chose a question; the rules are mechanical
(see the script). These are what offices publish, not what students ask: there is no student data.

## Run

Each case once, one at a time, one-message conversations, on each Brain:

    python3 run.py https://rockygpt-brain.onrender.com/v1/chat prod.json
    python3 run.py http://127.0.0.1:8013/v1/chat dev.json --diagnostics

Dev :8013 is the graph-only Brain with written answers (BRAIN_OUTPUT=written), same graph as :8000.

## Grading

`grade_pack.py pack` writes blind batches (A/B drawn per case, key hidden); two graders per batch
follow `grading.md` word for word; `grade_pack.py tally` keeps a label only when both agree and
lists every disagreement. Cases either grader calls unscorable are left out for both systems.

## Decision rule (fixed before the run)

On scored cases, "substance" = answered + partial.
- Dev is a step back if its substance count is 6 or more below production's, or it has more
  `wrong` labels than production.
- Within 5 either way is a tie (one run of each; single runs swing a few cases).
- Disputed labels are reported, never counted for either side.
Nothing is changed in either Brain after the run; the result is reported as is.
