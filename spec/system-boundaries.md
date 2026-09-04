# Brain architecture

```text
Student UI / Dev UI / CLI
          │ complete ordered messages
          ▼
 HTTP validation + limits
          ▼
 One bounded model/tool loop ──→ OpenAI Responses
          │ search_campus / read_campus
          ▼
 Read-only published campus data ──→ PostgreSQL rockygpt_v2
          │ record IDs + facts + provenance + validity + freshness
          ▼
 Validated answer parts + server-rendered source links
```

The product needs accurate natural-language interpretation, combined answers,
follow-ups, and authoritative campus facts. A separate classifier does not help
answer those questions: it adds a lossy boundary before the model can see facts.
Instead, one model receives full history and chooses its read-only searches.
It can refine queries, read details, answer several subjects, or ask a focused
clarification in the same bounded loop.

## Five responsibilities

1. `contracts.py` validates ordered user/assistant messages and structured answer
   parts. No user can supply system instructions, tool results, or trusted state.
2. `api/app.py` owns HTTP errors, optional environment protection, campus time,
   per-process concurrency, a 52-second HTTP deadline, and dependency cleanup.
   Database and model timeouts share the remaining execution budget. `limits.py` bounds bodies
   before JSON parsing.
3. `engine.py` runs the model and tools, enforces budgets, resolves source IDs,
   rejects unverified links and stale current assertions, and returns observability.
4. `data.py` reads one active dataset version, pinned for the whole turn. It uses
   only allowlisted SQL identifiers and parameterized values in read-only sessions.
5. `prompt.md` defines conversation, evidence, ambiguity, and student-help behavior.

## Retrieval

Two tools are enough. `search_campus` takes a collection, ordinary keyword query,
campus-local date range, and bounded result limit. A start date is required for
menus, hours, shuttle schedules, and events; dates are optional for other
collections. `read_campus` accepts
only IDs actually returned earlier in the same turn. No tool accepts SQL, URLs,
code, student credentials, or write actions.

Small structured collections and published artifacts are ranked by distinctive
keyword overlap, weighting names more strongly than body text. No user phrases
or intent regexes are encoded. Collection descriptions expose their coverage,
including concise verified facts and official action links. The model chooses
collections and supplies semantic reformulations.
Document chunks use PostgreSQL full-text search and existing lexical indexes;
reading expands nearby chunks only within the same source page and heading.
Program requirements are separate records because whole catalog pages are too
large to return reliably. Results disclose truncation and total matches.

Official primary and secondary sources are eligible; community sources are not.
Every evidence record includes its source, collection timestamp, freshness SLA
result, and applicable date range. A source's frozen publication quality flag is
not a substitute for computing its age at request time. Static timetables are
marked as schedules, with live delays and holiday operations unknown. Daily menu
and event filtering uses America/New_York, not the server's timezone. Seasonal
hours override ordinary weekly rows on their dates. Calendar history is retained
when the student asks about a specific term.

Schedule fields preserve departure/arrival locations and stop order. Dining meal
labels are included only when the published artifact exactly matches the venue,
day, validity, and complete interval sequence. The model receives the current
weekday and calendar-week boundaries as well as the campus timestamp.

Current dataset coverage does not imply exhaustive campus knowledge. In the
September 4 audit, library hours and map data were not published, and menus
covered only selected dates. Missing records cannot prove closure or nonexistence.
The Brain supplies the parts it can support and states the remaining limitation.

## Trust, failure, and limits

Sources and prior conversation text are data, never executable instructions.
Campus assertions require evidence IDs retrieved during the current turn; the
server generates source URLs instead of accepting links invented by the model.
Freshness and ID validation are deterministic. Semantic support remains a model
responsibility: a valid citation alone cannot prove that the cited text entails
an answer. Realistic and adversarial evaluations must check that relationship.
Stale records can explain a limitation. This first checkpoint conservatively
withholds assertions from stale records, including historical facts whose source
has exceeded its freshness SLA.

Database failures become an explicit unavailable tool result, not an empty
search. General guidance works without a database connection. Provider failures,
incomplete output, invalid citations, and exhausted budgets return safe errors;
they do not masquerade as a need for clarification. Conversation state belongs
to each request, so workers and restarts cannot leak or lose hidden context.

Model tool outputs are fed back with their call IDs, including every call in a
multi-call response, following the [official Responses function-calling contract](https://developers.openai.com/api/docs/guides/function-calling#handling-function-calls).
There is no background agent, deployment, new datastore, or scheduled work.
