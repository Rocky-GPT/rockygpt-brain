# BASE boundaries

```text
browser -> UI -> Python BRAIN -> OpenAI
```

The UI owns presentation. The Python brain owns two model calls — one to plan
the turn, one to answer it — and process-local conversation memory. BASE has no
database dependency and makes no outbound calls other than those two.

Plans are recorded, not executed. Nothing in BASE reaches a data service.
