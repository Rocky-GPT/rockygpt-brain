# BASE design

The complete request path lives in `src/rockygpt_brain/brain.py`:

1. AI #1 returns an `Intent` with one of five lanes.
2. A plain Python `if` statement executes that lane.
3. The lane returns a JSON object.
4. AI #2 writes the final answer from that object.
5. The turn is added to a small in-memory store.

```text
                    Python
          ┌────────────┼────────────┐
          │            │            │
        CODE          RAG        MEMORY / GENERAL / SAFETY
          │            │
    DATA shuttle   DATA retrieve
          └────────────┴────────────┘
                       │
                  result JSON
```

`model.py` owns the two model calls. `data_client.py` owns the two DATA calls.
`memory.py` owns process-local turns and UI logs. `app.py` exposes HTTP. That is
the whole BASE architecture; advanced reliability and security work belongs in
later versions.
