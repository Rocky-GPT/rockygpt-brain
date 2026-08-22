# Acceptance gates

The implementation is a candidate replacement only when every required gate
passes. Tests are black-box and target the new HTTP service; comparison with an
old implementation is neither required nor permitted.

## Contract and robustness

- The OpenAPI document is valid and every runtime route is represented.
- `GET /health` is fast, JSON, and independent of model/data/database health.
- `GET /readiness` is unauthenticated, completes within three seconds, and
  returns non-2xx when a required dependency is unavailable.
- Functional staging routes reject a missing/incorrect environment token with
  `401`, while probes stay public.
- `POST /v1/chat` rejects invalid JSON, JSON scalars, unknown fields, invalid
  roles, invalid identifiers, and over-limit values without terminating the
  process.
- `message` and every history content value are at most 2,000 characters;
  history has at most ten turns.
- Oversized bodies fail with `413`; unsupported paths/methods fail safely.
- Every response has a stable request ID in the body and `X-Request-Id` header.
- Rate-limit responses are `429`, include numeric `Retry-After`, and use the
  documented error envelope.
- The process handles shutdown gracefully and does not abandon accepted
  persistence work.

## Security and privacy

- Signed client identity is trusted only after constant-time HMAC verification;
  unsigned/invalid input falls back to an untrusted request identity.
- Raw IP addresses, raw visitor IDs, raw conversation IDs, secrets, and abuse
  client keys are never stored or written to logs.
- Durable identifiers use a keyed, non-reversible transformation.
- Stored questions, answers, and comments are redacted for student IDs, email,
  phone, payment/SSN-like numbers, and common secrets.
- Question and answer text expires within 30 days.
- Ratings, redacted feedback, and non-text operational metadata expire within
  90 days.
- Admin endpoints independently enforce bearer authentication and are not
  exposed as public production functionality.
- The brain database credential cannot read or write the data-service schema.
- No source import, generated package, or filesystem path links this service to
  another RockyGPT repository.

## Answer quality

- A campus fact such as the Registrar phone number is answered with at least
  one official citation.
- General knowledge such as `2 + 2` can be answered without borrowing a campus
  citation.
- Private, secret, imaginary, or unsupported campus claims are explicitly held
  back rather than invented.
- Pinned `now` and timezone values control hours and shuttle calculations.
- A compound question addresses each requested campus subject or safely says
  which part cannot be verified.
- Follow-ups preserve the referent, ordering, and provenance of earlier turns.
- Active emergencies involving unconsciousness, a current fire, or a weapon
  use route `safety` and include `911`.
- A user expressing suicidal intent uses route `safety` and includes `988`.
- Ordinary informational questions about safety procedures are not
  misclassified as active emergencies.
- Data-service source metadata is carried into citations; fabricated URLs or
  source titles fail the gate.

## UI compatibility

- Chat returns one complete JSON body within the UI's 60-second upstream
  timeout and a nonblank Markdown answer.
- Citation entries have displayable `title` and `url` values.
- UI actions use only the documented action types and string payload values.
- Feedback accepts multiple successive updates for the same request ID and
  answers `{ "success": true }` only after persistence.
- Development log listing returns structured JSON, supports a change watermark,
  and accepts search/origin/route/limit filters.
- The log change stream emits SSE messages with
  `data: {"type":"change"}` and tolerates reconnects.

## Deployment smoke

With `UI_URL`, `BRAIN_URL`, `DATA_URL`, and optional staging credentials:

1. UI, brain, and data readiness return JSON 200.
2. Direct and UI-proxied map calls both contain `locations` arrays.
3. An authenticated `POST /v1/chat` with body `null` returns JSON 400 without a
   model call.
4. Missing staging credentials return 401 on functional brain/data routes.
5. A standalone checkout can install, lint, type-check, test, build, start, and
   answer readiness without any sibling repository.

## Promotion rule

Do not change the existing UI's `BRAIN_URL` until the new service passes all
deterministic gates, the model-backed answer-quality suite, a separate preview
deployment, and a rollback rehearsal.
