# RockyGPT BASE implementation rule

Keep this brain deliberately small.

The architecture is exactly:

```text
AI #1 UNDERSTAND
-> Python if/elif: CODE | RAG | MEMORY | GENERAL | SAFETY
-> result JSON
-> AI #2 COMMUNICATE
```

Do not add planners, agent loops, repair passes, evidence registries, claim
ledgers, database persistence, complex policy engines, or case-by-case behavior
unless the user explicitly starts a later hardening phase.

The brain may read campus information only through DATA HTTP APIs. Preserve the
existing `/v1` UI response shape.
