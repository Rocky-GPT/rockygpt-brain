# Production vs dev on structured questions (2026-10-15)

The FAQ comparison (../prod-vs-dev-20261015) mostly asked things that need page text, which the
graph-only Brain was not built to hold yet. This set asks only what both Brains are built to answer
from structured data or code: office email, phone, room, hours on a day, how to reach, fax; dining
venue hours on a day; shuttle departures on a day. No "now"/"today" and no menus.

## Frozen set

- `slots.json` (sha256 6eeb00f3...): what each case asks, drawn by `plan.py` (seed 20261015) from
  the dev graph's office list and Ramapo's own pages, committed before any wording (f2589f7).
- `cases.json` (sha256 10333269127835185c9eb9565333dc7f7a3f1807ee2985a41c4f1bae6fdd871a): the 60
  student messages, worded by two blind writers who saw only what each slot asks and a style.
- `key.json` (sha256 0a839edfffd45bf31f71c5aed18626d251b771012b021d8fc25006167ef324e4): the
  expected answers, written by two blind labelers per case from the captured official pages only
  (`labeling.md`). 5 disputes ruled by Dan on 2026-10-15 (`ruled` field): s015, s033, s038, s057
  scored; s034 soft. 54 scored, 6 soft (run and reported, never scored), 9 scored as not published.
  `REVIEW.md` is the list Dan reviewed.

## Run

    python3 ../prod-vs-dev-20261015/run.py https://rockygpt-brain.onrender.com/v1/chat prod.json --cases cases.json
    python3 ../prod-vs-dev-20261015/run.py http://127.0.0.1:8013/v1/chat dev.json --diagnostics --cases cases.json

Each case once, one at a time. Grading: `grade.py pack`, two blind graders per batch following
`grading.md`, `grade.py tally` (a label counts only when both graders agree).

## Decision rule (fixed before the run)

On scored cases: a gap of 4 or more `correct` is a real difference; less is a tie. Every `wrong`
and every `unverified` (a value given where the key says not published) is listed. Nothing is
changed in either Brain after the run.
