# BASE boundaries

```text
browser -> UI -> Python BRAIN -> OpenAI
                              -> Neon (PostgreSQL)
```

The UI owns presentation. The Python brain owns two model calls — one to plan
the turn, one to answer it — the lookups the CODE lane makes in between, and
process-local conversation memory.

The brain reads campus data straight from PostgreSQL through `PostgresData`,
and it owns that connection rather than borrowing one: published artifacts and
the RAG lane's passages arrive over it too, and chat logs are written back
through `PostgresLogStore`. The campus-data service this diagram used to point
at was retired on 2026-08-28. There is no HTTP hop left between a plan and the
records that answer it, and nothing here calls another application's service.

A capability without an executor makes no call at all. A database that does not
answer costs the lookup rather than the turn: the CODE lane turns
`DataUnavailable` into `DatasetUnavailable`, which the student reads as "Rocky
could not reach campus data just now" instead of a failed request.

Conversation memory stays in the process and is lost on restart. Only the chat
logs are durable, and they are awaited before the answer is sent, so a log that
is missing means a recorded failure rather than a silent gap.
