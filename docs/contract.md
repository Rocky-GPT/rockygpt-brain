# Brain contract

What the apps send the Brain and what it sends back. The code is
`src/rockygpt_brain/contract.py`, and `tests/test_chat_route.py` checks it at the wire.

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
| `x-rockygpt-conversation-id` | either app (optional) | Names the conversation, 1 to 64 letters, digits, `-` or `_`; without one the turn starts a new conversation |
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
  `rate_limited`, `model_timeout`, `model_unreachable`, `model_provider_error`,
  `invalid_model_output` and `data_unavailable` (the campus data can't be read).
- Answers and failures have an `X-Request-Id` header, the same as `requestId`.
- A malformed request gets 422 `invalid_request` in this same shape, plus FastAPI's
  `detail` list. Each `detail` item keeps `type`, `loc` and `msg` (both apps read them,
  for example `extra_forbidden`) and never the student's words. A body that can't be
  read at all (bad UTF-8, absurd nesting) gets the same 422. A missing or wrong token
  gets 401 with FastAPI's usual `{"detail": …}` body.

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

Only `/health`, `/readiness` and `/v1/chat` exist, and the Brain is being rebuilt one
step at a time. Everything below the request rules above is not built yet; earlier
versions of this document described the code before the restart (commit 3dec0bd).

`/v1/chat` checks the request, makes a `Turn` (`turn.py`: the latest student message,
a conversation id, a new request id and the campus time in `America/New_York`), and then
looks at the message once (`boundary.py`, code only, no model):

- Immediate danger (a person collapsed or not breathing, self-harm, an attack or weapon,
  fire or gas) gets HTTP 200 with status `partial`: the fixed 911/988 help, written by code.
  Danger wins over everything else.
- The student's own account or an action for them ("show me my grades", "register me for
  CS 450") gets HTTP 200 with status `unavailable`: a fixed line saying RockyGPT can't see
  or change it. "How do I check my grades?" is a general question and goes on.
- Anything else answers 503 `not_ready`. That failure has no `emergency` help yet.

Both 200 replies have `citations: []`. Streaming is not built: a request that asks for
`text/event-stream` gets the same plain JSON. The phrases are a floor, not a reading of the
message: unusual wording can get past them. The conversation id is the
`x-rockygpt-conversation-id` header when one is sent (the apps send none yet), else a new one.
