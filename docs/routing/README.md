# Jev routing

Routing is implemented but defaults to **off**. Live validation is pending a TypeSafe
or OpenRouter key; `live-status.json` records the preflight result. No paid
evaluation calls have been made and no environment has been promoted.

## Behavior

`BRAIN_ROUTING_MODE=shadow` classifies requests and records metrics while preserving
GPT tool selection. `active` allows Jev to choose the first tool or directly execute
a validated contact/profile lookup. Both modes incur paid calls. `off` makes no
Jev calls and needs no TypeSafe credential.

Without active routing, one free rule makes the first-call choice instead: when the
latest request names exactly one curated identity by its longest name or alias,
GPT's first call must be `lookup_profile` or `lookup_contact`. GPT still picks the
sections, date and meal, and later calls regain every tool. Subjects don't count,
because a subject's profile lists no courses, and a follow-up that names nothing
keeps every tool. `metrics.graphFirst` and the turn summary record when the rule
applied. Shadow mode applies it too, so the paired evaluation compares Jev with it.

Jev sees the accepted conversation with the current request separated from prior
messages. Candidates come from the active release's curated identities, capped at
24 and ranked by current exact name/alias matches, previous mentions, then lexical
overlap. Candidates are selectors, not evidence or new identity links.

The router uses one request to pinned `jev-1.13.0`. It picks the route, the entity, the
day, the meal and whether the student describes danger, and asks one yes/no question per
profile detail (hours, menu, contact, location, events, leaders, teachers, courses),
whether the student wants the whole menu, whether the request needs the earlier
messages, what kind of campus information it asks for, and whether it asks for a whole
list rather than one particular thing. The route needs its top probability ≥0.70: over
four runs of the routing cases, route picks at 0.70–0.90 were right 21 of 21 times (nine
questions) and picks under 0.70 were right 17 of 19. Every other pick, the entity
included, needs probability and confidence ≥0.90. Contact and profile are both a lookup
of one entity, so when Jev splits between them and the two together reach 0.70 the
request is still a lookup: a contact lookup when contact leads and
contact is all that's asked, otherwise a profile lookup, which fetches contact details too.

Every question asks about the student's words, never the Brain's labels. Asked "Does
latest_request request the profile section 'menu'?", Jev answered 0.5 to 0.8 whether or
not the menu was asked. Asked "Does `latest_request` ask what food is served?", with a
line saying what counts as yes and no, it answered 0.9 or more, or 0.1 or less, on the
same requests (tested 2026-09-28; one change at a time, so neither the number of questions
nor the state was the cause). The day option quotes the words the campus resolver found
("The day it calls 'tomorrow'") instead of an ISO date, since matching the two is date
arithmetic. Each option describes one case: a catch-all listing several took 10–20% of
every answer.

A direct contact lookup fetches every field. A direct profile lookup fetches the sections
of every detail Jev says yes to (≥0.90) and of any it isn't sure about (above 0.10). When
it says yes to none, as for "Tell me about the club", the lookup fetches every routed
section instead. GPT still writes and reviews the answer from what was fetched, so an
extra section costs context, not accuracy. The whole-menu question raises the menu limit
only at ≥0.90. Simple dates reuse the existing campus-local resolver. A lookup that needs
a date defers unless Jev is ≥0.90 sure the student means the day the resolver read, or
names no day, so "next Saturday" and "the week after Thanksgiving" go to GPT. It also
defers when the resolver's weekday has already passed this week, as for "Saturday" asked
on a Sunday. A meal filter applies only when Jev's meal pick clears 0.90; otherwise every
meal is fetched. Meal labels are request filters, never proof of availability.
A follow-up runs its own lookup when Jev says it makes sense without the earlier messages
(0.10 or less) and it names exactly one entity itself, as for "What are the library's
hours tomorrow?" after another question. "Actually, …" and "What about …?" came back at
0.26 and 0.37 and stay with GPT. A follow-up that names no entity runs its own lookup
when Jev is ≥0.90 sure who "their" or "it" is from the conversation, as for "What is
their email?" (0.98 for the Registrar). Mixed or ambiguous requests defer through
the route and entity choices.

A request names an entity by its longest matching name or alias: "Computer Science
BS" names that program, not every program sharing the "Computer Science" alias.
Naming two entities defers, and Jev must select the entity the request names. Besides
the candidates, the entity pick offers "none" and "several": without "several", Jev picked
one of the two offices a mixed request named. When
the request names exactly one entity, Jev's pick of that same entity stands once its
probability reaches 0.50, since the name itself backs it.

A request with several parts, such as "When is the library open today, what's the
Registrar's phone, and when is the next shuttle?", gets one Jev yes/no per detail (hours,
phone, email, location, menu) of each place it names, at most four places, and one per
whole list (events, shuttle times, food served, what's open). Each place with a detail
Jev says yes to is one profile lookup (unsure details are fetched too), and each whole
list is one search. When there are two to four parts on one sure day, they run together
in the first round, and GPT then writes, reviews, and may look up anything a part missed
(`metrics.routing.parts`). A request naming two days, like "hours tomorrow and today's
events", stays with GPT.

Under active routing, code also writes a plain contact answer itself, with no GPT call
at all. Jev says, one yes/no each, whether the request asks for a phone number or an
email address, and whether it adds a purpose or condition ("for transcripts", "after
hours"). When the request plainly asks for phone, email or office location, or how to
reach the office in general, adds no purpose, asks nothing else and names no danger,
and the shared entity facts hold each requested detail as one `known` value with fresh,
caveat-free support, code states them with their evidence (`responseMode`
`exact_facts`). Anything else, including unknown, conflicting or multiple values, is
GPT's to write and review.

Code also writes a plain menu or hours answer, with no GPT writer or checker
(`campus/profile_answers.py`). Jev says, one question each, which diet a menu request
names (none, vegan, vegetarian or another), whether it asks more of the food than what is
served ("what's good", "spicy", a particular dish or ingredient), and whether an hours
request asks about a moment such as "now" or "9 PM". A vegan or vegetarian pick filters
the menu by its published labels before anything is cut. A menu request for one meal, or
one diet, fetches every matching dish (up to 100); only a whole day's menu, which ran to
141 items, keeps a dozen, taken one station at a time so every station shows up. Every
menu section says how many dishes matched and were returned, per station, and whether
the list is `complete`, so nothing reads 12 of 50 as the menu.

When the request asks only what one meal serves (and perhaps its hours), names one sure
meal and at most a vegan or vegetarian filter, code lists the dishes by station under the
meal's published hours (`responseMode` `exact_menu`). For "what's for lunch", a second
Jev call reads each item, "Is `item` a dish someone would choose to eat, rather than
something added to one?", and the answer lists the ones it doesn't rule out and says how
many items the meal has in all; a failed check lists every item. "The full lunch menu"
lists every item without the check. When the request asks only for a place's hours on a
sure day, code states that day's one published schedule, its labeled meal periods, or the
asked meal's period (`exact_hours`). Before either, code checks that the lookup proves
every word: the matched entity, the day, the meal and diet, every matching record
returned whole, one schedule, and fresh official records that publish each stated field
with no caveat beyond the standard label and schedule notes. Anything else, including a
second schedule, placeholder hours or a meal the schedule doesn't label, is GPT's to write
and review.

Contact and every profile section except related, requirements, school, subject and
graduation plans can run directly. Every lookup still uses the ordinary
schema validation, read-only retrieval, context bounds, evidence collection, trace,
and exact-answer checks. Direct results feed GPT synthesis and the existing evidence
review: the exact contact answer skips canonical entities (`retrieval/exact.py`), so
a direct lookup saves GPT's first call, not the answer or its review. Jev never
certifies a factual answer or resolves conflicting evidence.

Under active routing, a danger pick adds a safety block at the top of the answer. When Jev's
top pick is self-harm or other danger, the block shows the 911 guidance, plus 988 for
self-harm. Under it come Public Safety's numbers from their critical-fact records. The
block is written by code, never by a model. GPT is told what the block says, so it
neither repeats the block nor claims its numbers can't be verified. The reviewer sees
the block as a verified prefix, so a reference to it is not an unsupported claim. An answer GPT marks
as urgent safety takes its numbers from the block instead of appending them a second
time. When the answer fails, whether by review, invalid output, a provider error or the
deadline, the student still gets the block alone, with status `partial` and
`metrics.responseMode` `safety_net`. The danger pick is validated on its own, so a
routing answer that is invalid elsewhere, or arrives after the routing deadline, still
flags danger. `metrics.safetyNet` records the pick. Shadow mode only records the pick
in `metrics.routing`.

A search route runs directly when it asks for a whole list of one kind on one day, such
as "What events are happening on campus tomorrow?" or "Which dining halls are open
today?": Jev is ≥0.90 sure of the kind (events, shuttle, campus hours, dining hours or
menu), that a whole list is asked for, and of the day, and the request names no curated
entity. Code then searches that collection for that day with no search words, which only
GPT writes, and filters a menu by a meal Jev is sure of. Otherwise, when only a search or
calculate route is resolved, GPT's first call is constrained to that tool; later calls
regain all tools.

Under active routing, Jev also reads every search result before GPT does, one yes/no per
record: "Does `record` help answer `latest_request`?", with what counts as yes and no.
Results it answers 0.10 or less for are dropped; ones it isn't sure about are kept, and
all are kept when it rules out every one, so GPT decides what the search found. On 121
results from the routing cases it dropped 23 and nothing a real answer used. The check is
billed as routing and budgeted on its own: at most three a turn, never into the writer's
and review's time. A timeout, an invalid answer or its own call limit keeps the results as
they were; budget and accounting errors still stop the turn. `metrics.searchFilter` lists
each check's record count, drops and any reason it kept everything. A contact or profile route that can't run
directly, and uncertain, general and mixed routes, retain the ordinary flow. No new frontend flow or request field is required.

The routing deadline is two seconds, includes preparation, and gives the HTTP
attempt only its remaining allowance. HTTP cancellation covers the entire response
body, not just individual socket reads. There are no automatic retries. Durable
accounting cleanup must finish before further paid work. Routing consumes the
existing 45-second turn allowance; the four-call GPT ceiling and review reserves
remain unchanged, with at most one additional paid routing call before GPT's first call
and at most three search-filter calls after it.

Provider failures, invalid answers, oversized context, expired routing prices, and
late decisions fall back. Accounting failures and exhausted spending limits stop
paid work. Unknown usage retains its reservation and requires reconciliation.

## Setup and migration

1. Apply `migrations/003_routing_accounting.sql` after migrations 001 and 002 using
   the operational database administrator. It only adds `routing` to the operation
   category constraint; balances, row-level security, and campus schemas are unchanged.
2. Set the server-only `BRAIN_TYPESAFE_API_KEY` for the intended environment, or set
   `BRAIN_ROUTING_PROVIDER=openrouter` and `BRAIN_OPENROUTER_API_KEY` to reach Jev
   through OpenRouter. Never expose either key through a `NEXT_PUBLIC_` setting. Keep
   `BRAIN_ROUTING_MODE=off` for rollout.
3. Update any `BRAIN_EXPECTED_CONFIG_HASH` deployment pin using
   `python -m rockygpt_brain.config`. Routing prompts, code, model and prices are
   covered by the hash; the deployment's off/shadow/active mode is logged separately.
4. Run the development evaluation below before enabling active routing.

Pricing is versioned in `release.json`: 42 nanodollars per input token, zero per
output token, matching [TypeSafe's model documentation](https://docs.typesafe.ai/models).
Both providers share the existing monthly and per-turn allowances. API documentation:
[TypeSafe evaluation endpoint](https://docs.typesafe.ai/api).

### Through OpenRouter

`BRAIN_ROUTING_PROVIDER=openrouter` sends the same request to OpenRouter's
[System One endpoint](https://openrouter.ai/docs/api/api-reference/systemone/submit-a-system-one-request),
which forwards it to TypeSafe. OpenRouter lists `typesafe/jev-1.13` at the same
per-token price, so the `release.json` price still applies. Any fee OpenRouter charges
for buying credits is outside the ledger.

The brain requests `typesafe/jev-1.13` and accepts only the reported snapshot
`typesafe/jev-1.13-20260917` as `jev-1.13.0`. That snapshot name comes from
OpenRouter's API reference example, so confirm it on the first paid call. Any other
reported model is billed, recorded in the ledger and falls back as
`routing_model_changed`. The ledger names `openrouter` as the provider and keeps
OpenRouter's generation ID as the receipt. Evaluation reports record `routingProvider`,
because latency through OpenRouter includes an extra hop.

## Evaluation and promotion

Run from the Brain directory, with development credentials and the migration applied:

```sh
.venv/bin/python scripts/evaluate_routing.py --output /tmp/jev-comparison.json
```

This runs the 40 fixed synthetic cases in `cases.json` three times, pairing off and
active modes and alternating their order. Both halves of a pair use the same campus
time. Every paid call goes through the ledger. Missing credentials stop before any
paid work; paid-call errors stop the run. Reports include answers/citations for
review, request costs including Jev, elapsed time, selected route, and dataset version.
These are synthetic evaluation artifacts, not stored student conversations.

Case expectations follow the published identities, last checked against
`dev-profiles-offices-20260924-r3`. A case marked `eligible_direct` must name exactly
one entity; when identities or aliases change, recheck them. Follow-ups keep
`eligible_direct` false even when prior messages resolve the entity.

Review each paired answer against its cited evidence and the original fixture.
Check completeness, current subject, dates, unsupported assertions, and honest
limitations. Create a review file of this form, with every pair ID included:

```json
{
  "reportDigest": "copy gates.reportDigest from the comparison report",
  "pairs": {
    "registrar-phone:0": {"baselinePass": true, "activePass": true}
  }
}
```

Then recompute gates without more paid calls:

```sh
.venv/bin/python scripts/evaluate_routing.py \
  --report /tmp/jev-comparison.json --quality-review /tmp/jev-quality.json \
  --output /tmp/jev-reviewed.json
```

Development requires ≥95% precision among resolved routes, reviewed answer quality,
complete billing, stable paired datasets, and lower mean cost and latency on eligible
direct lookups. Production additionally requires lower overall mean cost and median
latency, and p95 latency no more than 5% above baseline. Review approvals are bound
to the report digest. The runner never changes deployment settings automatically.
If gates fail or validation is incomplete, keep active routing off.

To roll back, set `BRAIN_ROUTING_MODE=off` and restart the Brain. The additive ledger
migration can remain applied; existing routing charges retain their audit history.

## When Jev stops answering

On 2026-09-28 about 1 in 5 routing calls ran out their 2 s window. `scripts/jev_latency.py`
showed the time went to Typesafe's servers, not our questions: a one-question control
call was slow at the same moments, every slow call sat waiting for the reply with
connecting and sending normal, and Typesafe's own `x-envoy-upstream-service-time`
header read 0.6 to 10.5 s. For ten minutes it answered almost nothing. Answered calls
took under 1 s; slow ones took at least 1.46 s.

Each process now pauses Jev after three misses in a row (a timeout on a wait of at least
1 s, no connection, a 429, or a 5xx such as Cloudflare's 520). For the next 60 s every
Jev call, including search filters and dish picks, is refused before it is reserved or
sent, with the fallback reason `routing_paused`, so the turn goes to GPT at once instead
of waiting out the window. The first call after that tries Jev again, and one more miss
pauses it again. `scripts/jev_latency.py --hedge SECONDS` measures whether a copy of a
slow call, sent that many seconds later, answers in time.

## Observability and privacy

`metrics.routing` contains mode, model, version, route, confidence, direct retrieval,
fallback reason, the danger pick, and elapsed milliseconds. `routingCalls` and `routingModelMs` count
actual admitted calls at the paid boundary; `modelCalls` includes both providers.
Direct exact answers identify Jev in the existing `model` field; GPT-written answers
continue identifying GPT. Billing amounts remain operational metadata.

Chat operational summaries now omit question text, message history, answers and
citations. They persist routing decisions and existing text-free usage metadata.
Consequently, new turns do not populate the legacy `/v1/logs` conversation history,
which filters for stored questions. Historical rows are not deleted. Explicitly
submitted feedback remains a separate, unchanged feature.
