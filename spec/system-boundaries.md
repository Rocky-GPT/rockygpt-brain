# BASE boundaries

```text
browser -> UI -> Python BRAIN -> OpenAI
```

The UI owns presentation. The Python brain owns one model call and
process-local conversation memory. BASE has no database dependency and makes no
outbound calls other than the model call.
