# Jev decisions

Jev is Typesafe's fast reading model (`jev-1.13.0`). It answers yes/no and pick-one
questions about text and never writes. The Brain asks it what the student's words
mean, so code never has to. The one exception is the short danger phrase list in
`safety.py`, kept as a backstop for when Jev is down or misses danger.

## One call per turn

After the safety check, `decisions.py` sends Jev one call with every question.
Jev reads:

- `latest_request`: the new question.
- `prior_messages`: the earlier messages.
- `campus_time`: the turn's one campus clock.

Jev decides. Code follows Jev's top pick on every question: a pick-one answer's choice,
and yes when Jev puts yes at 0.50 or more. A pick Jev put under 0.90 is still followed,
and the diagnostics list it as low confidence, so a wrong pick shows up in the export
and gets fixed at its root.

| Question | Kind | What code does with the pick |
| --- | --- | --- |
| `danger` | pick: self-harm, danger, none | Self-harm or danger: the danger route, and the safety help comes first. |
| `own_account` | yes/no | Yes, with `own_account_only` yes, and the question is the first one or `needs_earlier` is no: the account action route, and code says what RockyGPT can't reach. |
| `own_account_only` | yes/no | See above. It keeps "register me and where is the Registrar?" from losing its second part. |
| `needs_earlier` | yes/no | Kept for Conversation State (milestone 7), and read by `own_account`. |
| `multi_part` | yes/no | Yes: the multi-part route. |
| `work` | pick: calculate, look up, policy, general, reasoning, can't do, unclear | Names the route for everything else (below). |
| `subject` | pick: dining, transport, places, people, academics, student life, housing, money, safety, none | Kept for Campus Retrieval (milestone 5). |
| `named` | pick: place, office, person, group, event, course, several, none | The same. Which place or office it is needs the campus name list. |
| `needs` | pick: campus information, the conversation, their own account, someone else's private information, right now, a guess, outside knowledge | Code reads it as supported, private, live-only or unsupported. |

From these, code picks one of the nine routes in Dan's routing table (`ROUTES` and
`handler` in `decisions.py`). It goes down Jev's picks in this order and follows the
first that settles it: `danger`, then `own_account`, then `multi_part`, and then `work`,
which always settles it.

| Route | Goes to | Jev's pick |
| --- | --- | --- |
| exact | code | `work`: calculate |
| campus fact | retrieval | `work`: look up |
| document or policy | retrieval + GPT | `work`: policy |
| general question | GPT | `work`: general |
| complex reasoning | GPT | `work`: reasoning |
| multi-part | orchestrator | `multi_part` |
| account action | capability limit | `own_account`, or `work`: can't do |
| danger | safety path | `danger`, or the danger phrases |
| ambiguous | clarification | `work`: unclear |

Every route is a place Jev sends a question on purpose. Ambiguous means the student's
words are unclear. It is never where a question lands because Jev wasn't sure. For now
the turn ends in one of four ways, because each route's handler comes with its milestone.
Danger gets the safety help. The account action route gets words code wrote: the account
limit when all of it needs the student's own account, otherwise the line for the reason
Jev's `needs` pick names (the student's own account, someone's private information, a
live look, or "I can't help with that one" for any other reason, which is a placeholder
until what a guess or an opinion gets is settled, see results.md run 5). For a can't-do,
`handlerPath` ends with `needs`, so a shaky `needs` pick is listed as low confidence. The ambiguous route asks the student to say it
another way. Every other route says "not ready". The route (`handler`), where it goes
(`goesTo`), the picks that led to it (`handlerPath`) and those under 0.90
(`lowConfidence`) are in the dev diagnostics (`metrics.jev.decided`). The route and the
low-confidence picks are also in the turn log.

`scripts/check_decisions.py` asks Jev the 130 labeled questions in
`evals/decisions/cases.json` (the 30-question audit and the 100-question stress run) and
scores each item. Its results are in `evals/decisions/results.md`.

Every question asks about the student's own words, never the Brain's labels. The
wording is the old Brain's, tested on Jev. The numbers are in the comments in
`decisions.py`, and the lessons are in [routing/README.md](routing/README.md), which
describes the old Brain's routing. Later milestones add their questions to the same
call.

## Money

`jev.py` holds a call's worst case in the ledger before sending it. The worst case is
one token per byte of the call, plus 8,192, at 42 nanodollars per input token. After
the call, it settles the tokens Typesafe reports. A call is about $0.0001.

- A call Typesafe refused before reading it (no connection, a rejected key or request,
  rate limiting, overload) cost nothing, so its hold is settled at zero with the error
  that says why. Any other failed call stays held as uncertain and counts against the
  month, because it may have been read and charged.
- A call is sent only if it should fit Typesafe's limits (32k tokens for the state and
  any one question, 64k in all), counting two bytes to the token. Our own calls ran about
  three on 09-29 and English runs over four. The hold still uses one byte to the token, so
  the estimate never lowers what is held.
- The price lives in `src/rockygpt_brain/prices.json`. It was checked on
  <https://docs.typesafe.ai/models> on 2026-09-29 and is trusted until 2026-12-28.
- `.github/workflows/price-window.yml` opens an issue two weeks before that date. Past
  it, every turn without a danger phrase fails with 503 `model_not_configured`.

## When Jev can't help

Without Jev's readings there is no plan, so the turn fails on purpose. It never hands the
question to a model instead. Like every failure it carries the 911/988 help, and the
danger phrases still work, because they need no Jev.

| What went wrong with Jev | What the student gets |
| --- | --- |
| a timeout (2 s for the whole call, connecting included) | 504 `model_timeout` |
| no connection (opened twice before giving up) | 503 `model_unreachable` |
| rate limiting or overload (429, 529) | 429 `busy` |
| a rejected key (401, 403) | 503 `model_not_configured`, not retryable |
| a request Typesafe refused as invalid (other 4xx) | 500 `internal_error`: a bug in the Brain, in the turn log |
| another provider error (5xx, 408), missing usage, or a different model | 502 `model_provider_error` |
| an answer in the wrong shape | 502 `invalid_model_output` |
| a call too long for Jev | 422 `context_limit`, not retryable: a shorter chat helps |
| an expired price | 503 `model_not_configured`, not retryable |

The turn's diagnostics keep Typesafe's HTTP status (`metrics.jev.httpStatus`) and how long
the failed call took (`elapsedMs`), and the turn log has the time as `jevMs`.

`JEV_FAILURES` in `turn.py` holds the table, and a test checks that every error `jev.py`
raises has a row. Until the route handlers exist, trying again still ends in `not_ready`
when Jev works, so "retryable" is only fully honest once a handler can answer.

A spent allowance or a ledger problem is different. It stops all paid work and fails
the turn, but danger help still comes first.

## Settings

Jev runs only when all three of these are set: `BRAIN_ENVIRONMENT`,
`BRAIN_LEDGER_DATABASE_URL` and `BRAIN_TYPESAFE_API_KEY`. Without them, every turn
says `routing_unavailable` in its diagnostics.

## Shuttle questions (milestone 5)

When the campus data is set up, six more questions ride in the same one call
(`shuttle_ask.py`): is it about a shuttle's times or stops, what does it want to know,
which trip, is a clock time attached, which day, and which stop. Code writes the day and
stop options (seven dates from the campus clock, the stops from the timetable), so the
timetable is read before Jev is asked. The nine questions above stay as they are.

Code follows Jev's top pick on these too, with one rule of its own: an answer is never
built on a near coin flip. If the least sure of these picks is under 0.6, the turn is "not
ready" (`unsure`). The floor covers only the shuttle picks; the nine follow the rule above.
The results, the miss against the bar and the known limits are in
[shuttle-benchmark.md](shuttle-benchmark.md).
