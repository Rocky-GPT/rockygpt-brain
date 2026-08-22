# System boundaries

This document describes the other RockyGPT applications only through their
network responsibilities. It intentionally says nothing about how a previous
brain was implemented.

## Topology

```text
browser -> rockygpt-ui -> replacement brain -> OpenAI
                         replacement brain -> rockygpt-data
                         replacement brain -> brain-owned PostgreSQL schema

rockygpt-evals -> replacement brain
rockygpt-evals -> rockygpt-data
rockygpt-infra -> readiness and smoke endpoints
```

No arrow represents a source import or direct access to another application's
database.

## rockygpt-ui

The UI owns the browser experience, a server-owned visitor cookie, browser
request validation, a process-local edge rate limit, and rendering. It calls
the brain server-to-server and expects one complete JSON response rather than a
token stream.

For chat, the UI:

- accepts a message plus up to ten prior turns;
- supplies a conversation ID, pseudonymous visitor ID, timezone, style,
  response mode, and question origin;
- injects a staging environment token on the server when configured;
- may attach a signed pseudonymous abuse identity;
- strips browser cookies, raw source addresses, browser authorization, and
  browser-supplied service credentials;
- displays Markdown answers, up to three valid citations, known UI actions,
  and up to three suggested questions;
- uses the response request ID for feedback and support diagnostics.

Known UI actions are `VIEW_MENU`, `VIEW_BUS`, `VIEW_PRINT`, `VIEW_EVENTS`,
`VIEW_MAP`, and `VIEW_DIRECTORY`. Optional payload keys currently used are
`meal` for menu and `locationKey` for map.

The UI also proxies feedback. Its development-only logs screen calls the
operator endpoints documented in the OpenAPI contract. The UI does not own
durable chat data.

## rockygpt-data

The data service owns collection, normalization, publication, dataset releases,
and the campus-data database schema. It is a read-only HTTP dependency from the
brain's perspective.

For answer generation, prefer the structured `/v1/search/*` endpoints. Each
search response identifies one active dataset and puts an official source on
every record. Use those record sources to produce citations. Do not scrape
campus sites from the brain, import data-service code, query its database, use
development inspector routes, or treat whole-artifact payloads as stable typed
contracts.

## rockygpt-evals

The evaluation application is a black-box HTTP client. It requires only
`BRAIN_URL`, `DATA_URL`, and an optional staging environment token. It has no
permission to inspect service source, use a model key, or connect to either
database.

Deterministic contract checks do not spend model tokens. Answer-quality suites
do spend model tokens and cover grounding, unsupported claims, hours, shuttle
questions, compound questions, crisis handling, conversation continuity, and
follow-up reference resolution.

## rockygpt-infra

Infrastructure deploys each application from its own checkout. The deployment
order is data, then brain, then UI. Readiness is the orchestrator health gate.
Cross-service smoke checks JSON readiness, a data response both directly and
through the UI, malformed chat handling, and the staging access gate without a
model call.

The brain's database credential must be confined to a brain-owned schema. A
staging database is independent and starts without production chat or feedback
records.

## Shared security boundary

When `STAGING_SERVICE_TOKEN` is configured, all functional brain requests must
require this header:

```http
x-rockygpt-environment-token: <token>
```

`/health` and `/readiness` remain unauthenticated for probes.

When `ABUSE_HASH_KEY` is configured, UI and brain share the same value. The UI
sends:

```text
client_key = hex(HMAC-SHA256(ABUSE_HASH_KEY, normalized_source_address))
signature  = hex(HMAC-SHA256(ABUSE_HASH_KEY, client_key))
```

The brain may trust `x-rockygpt-client-key` only after verifying
`x-rockygpt-client-signature` in constant time. It must never durably store the
raw source address or the abuse identity.
