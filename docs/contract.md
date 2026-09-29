# Brain contract

What the apps send the Brain and what it sends back. The code is
`src/rockygpt_brain/contract.py`, and `tests/test_contract.py` checks it at the wire.

The student app (rockygpt-ui) and the dev UI (rockygpt-dev) already speak this
shape. It matches the old Brain (commit c00eb91), so either app can point at the
new Brain without changing.

## Asking: `POST /v1/chat`

```json
{
  "messages": [
    {"role": "user", "content": "When is the next shuttle?"}
  ],
  "omittedMessages": 12
}
```

- `messages`: the visible conversation, oldest first. 1 to 80 messages. Each is
  `user` or `assistant`, 1 to 16,000 characters, not blank. It starts and ends with
  a `user` message. The whole conversation is at most 48,000 characters.
- `omittedMessages` (optional, 0 to 100,000): how many earlier messages the app
  left out. The Brain never claims something wasn't said when it simply wasn't sent.
- Any other field is refused with HTTP 422.

Headers:

| Header | Who sends it | What it does |
| --- | --- | --- |
| `Accept: text/event-stream` | student app | Stream progress, then the result (below) |
| `x-rockygpt-environment-token` | both apps | Required when `STAGING_SERVICE_TOKEN` is set |
| `x-rockygpt-diagnostics: 1` | dev UI | Adds `metrics` and `diagnostics`, in development only |

## An answer: HTTP 200

```json
{
  "answer": "The next shuttle leaves at 7:00 AM. [Shuttle Schedule](https://www.ramapo.edu/shuttle/)",
  "status": "answered",
  "citations": [{"id": "…", "title": "Shuttle Schedule", "url": "https://www.ramapo.edu/shuttle/"}],
  "requestId": "…",
  "datasetVersion": "…"
}
```

- `status`: `answered`, `partial`, `clarification` or `unavailable`.
- `answer`: markdown, at most 12,000 characters. Its only links are its citations.
- Each citation has `id`, `title` and an `https://` `url`. It may also have
  `record_title`, `collection`, `collected_at`, `freshness`, `valid_from`,
  `valid_until`, `trust_tier` and `limitations`, which the dev UI's Sources panel shows.

## A failure: any other status

```json
{
  "error": {
    "code": "budget_exhausted",
    "message": "RockyGPT's monthly AI allowance is exhausted. Use the official campus resources.",
    "retryable": false,
    "resetAt": "2026-10-01T04:00:00Z",
    "resources": [{"title": "…", "url": "https://…"}],
    "emergency": {"text": "If you or someone else is in danger, call 911. …", "sources": []}
  },
  "reason": "budget_exhausted",
  "requestId": "…"
}
```

- Every failure carries `emergency` help, except a cancelled request that nobody is
  waiting for.
- `retryable` is true only when trying again right away can help: `busy`,
  `rate_limited`, `model_timeout`, `model_unreachable`, `model_provider_error` and
  `invalid_model_output`.
- Answers and failures have an `X-Request-Id` header, the same as `requestId`.
- A malformed request gets 422, and a missing or wrong token gets 401. Both use
  FastAPI's usual `{"detail": …}` body.

## Streaming

With `Accept: text/event-stream`, the HTTP status is 200 and the body is a stream of
events:

```
event: progress
data: {"stage":"retrieving","subjects":[{"topic":"shuttle","date_from":"2026-09-29"}]}

event: result
data: {"status":200,"body":{…the answer or failure above…}}
```

- `progress` events come in any number. `stage` is one of `connecting`,
  `understanding`, `retrieving`, `calculating`, `composing` or `reviewing`. They also
  carry `subjects` (what the step is about), `operation` (for `calculating`), and
  `draft` (only while `reviewing`). A `safety` block is sent the moment danger is
  read, so the 911 help shows before the answer is ready.
- `result` comes once, last. Its `status` and `body` are exactly what a plain request
  would get.

## Other routes the apps call

These come back in later milestones:

- Student app: `/v1/feedback`, `/v1/menu`, `/v1/menu/browse`, `/v1/dining-hours`,
  `/v1/shuttle`, `/v1/map`, `/v1/directory`, `/v1/entities/{id}/facts` and
  `/v1/data/{artifact}`.
- Dev UI: the development-only routes under `/v1/logs`, `/v1/evals`, `/v1/prompts`,
  `/v1/config`, `/v1/releases`, `/v1/templates`, `/v1/capabilities`,
  `/v1/documents`, `/v1/storage` and `/v1/dev/`.

## What the Brain does so far

Only `/health` and `/v1/chat` exist. Each turn reads the conversation once, checks the
danger phrases, then asks Jev what the question asks ([docs/jev.md](jev.md)).

- A question that names danger ("my friend isn't breathing", "I want to hurt myself"),
  in the danger phrases or by Jev's reading, gets HTTP 200 with status `partial`: the
  safety help first, then a line saying the new Brain can't answer the rest yet. A
  streaming app gets the safety help in a `progress` event right away.
- A request only the student's own account could answer or do ("register me for CMPS
  147", "what are my grades") gets HTTP 200 with status `unavailable`: what RockyGPT
  can't reach, written by code.
- Every other question gets HTTP 503 with code `not_ready` and the emergency help.
- When the spending allowance is used up, the turn gets 429 `budget_exhausted` with
  `resetAt`. When the ledger can't be reached, or a person paused spending, it gets 503
  `accounting_unavailable` or `accounting_paused`. Danger help still comes first.
- A bug in the Brain gets 500 `internal_error` with the emergency help, and the log
  says where it happened.
- In development, `x-rockygpt-diagnostics: 1` adds two things to answers and failures.
  Neither holds the student's words.
  - `metrics`: `responseMode`, `routingCalls`, `dangerPhrase` (the danger the phrase
    list heard, if any), and `jev`. `jev` has Jev's readings, what code `decided` from
    them, the cost and the time (Typesafe's part only), or why Jev was skipped.
  - `diagnostics`: `brain.revision` (the commit), `startedAt`, and `work`, the step
    timeline the dev UI reads. `work` holds when each step began, each Jev call with its
    step, and `endMs`.
