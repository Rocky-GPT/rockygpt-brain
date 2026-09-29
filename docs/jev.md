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

| Question | Kind | Acts when |
| --- | --- | --- |
| `danger` | pick: self-harm, danger, none | Its top pick is self-harm or danger. The safety help comes first. |
| `own_account` | yes/no | 0.90 or more, and `own_account_only` is 0.50 or more, and the question is the first one or stands alone. Code then says what RockyGPT can't reach. |
| `own_account_only` | yes/no | See above. It keeps "register me and where is the Registrar?" from losing its second part. |
| `needs_earlier` | yes/no | Sure at 0.90 or more (yes) or 0.10 or less (no). Otherwise it's undecided. |
| `asks` | pick: fact, list, how to, rule, advice, action, recall, chat | Its top pick is 0.90 or more. |
| `subject` | pick: dining, transport, places, people, academics, student life, housing, money, safety, none | The same. |
| `named` | pick: place, office, person, group, event, course, several, none | The same. Which place or office it is needs the campus name list (milestone 5). |
| `needs` | pick: campus information, the conversation, their own account, someone else's private information, right now, a guess, outside knowledge | The same. Code reads it as supported, private, live-only or unsupported. |
| `multi_part` | yes/no | Sure at 0.90 or more, or 0.10 or less. |

From these, code names the handler a later milestone will build (`handler` in
`decisions.py`): safety, access limit, can't answer, several parts, exact, conversation,
document or policy, or GPT. What Jev isn't sure of goes to GPT. For now the turn still
ends with the safety help, the account limit or "not ready"; the handler is in the dev
diagnostics (`metrics.handler`) and the turn log.

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
