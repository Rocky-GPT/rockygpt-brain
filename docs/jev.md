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
| `danger` | pick: self-harm, danger, none | Self-harm or danger: the safety help comes first. |
| `own_account` | yes/no | Yes, with `own_account_only` yes, and the question is the first one or `needs_earlier` is no: code says what RockyGPT can't reach. |
| `own_account_only` | yes/no | See above. It keeps "register me and where is the Registrar?" from losing its second part. |
| `needs_earlier` | yes/no | Kept for Conversation State (milestone 7), and read by `own_account`. |
| `asks` | pick: fact, list, how to, rule, advice, action, recall, chat | Names the handler for what it asks (below). |
| `subject` | pick: dining, transport, places, people, academics, student life, housing, money, safety, none | Kept for Campus Retrieval (milestone 5). |
| `named` | pick: place, office, person, group, event, course, several, none | The same. Which place or office it is needs the campus name list. |
| `needs` | pick: campus information, the conversation, their own account, someone else's private information, right now, a guess, outside knowledge | Code reads it as supported, private, live-only or unsupported, and as answer, account limit or can't answer. |
| `multi_part` | yes/no | Yes: several parts. |
| `open_ended` | yes/no | Yes: it wants an explanation, advice, a comparison, a view or conversation, so GPT. |

From these, code names the handler a later milestone will build (`handler` in
`decisions.py`). It goes down Jev's picks in this order and follows the first that
settles it:

1. danger → safety
2. `own_account` → access limit
3. `multi_part` → several parts. It comes before `needs`, which reads the request as
   one.
4. `needs` says their own account → access limit; private, right now, a guess or
   outside knowledge → can't answer
5. `open_ended` → GPT
6. `asks`: a fact or list → exact, how to or a rule → document or policy, recall →
   conversation, advice or chat → GPT, an action → can't answer

GPT is a place Jev sends a question on purpose. It is never where a question lands
because Jev wasn't sure. For now the turn still ends with the safety help, the account
limit or "not ready". The handler, the picks that led to it (`handlerPath`) and those
under 0.90 (`lowConfidence`) are in the dev diagnostics (`metrics.jev.decided`). The
handler and the low-confidence picks are also in the turn log.

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

- The price lives in `src/rockygpt_brain/prices.json`. It was checked on
  <https://docs.typesafe.ai/models> on 2026-09-29 and is trusted until 2026-12-28.
- `.github/workflows/price-window.yml` opens an issue two weeks before that date. Past
  it, Jev is skipped.

## When Jev can't help

Nothing Jev does wrong reaches the student. In each of these cases the turn goes on
without Jev's answers, and the danger phrases still work:

- a timeout (2 s),
- no connection, rate limiting or a provider error,
- an answer in the wrong shape,
- a different model,
- missing usage,
- a call too long for Jev,
- an expired price.

A spent allowance or a ledger problem is different. It stops all paid work and fails
the turn, but danger help still comes first.

## Settings

Jev runs only when all three of these are set: `BRAIN_ENVIRONMENT`,
`BRAIN_LEDGER_DATABASE_URL` and `BRAIN_TYPESAFE_API_KEY`. Without them, every turn
says `routing_unavailable` in its diagnostics.
