# Campus data HTTP contract

The replacement brain receives the base origin through `DATA_URL`.

- Local: `http://127.0.0.1:8100`
- Docker composition: `http://data:8100`
- Staging: `https://rockygpt-data-staging.onrender.com`

When `STAGING_SERVICE_TOKEN` is configured, attach it as
`x-rockygpt-environment-token` to functional data requests. Health and
readiness are public.

## Preferred answer-facing endpoints

| Method and path | Inputs | Purpose |
| --- | --- | --- |
| `GET /v1/search/campus-hours` | `q?`, `day?`, `at?` | Campus facility hours |
| `GET /v1/search/dining-hours` | `q?`, `day?`, `at?` | Dining hours |
| `GET /v1/search/menu` | `q?`, `meal?`, `at?` | Structured menu items |
| `GET /v1/search/contacts` | `q?`, `at?` | Office and person contacts |
| `GET /v1/search/clubs` | `q?`, `at?` | Student clubs |
| `GET /v1/search/events` | `q?`, `at?` | Campus events |
| `GET /v1/search/programs` | `q?`, `at?` | Academic programs |
| `GET /v1/search/academic-dates` | `q?`, `at?` | Academic calendar dates |
| `GET /v1/search/shuttles` | `route?`, `serviceDay?`, `at?` | Shuttle trips |
| `GET /v1/safety-resources` | none | Official emergency and counseling sources |
| `GET /v1/map` | `q?` | Campus locations and optional resolution |

Every search response has this envelope:

```json
{
  "dataset": {
    "id": "dataset identifier",
    "version": "release identifier",
    "activatedAt": "ISO-8601 timestamp"
  },
  "records": []
}
```

Every search record includes:

```json
{
  "source": {
    "sourceId": "stable source identifier",
    "title": "official source title",
    "url": "https://official-source.example/",
    "collectedAt": "optional ISO-8601 timestamp"
  }
}
```

Record-specific fields:

- Campus/dining hours: `name`, `day`, `schedule`, `source`.
- Menu item: `meal`, `station`, `name`, optional `calories`, `vegan`,
  `vegetarian`, `allergens[]`, `source`.
- Contact: `name`, optional `department`, `phone`, `email`, `office`, `source`.
- Club: `name`, optional `category`, `websiteUrl`, `source`.
- Event: `title`, `date`, optional `startTime`, `endTime`, `organizer`,
  `description`, `eventUrl`, `source`.
- Program: `name`, optional `degree`, `programKind`, `school`, `description`,
  `programUrl`, `source`.
- Academic date: `term`, `date`, `title`, optional `description`, `source`.
- Shuttle trip: `route`, `departure`, `arrival`, `stops[]`, `source`.

`programKind`, when present, is one of `major`, `minor`, `certificate`,
`undeclared`, `other`, or `special`. `serviceDay` is `weekday`, `saturday`, or
`sunday`.

## Other public endpoints

The UI may use these directly, but the brain may call them when a structured
search is not sufficient:

- `GET /v1/menu`
- `GET /v1/menu/browse?date=YYYY-MM-DD`
- `GET /v1/dining-hours?date=YYYY-MM-DD`
- `GET /v1/directory`
- `GET /v1/shuttle`
- `GET /v1/data/{artifact}` for `calendar`, `clubs`, `courses`, `events`,
  `hours`, or `programs`

Do not build strongly typed reasoning on `/v1/data/{artifact}`; those payloads
are intentionally heterogeneous.

## Query rules

- At most 32 query entries and an 8,192-character request target.
- Duplicate recognized parameters are rejected.
- `q` is at most 200 characters.
- `day` and `serviceDay` are at most 16 characters.
- `meal` is at most 64 characters.
- `route` is at most 120 characters.
- `at` is at most 64 characters and must be an ISO-8601 instant with `Z` or an
  explicit numeric offset.
- `date` is exactly a real `YYYY-MM-DD` calendar date.
- Do not depend on an exact record count or ordering beyond what the response
  itself states.

## Errors and consistency

Errors use:

```json
{
  "error": {
    "code": "INVALID_REQUEST",
    "message": "Human-readable detail.",
    "retryable": false
  }
}
```

Handle `400 INVALID_REQUEST`, `401 UNAUTHORIZED`, `404 NOT_FOUND`, and retryable
`503 UNAVAILABLE`. Unknown paths and unsupported methods answer `404`.

A single search request is pinned to one active release. Multiple requests can
straddle a release activation, so carry `dataset.version` or the
`X-RockyGPT-Release` response header through internal provenance. There is no
API for asking for an older release.

## Probes

`GET /health` returns process liveness. `GET /readiness` returns HTTP 200 with
`status: ready`, or HTTP 503 with `status: unready` and a `failing` array that
can include `database` or `dataset`.
