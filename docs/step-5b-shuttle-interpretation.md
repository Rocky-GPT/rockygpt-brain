# Step 5B: model-based shuttle interpretation

Step 5B connects the existing ordered conversation to the preserved Step 5A
`ShuttleRequest` contract. It does not read the database, calculate a schedule,
or generate a final shuttle answer.

## Boundary

The Responses API receives the original `messages` in their original order and
six optional transportation-only strict function tools: `shuttle_next_trips`,
`shuttle_schedule`, `shuttle_availability`, `shuttle_comparison`,
`shuttle_clarification`, and `unsupported_shuttle_request`.

- A function call means transportation was selected. Its arguments are
  immediately converted into the matching Step 5A variant and validated again
  locally. The small operation-specific wire models prevent invalid field
  combinations and exist because strict function schemas do not support the
  Step 5A union's nested `oneOf` or comparison tuple directly; they are not a
  second capability contract.
- No function call means transportation was not selected. The response exposes
  `selected: false`, `request: null`, and preserves the model's normal chat
  answer.
- Ambiguous shuttle language selects a `clarification` request.
- Shuttle questions requiring unavailable facts select an `unsupported`
  request with the contract's reason enum.

The model is not given route names, stops, schedules, sources, or any database
data. The tool schema contains no fields for trip facts. Any route, origin, or
destination mention must occur verbatim in user-authored conversation text;
otherwise local validation rejects the interpretation. This prevents the model
from introducing a campus entity through tool arguments.

The model call uses `store=false` and does not use server conversation state,
`previous_response_id`, a capability registry, or a generic routing layer.

## API inspection shape

Every successful chat response now includes:

```json
{
  "answer": "...",
  "model": "gpt-4o-mini",
  "transportationInterpretation": {
    "selected": true,
    "request": {
      "kind": "query",
      "answer_kind": "trips",
      "query": {
        "day": { "kind": "upcoming" },
        "selection": "next",
        "count": 1,
        "offset": 0,
        "route_mention": null,
        "origin_mention": null,
        "destination_mention": null,
        "time": null
      },
      "show": "both"
    },
    "model": "gpt-4o-mini"
  }
}
```

For a selected shuttle request, `answer` is only an interpretation-stage status
message. Step 5B deliberately does not produce schedule facts or a student-facing
shuttle answer.
