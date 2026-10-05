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
Clients must inspect the response content type. The diagnostics header does not
enable internal logs or expose prompts.

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

Greetings, thanks and "who are you" get fixed server-written replies with
`status: answered` and no citations. When the model reports danger that is current, the
911/988 text comes first (`partial`) and everything else the turn produced follows it:
contact details, "which office?" choices, "no matching office" notes and unsupported or
account-limit notes. A data outage on such a turn is still an error that carries the
emergency text. A finish with nothing in it gets HTTP 200 `clarification` ("Which office or
service ... do you mean?"), except directly after a safety reply, where it repeats the
911/988 text. An incomplete reply (facts found, then a provider failure) ends with the
911/988 text, as every error does. If the office directory cannot be read at the start of a
turn, the turn fails with retryable `data_unavailable` before any model call.

Office facts come only from the shared canonical fact reader. Their values and
citations are rendered by code. Every office lookup result is included automatically;
the model's finish tool adds only bounded parts (account limitation, unsupported,
clarification, safety, recall, clock, greeting, thanks, about), with an empty parts list
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
