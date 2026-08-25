# BASE boundaries

```text
browser -> UI -> Python BRAIN -> OpenAI
                              -> rockygpt-data
```

The UI owns presentation. The Python brain owns two model calls — one to plan
the turn, one to answer it — the lookups the CODE lane makes in between, and
process-local conversation memory.

BASE has no database of its own. Its only outbound calls are the two model
calls and, when a plan names a capability that has an executor, one request to
the data service. A capability without an executor makes no call at all, and a
data service that does not answer costs the lookup, never the turn.
