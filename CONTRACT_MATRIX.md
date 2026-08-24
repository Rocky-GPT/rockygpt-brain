# Hybrid V1 contract matrix

This matrix freezes V1 ownership before implementation. Existing UI and Brain
contracts remain `/v1`; additive DATA capabilities use `/v2`.

## External Brain API

| Surface | Frozen behavior |
| --- | --- |
| `GET /health` | Public process liveness; no dependency calls. |
| `GET /readiness` | Public, under 3s; reports required DATA/database failures. |
| `POST /v1/chat` | Strict frozen request schema; one complete JSON response; required answer, route, citation/action/suggestion arrays and request ID. |
| `POST /v1/feedback` | Validated idempotent upsert by request ID. |
| `GET /v1/admin/logs` | Admin-only filters, metrics, version watermark, and conditional refresh. |
| `POST /v1/admin/logs/feedback` | Admin-only operator feedback, distinct from student feedback. |
| `GET /v1/admin/logs/stream` | Admin-only SSE `{"type":"change"}` notifications with reconnect-safe heartbeats. |
| Errors/security | Frozen envelopes/status codes, `X-Request-Id`, `Retry-After`, environment token, admin bearer, and signed pseudonymous client rules. |

`spec/brain-api.openapi.yaml` is normative when this summary is incomplete.
Every finite HTTP response carries `X-Request-Id`; only response schemas that
define a body request ID carry it in the body. The SSE handshake carries the
header. Request bodies are capped at 64 KiB. Invalid JSON/scalars, unknown fields,
and schema violations are normalized to HTTP 400; oversized input is HTTP 413.
Implicit framework documentation/OpenAPI routes are disabled.

## DATA V2 common contract

All endpoints below are authenticated by the existing staging environment token
when configured. Every response carries one pinned DATA release and uses typed
records.

```text
outcome
records[]
completeness { state, returned, matched?, limit, truncated, reason? }
appliedFilters
ordering[]
dataset { id, version, activatedAt }
evidence[] { evidenceId, sourceId, title, url, collectedAt? }
```

Invalid requests use HTTP 400. Dependency failures use HTTP 503 and map to
`outcome=unavailable`. A valid authoritative query with zero results is a 200
`empty` or `no_match`, never an error.

## Outcome projection at the Brain boundary

| Internal result | Public Brain result |
| --- | --- |
| authoritative empty or no remaining records, with dataset/source evidence | HTTP 200 `ChatSuccess` with a verified negative answer |
| required DATA unavailable | HTTP 503 `DATASET_UNAVAILABLE` |
| model or required brain service unavailable | HTTP 503 `SERVICE_UNAVAILABLE` |
| unexpected internal failure | HTTP 500 documented error envelope |

## Capability feasibility and ownership

| Capability | Typed input / semantics | Authoritative DATA surface | V1 evidence/completeness | Delivery |
| --- | --- | --- | --- | --- |
| Transportation | route, origin, destination, service date/day, fixed `asOf`, selection `first|next|all|current`, scope `full_day|remaining|at_time` | `POST /v2/capabilities/shuttle/query` | Official transportation source; DATA filters, chronologically sorts, bounds, declares completeness | First vertical slice |
| Dining locations/menu/hours/payment | venue, meal, dietary terms, date/day, fixed `asOf`, operation discriminator | `POST /v2/capabilities/dining/query` | Official dining records; payment prose may delegate to retrieval | Phase 5 |
| Campus hours | venue, date/day, fixed `asOf`, open-at intent | `POST /v2/capabilities/hours/query` | Official schedule record and deterministic status | Phase 5 |
| Events | query, start/end window, category/location, fixed `asOf` | `POST /v2/capabilities/events/query` | Official event records, chronological ordering, bounded completeness | Phase 5 |
| Academic calendar | query, term, start/end window | `POST /v2/capabilities/academic-dates/query` | Official calendar records | Phase 5 |
| Public contacts/staff | query, contact kind/department | `POST /v2/capabilities/contacts/query` | Public official directory only; record-scoped aliases | Phase 5 |
| Clubs | query, category | `POST /v2/capabilities/clubs/query` | Official/community-labelled source and explicit trust tier | Phase 5 |
| Academic programs | query, kind, school | `POST /v2/capabilities/programs/query` | Official catalog/program source | Phase 5 |
| Map lookup | query, location kind; no nearest/navigation promise | `POST /v2/capabilities/map/query` | Known location/relationship evidence | Phase 5 |
| Safety resources | resource kind; emergency facts separate from procedure prose | `POST /v2/capabilities/safety/query` | Exact verified public facts plus official sources | Phase 5 |
| Policy/prose RAG | query, domain filters, `topK` | `POST /v2/retrieve` | Ranked chunks with immutable chunk/document IDs, source locator, trust tier, release/index version, and truncation declaration | Contract in Phase 2; orchestration in Phase 6 |
| General questions | confirmed non-campus input only | No DATA call | No campus citation; model knowledge is not campus evidence | Phase 6 |
| Conversation truth | turn/claim reference and optional current-truth comparison | Brain-owned memory plus relevant current capability | Conversation claims remain distinct from current DATA evidence | Shuttle follow-up first, full Phase 6 |

## Transportation normalization

| User meaning | DATA selection | DATA time scope |
| --- | --- | --- |
| first shuttle on service date | `first` | `full_day` |
| next shuttle after request time | `next` | `remaining` |
| all remaining shuttles | `all` | `remaining` |
| full service-day schedule | `all` | `full_day` |
| currently active trip | `current` | `at_time` |

Destination and origin match canonical stop names and record-owned aliases; they
are never placed in `route`. DATA applies filters before sorting and bounding.
`serviceDay` is derived from `serviceDate`; a supplied mismatch is invalid. DATA
continues serving the existing `/v1/shuttle` contract used by the current UI.
Successful shuttle answers may emit the documented `VIEW_BUS` UI action without
requiring a payload.

## Vertical-slice acceptance

The shuttle slice is complete only when deterministic tests cover first versus
next, destination versus route, service day/date, fixed time, true empty versus
failure, citations resolved from evidence IDs, conversational follow-up, previous
utterance versus current truth, topic switching, log persistence, the frozen
Brain API, and an existing-UI smoke test.
