# DATA calls used by BASE

The brain has exactly two read-only DATA operations:

- `POST /v2/capabilities/shuttle/query` for the CODE shuttle lane.
- `POST /v2/retrieve` for the RAG lane.

`spec/data-api.openapi.yaml` contains the complete transport contract. The brain
passes DATA's returned records and evidence to AI #2 without implementing DATA's
filtering or retrieval logic itself.
