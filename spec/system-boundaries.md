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
 Draft answer + citation/freshness validation
          ▼
 Required evidence review in a separate context ──→ OpenAI Responses
          │ factual support + event scope + food-safety inference check
          ▼
 Accepted answer + server-rendered source links
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
   rejects unverified links and stale current assertions, requires evidence review,
   enforces source scope, and returns observability.
4. `data.py` reads one active dataset version, pinned for the whole turn. It uses
   only allowlisted SQL identifiers and parameterized values in read-only sessions.
5. `prompt.md` defines conversation, evidence, ambiguity, and student-help behavior.
   `review.md` defines the independent evidence check; it cannot access tools or
   the draft model's tool/repair history.

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
On a no-match search, collections with at most 300 date-eligible records also
return their distinct published titles as a discovery index. The model can
select names by meaning, then retrieve the actual record. These names do not
enter the evidence registry and cannot be used as citations on their own.

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
When an optional record website cannot be cited over HTTPS, the record retains
that published field but citations use its existing official source URL. The
model is never asked to repair a URL owned by the retrieval layer.
Freshness and ID validation are deterministic. Every candidate then receives a
separate model review with the complete conversation, exact current-turn evidence,
and search coverage. Each paragraph must have one verdict. Every cited event record
must also be classified as supporting an assertion about the event or a referenced
entity. Code rejects missing/duplicate paragraph decisions and omitted or invented
event-use entries. Non-event citations receive the same factual support review
without redundant ID echoes. This applies to guidance and limitations too.
Each paragraph can cite 50 records, matching the retrieval result limit, so a
published menu list can retain a citation for every item.

Event records and the official Archway Events feed describe activities. Code
overrides a supported verdict when those records are used for general attributes
of a referenced facility or organization. An event venue can therefore remain an
event venue without being promoted into a facility's general location. Source
scope comes from collection/provenance metadata, not student wording or regex
routing. Later search excerpts cannot erase a fuller record already read.

The reviewer separately classifies whether a paragraph infers allergy safety or
relative risk from menu, dietary, or allergen labels. Code rejects that inference
even if the reviewer also marks the paragraph supported. Blank labels cannot
establish lower risk; reporting labels and directing the student to dining staff
for ingredients and cross-contact questions remain valid.

Other semantic support, including negative claims and faithful
schedule interpretation, is checked by the reviewer against full records and
the actual request. A rejected draft can be repaired within the shared budget, but the
revision must pass another review. Code never returns a rejected or unreviewed
revision and does not splice unreviewed paragraph combinations together.

Claim interpretation and source-use classification still depend on a model. This
gate reduces observed errors; it is not a proof of entailment. Realistic and
adversarial evaluations must independently check the resulting answers.
Stale records can explain a limitation. This first checkpoint conservatively
withholds assertions from stale records, including historical facts whose source
has exceeded its freshness SLA.

Database failures become an explicit unavailable tool result, not an empty
search. General guidance works without a database connection. Provider failures,
incomplete output, invalid citations, failed reviews, and exhausted budgets return safe errors;
they do not masquerade as a need for clarification. Conversation state belongs
to each request, so workers and restarts cannot leak or lose hidden context.

The six draft/tool calls reserve their last two calls for synthesis and contract
repair. Every new draft reserves another call for evidence review; unused call
capacity remains available for further repairs and reviews. Every turn is
bounded by eight model calls in total, twelve admitted tool attempts, and the
same 50-second wall-clock deadline. Database I/O stops at 30 seconds, not just
new tool starts. Draft calls reserve eight seconds for review, and HTTP responds
within 52 seconds even if an upstream socket takes longer to clean up. A two-second
connect timeout avoids spending the full model-read timeout on each unreachable
network address. These bounds fail safely rather than extending a student's wait.

Model tool outputs are fed back with their call IDs, including every call in a
multi-call response, following the [official Responses function-calling contract](https://developers.openai.com/api/docs/guides/function-calling#handling-function-calls).
There is no background agent, deployment, new datastore, or scheduled work.
