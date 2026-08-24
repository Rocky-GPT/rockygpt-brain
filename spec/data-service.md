# DATA calls used by BASE

The brain uses two kinds of read-only DATA operations:

- Structured `/v1/search/*` endpoints, `/v1/map`, and
  `POST /v2/capabilities/shuttle/query` for CODE.
- `POST /v2/retrieve` for the RAG lane.

`spec/data-api.openapi.yaml` contains the complete transport contract. The brain
passes DATA's returned records and evidence to AI #2 without implementing DATA's
filtering or retrieval logic itself.
