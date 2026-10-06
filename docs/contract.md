# Brain HTTP contract

The current routes are `/health`, `/readiness`, `/v1/chat`, and the shared office
fact endpoint `/v1/entities/{entity_id}/facts`. The student and
developer apps use the existing message/answer envelope. Campus panels, feedback,
admin APIs, streaming progress, and arbitrary generated explanations are not part
of this office-facts slice.

## Request

`POST /v1/chat` accepts JSON:

```json
{
  "messages": [
    {"role": "user", "content": "Where is the Registrar office?"},
    {"role": "assistant", "content": "The previously returned answer."},
    {"role": "user", "content": "And their phone number?"}
  ],
  "omittedMessages": 0
}
```

- `messages` contains 1–80 messages, oldest first, starting and ending with a user
  message. Each role is `user` or `assistant`; each content is nonblank and at most
  16,000 characters. The whole conversation is at most 48,000 characters.
- `omittedMessages` is an optional integer from 0 to 100,000. It records messages
  removed by the caller. The service also reports its own context truncation to
  the model. Missing history is not evidence that something was never said.
- Unknown JSON fields are rejected. Malformed input returns HTTP 422 with the
  standard failure envelope and sanitized validation details; student input is
  not echoed into those details.
- The encoded HTTP body is limited to 64 KiB before parsing; a larger body returns
  HTTP 413. This byte limit also applies when Unicode text meets the character limits.
  A body taking more than ten seconds to arrive returns HTTP 408 `request_timeout`.

`x-rockygpt-conversation-id` optionally supplies 1–64 letters, digits, hyphens or
underscores. Every turn receives a new request ID. Protected environments require
`x-rockygpt-environment-token` matching `STAGING_SERVICE_TOKEN`; this token is
required in production and optional in development. Production without a configured
token returns 503; a missing or incorrect configured token returns 401. A model
cannot grant authentication or select a spending environment.

The `Accept: text/event-stream` header currently receives ordinary JSON, not SSE.
Clients must inspect the response content type. The diagnostics header adds the
development-only `trace` and `metrics` described below; it does not enable internal logs
or expose prompts.

## Successful envelope

```json
{
  "answer": "The supported answer, with source links.",
  "status": "answered",
  "citations": [{"id": "record-id", "title": "Source title", "url": "https://example.edu/source"}],
  "requestId": "request-id",
  "datasetVersion": "published-dataset-version"
}
```

HTTP 200 indicates a successfully handled request. `status` describes the result:

- `answered`: all rendered requested parts are available.
- `partial`: useful public facts or safety help remain alongside limitations.
- `clarification`: a concrete missing detail or ambiguous office needs resolution.
- `unavailable`: the requested capability or usable facts are absent.

`datasetVersion` identifies the fact publication when facts were read. Citations
refer to original linked evidence, not prior assistant messages. Citation metadata
can include record provenance, freshness, validity dates, and limitations.

A field that a publisher re-observed (email, phones or offices) is cited through a
derived source: the citation `id` is `<record id>:contact_observation:<field>`, its
`collected_at` is that field's re-observation time, and the citation also carries
`original_record_id`, `observation_field` and `original_collected_at` (the record's own
capture, unchanged). A client must not treat such an `id` as a bare record id. An
observation that is not newer than its record is ignored.

Greetings, thanks, "it was a false alarm" and "who are you" get fixed server-written replies with
`status: answered` and no citations. The false-alarm reply repeats the 911/988 numbers.

An emergency reply is `partial`, and its text depends on the kind of emergency. The danger phrase
list names the kind from the group of phrases that matched (`self_harm`, `medical`, `danger`,
`fire`, or `other` for an overdose, a spiked drink, "this is an emergency" and similar). When the
model reports danger that is current, it sets `situation` on its `safety` part, or leaves it null.
One kind gets its own text; several different kinds, or none, get the general `other` text, which
is also the text of every failure and of a reply cut short. The self-harm text leads with 988;
the medical, danger and fire texts lead with 911 and do not mention 988.

Right after the emergency text the code adds the published phone numbers of the campus office for
that kind of emergency (Public Safety (Emergency) for every kind, and the Counseling Center first
for `self_harm`), read through the shared fact reader like every other fact, so each carries its
source. The code does this, not the model, so it also happens on the phrase-list path. It waits at
most three seconds; if the numbers cannot be read in time, the emergency text goes out alone. Then
everything else the turn produced follows: contact details, "which office?" choices, "no matching
office" notes and unsupported or account-limit notes. A data outage on such a turn is still an
error that carries the emergency text. A finish with nothing in it gets HTTP 200 `clarification`
("Which office or service ... do you mean?"), except directly after a safety reply of any kind,
where it repeats that emergency text. An incomplete reply (facts found, then a provider failure)
ends with the general emergency text, as every error does. If the office directory cannot be read
at the start of a turn, the turn fails with retryable `data_unavailable` before any model call.

A refusal says what it can. When office details are shown above it, an unsupported or
account-limit note points to them ("The contact details above are the best way to ask the office
directly"); with no office shown, the unsupported note says what can be looked up and asks which
office. A recalled reply is quoted as plain words, without RockyGPT's own bold marks and links.

The model can ask for `hours` with the usual contact fields. They are the office's linked schedule
records (see [entity-facts.md](entity-facts.md)), rendered by the code as runs of weekdays with the
published sentence, validity window and source. An office with no schedule record answers
"Hours: not published in the available evidence."

Office facts come only from the shared canonical fact reader. Their values and
citations are rendered by code. Every office lookup result is included automatically;
the model's finish tool adds only bounded parts (account limitation, unsupported,
clarification, safety, recall, clock, greeting, thanks, okay, about), with an empty parts list
finalizing the retrieved facts. The model
cannot supply new campus fact values, invented result or citation references, arbitrary SQL,
account actions, or tool names outside the allowed set. Missing values, conflicting
records, and stale/dated evidence remain explicit. Supported public parts can be
answered even when private-account parts cannot.

The latest message stays intact. Earlier context is a contiguous suffix bounded
to 32,000 UTF-8 bytes, with an additional shared 48,000-byte content ceiling; the
model receives separate counts for client and server omissions.

History resolves references and can be quoted as conversation history. It is never
promoted to current campus evidence. The service cannot recover messages that the
caller omitted, and it has no persistent conversation memory or account tools.

## Developer diagnostics

A chat request that carries `x-rockygpt-diagnostics: 1`, sent to a Brain running in
development, gets two more fields in the reply. A production Brain ignores the header, and the
student app never sends it.

- `trace`: one entry per office lookup the model asked for, in order, recorded before the
  lookup runs so a lookup that fails still appears. A lookup the code makes for an emergency
  reply has `tool` `emergency_contacts` (and status `timeout` when it ran out of time); the model
  never asks for it. Each has `tool` (`office_facts`),
  `arguments.query` and `arguments.fields` as the model sent them, `status` (`ok`,
  `ambiguous`, `not_found`, `data_unavailable`, `dataset_changed` or `rejected`),
  `result_count` (how many offices the search returned; an exact match plus partial matches
  counts them all), `office` when one was chosen, and `candidates` (up to five names) with
  `truncated` when several fit. A `not_found` or `data_unavailable` entry has neither.
- `metrics`: `decidedBy` (`model`, `phrase_floor` or `error`), `errorCode` when `decidedBy` is
  `error` (also set when a reply was cut short but kept the lookups it had), `situation` (the kind
  of emergency, on an emergency reply), `modelCalls`,
  `committedNusd` (the turn's model spend in nanodollars: the actual cost once a call settles,
  the held amount while a charge is uncertain), `officesListed` (how many published offices the
  model was shown), and `finish` (the part kinds the model ended with, as it sent them, even
  when the reply was then rejected).

`trace: []` means the engine ran and recorded no lookup. A turn that fails outside the engine (a
crash, or the API-level timeout) sends `metrics.decidedBy: "error"` and no `trace` field at all,
because nothing was kept.

## Development routes

A Brain running in development also serves three read-only routes for the dev UI. Each needs
`x-rockygpt-diagnostics: 1` and returns 404 without it (with a service token configured, the
environment token is checked first). A production Brain does not register them, and they are left
out of `/openapi.json`. Every `*Nusd` and `*_nusd*` number is in nanodollars; `nusdPerDollar` says
how many make a dollar.

- `GET /v1/dev/runtime`: `environment`, `model`, `prices` (the rates the Brain bills per token),
  `nusdPerDollar`, `limits` (turn time and spend, model calls, lookup attempts and results, answer
  size, model input and output, and the request and history bounds), the system `prompt`, the
  `modelInputKeys` the model is given, the `tools` with their JSON schemas, the finish `parts` with
  what each does, and `fixedTexts`: the reply texts the code writes, each with `pickedBy` (who
  chooses it: the model, the danger phrase list, a lookup result, a provider failure, or the code itself) and `when`.
  How facts are worded and the error messages are not in `fixedTexts`.
- `GET /v1/dev/offices`: `datasetVersion`, `identityHash`, `truncated`, and each published office's
  `entityId`, `name` and `aliases`. The ids and pins feed `GET /v1/entities/{id}/facts`.
- `GET /v1/dev/offices/search?q=`: `query`, `datasetVersion`, `identityHash`, `truncated`, the
  `candidates` the search returns (each with `entityId`, `name` and `match`, `exact` or `partial`),
  and `outcome` and `chosen`: what a lookup does with that result, decided by the engine's own
  function. `outcome` is `answers` (one office, named in `chosen`, a list of office names),
  `asks` (several fit, or the result was truncated: the Brain asks which, naming up to five) or
  `not_found`. In a chat the model picks the query from the published list.

## Failure envelope

```json
{
  "error": {
    "code": "budget_exhausted",
    "message": "A safe service explanation.",
    "retryable": false
  },
  "reason": "budget_exhausted",
  "requestId": "request-id"
}
```

Failures distinguish configuration/readiness, provider availability, malformed
model output, execution limits, budget admission, and unavailable campus data.
The server supplies `retryable`; clients should not infer it from HTTP status.
Do not retry a budget limit, invalid configuration, or exhausted turn automatically.
Provider failures never expose raw provider errors, credentials, or student text.
An exhausted allowance includes `error.nextAllowanceAt`, the next monthly boundary
in campus time. It is not a promised recovery time: unresolved reservations and a
paused account can still block requests then. Unexpected model identity or usage
pauses the provider account and returns a non-retryable error.

Chat answers and failures include `X-Request-Id` matching the body request ID. Invalid
requests retain only the `type`, `loc`, and `msg` validation fields.
Chat responses and standard failure envelopes use `Cache-Control: no-store`.

## Probes and operations

`GET /v1/entities/{entity_id}/facts` requires `dataset_version` and `identity_hash`
query parameters from the canonical publication. It uses the same reader as chat;
it does not independently reconcile source values. A changed publication returns
409, an unknown office 404, and unavailable data 503. The endpoint shares ingress
authentication with chat.

If retrieved public facts are usable but a later provider call fails, chat returns
HTTP 200 with `status: partial`, preserves those facts and citations, and adds a
`limitation.code` naming the incomplete operation. This must not be counted as a
fully completed answer. A publication change invalidates the whole turn's fact set.

`GET /health` reports process liveness. `/readiness` verifies configured provider
pricing, spending access, and the published canonical fact read path; it fails
closed when these are unavailable. It makes no paid model call and does not certify
the provider network, model quality, or complete campus data coverage.

The gateway bounds calls, input, output, total turn cost, and elapsed time. It
reserves spending before each provider request, disables automatic retries, and
retains charges when a provider request has an uncertain outcome. The ledger is
shared across workers; memory counters are not spending authority.

For coverage and evaluation limits, see [verification](verification.md). The
[previous contract draft](historical/contract-pre-office-slice.md) is historical;
its unimplemented API and streaming promises do not apply to this runtime.
