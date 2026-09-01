# Step 5B checkpoint: model-based shuttle interpretation

This document records the completed Step 5B checkpoint. The completed Step 5
runtime now continues from this interpretation into deterministic trusted-data
execution and a grounded final answer.

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
- Full schedules, availability checks, and comparisons use a bounded-day wire
  shape, so the model cannot express an `all` + `upcoming` combination.
  Availability without an explicit day is converted deterministically to
  `today`, matching the Step 5A default-day rule.
- If the model still emits malformed JSON, an unknown operation, multiple
  calls, an invented mention, or any locally invalid argument combination, the
  API returns a typed `clarification` with reason `interpretation_failure`.
  Model-generated interpretation errors do not become HTTP 5xx responses.

The model is not given route names, stops, schedules, sources, or any database
data. The tool schema contains no fields for trip facts. Any route, origin, or
destination mention must occur verbatim in user-authored conversation text;
otherwise local validation rejects the interpretation. This prevents the model
from introducing a campus entity through tool arguments.

`route_mention` is reserved for a route/service name. A requested place is an
`origin_mention` or `destination_mention`; for example, `to Ridgewood` is a
destination mention. The trusted active data has station stops, so an
arrival-at-station request is representable. It has no Ridgewood route or stop,
so later deterministic execution must produce a no-match rather than reinterpret
Ridgewood as a route or invent a trip.

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

The completed Step 5 response also includes `transportationResult` and
`transportationProvenance`; `answer` is rendered only from those deterministic
facts. Non-transportation responses retain the model's normal answer and expose
null execution fields.
