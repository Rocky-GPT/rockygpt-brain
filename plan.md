# RockyGPT Brain: simplest effective architecture and implementation plan

Updated September 11, 2026. This document replaces `plain.md` and incorporates the agreed architecture, model parity, traffic, and budget requirements. It is a plan, not a record of implemented changes or authorization to run paid experiments.

## 1. Decisions and success conditions

**Keep the working FastAPI and PostgreSQL foundation. Build the first version with one tool-using chat model, accurate structured and keyword retrieval, calculations and exact answers in code, reviewed campus explanations, and enforced budgets.** A complete project rebuild is not justified by the current evidence. This document is the roadmap; deferred features are not prerequisites for completing the first working version.

Seven previously listed AI responsibilities do not require seven services, models, or calls per question. Understanding, tool selection, and answer writing belong to the same controller. Required review uses the same chat model in a separate call. Semantic search, automatic repair, additional providers, AI grading, and batch testing are extensions that must earn their place through measured results.

The goal is the least expensive complete system that reliably answers supported Ramapo questions, including complex questions and follow-ups, and handles general help accurately. Fewer calls are useful only if answer quality remains acceptable.

### Confirmed constraints

| Area | Decision |
|---|---|
| Audience | Ramapo College students |
| Production traffic | Approximately 100 questions/day, or 3,000 turns in a 30-day month |
| Development/testing traffic | Tests after relevant changes, repeated difficult cases, and the full acceptance suite before a release; no mandatory daily answer count |
| Optional stress test | Up to 1,000 fresh questions in a day when useful and affordable; not a recurring requirement or automatic job |
| Production AI budget | **$10 per calendar month**, including production model calls and attributable embedding work |
| Development AI budget | **Separate $10 per calendar month**, including experiments, fresh answers, graders, and attributable embedding work |
| Combined allowance | **$20/month; no automatic transfer between environments** |
| Model parity | Same selected chat model and released Brain configuration in both environments; identical embedding configuration if added later |
| Budget exhaustion | Stop paid work in the affected environment; provide supported resources where available |
| Campus information | Existing campus sources only |
| General questions | Stable general knowledge, writing, study assistance, and supported calculations |
| Live web browsing | Disabled in the bot |
| Current scope | Update this plan only; no application changes, deployments, or paid tests |

The user explicitly made 1,000 fresh development answers/day an **optional stress test**. Quality testing is organized around changes, release candidates, and known failure risks. Replaying stored answers or running retrieval checks must still never be counted as fresh Brain answers. Admit an optional stress run only if its conservative cost fits the remaining development allowance after required test work is reserved.

The combined $20 allowance comes from the user's separate-environment budget clarification, not from an architectural cost saving. Neither this clarification nor the optional stress-test choice permits weaker development settings or unmetered paid work.

Correctness means answering every supported part, preserving conditions and exceptions, citing the actual supporting evidence, and identifying genuine gaps. Neither a different model nor an extra reviewer can recover facts absent from the permitted sources. Zero observed errors in a test set cannot guarantee that every future answer will be correct.

## 2. What stays, what changes, and what is deferred

Keep these current foundations:

- FastAPI, the stateless conversation contract, and explicit request limits.
- The published, versioned campus dataset in PostgreSQL.
- Separate ownership of ingestion, Brain, clients, evaluations, and deployment.
- Source identity, citations, safe tool execution, and behavioral regression tests.
- The current branch's implementation and documentation as the baseline. Do not recover or reuse old branches or commits for this work.

Change the expensive or error-prone parts:

- Improve structured filtering and evidence selection before adding more model instructions.
- Use one small controller to choose tools, preserve the question's parts, and answer.
- Render exact supported values with code when the request can be fully satisfied that way.
- Stop requiring an AI evidence review for ordinary general conversation or eligible deterministic responses.
- Keep review for generated campus factual prose initially; narrow it only where independent tests support doing so.
- On a rejected draft, return independently supported exact facts and a clear limitation without another model call in the first build.
- Enforce separate environment budgets before every paid operation.

Defer a specialist-agent network, a separate vector database, a dedicated AI router, an AI reranker, automatic conversation summarization, fine-tuning, and a general workflow framework. Add a component only when a specific measured failure requires it. This follows the principle of starting with simple workflows and adding complexity when it demonstrably helps. [Anthropic's architecture guidance](https://www.anthropic.com/engineering/building-effective-agents)

### First build versus measured extensions

| Build first | Consider later, only with evidence |
|---|---|
| Existing server, database, and one selected chat model | Additional provider integrations after a small comparison is justified |
| Exact filters, bounded keyword search, source identity, applicability, and missing-data checks | Semantic search if labeled failures show useful evidence is missed |
| Calculations and eligible exact responses in code | More domain operations only when actual questions require them |
| Write a campus explanation, review it, then return supported output or a safe fallback | One repair plus recheck if it restores enough complete correct answers to justify the cost |
| One accounting implementation with separate environment balances and credentials | Batch-specific reservation and reconciliation handling when batch is introduced |
| Representative real-time tests, deterministic grading, and human review of meaning | A batch runner and sampled AI grading after measurement justifies them |

The first build must still meet the applicable correctness and completion gates. More partial answers are a possible cost of omitting repair, not an acceptable way to inflate pass rates. If the small version fails a gate, diagnose the cause and add only the remedy supported by evidence before claiming release readiness.

## 3. First-build architecture and exact AI call locations

```mermaid
flowchart TD
    Sources[Existing campus sources] --> Ingest[Collect and validate with code]
    Ingest --> Data[(Versioned campus records and passages)]
    Data --> Index[Existing PostgreSQL keyword indexes]

    Client[Student UI / Dev UI / evaluation runner] --> API[Validate request and admit within environment budget]
    API --> Chat[Same chat model: answer or request tools]
    Chat -->|General answer| Format[Code validation and response formatting]
    Chat -->|Tool request| Tools[Code: structured search, passage search, read, calculate]
    Tools --> Data
    Tools --> Index
    Tools --> Evidence[Evidence with identity, scope, coverage, and dates]
    Evidence -->|Eligible exact answer| Exact[Code assembles supported values]
    Exact --> Format
    Evidence -->|Explain or continue retrieval| Chat
    Chat -->|Generated campus factual answer| Check[Same chat model: focused evidence review]
    Check -->|Pass| Format
    Check -->|Fail| Fallback[Code: supported exact facts or clear limitation]
    Fallback --> Format
    Format --> Client

    Gateway[Shared paid-call gateway and environment ledger] -. Reserve before execution .-> Chat
    Gateway -.-> Check
```

These boxes describe functions in the existing services, not separate deployed agents. There is no repair loop, embedding service, or AI grader in this first-build diagram. Later paid extensions must use the same accounting implementation and preserve environment isolation.

### AI components by stage

| Component | Responsibilities | Stage |
|---|---|---|
| **Chat model and controller** | Understand the request; choose tools; answer; perform required evidence review | First build; one or more bounded calls per turn |
| **Embedding component** | Embed changed passages and semantic search queries | Deferred until retrieval evidence justifies it |

One component can make multiple calls. Grouping responsibilities does not itself reduce billing. Count actual input, output, reasoning, embedding, and grading usage.

### First-build chat-call paths

| Answer type | Expected path | Target chat calls |
|---|---|---:|
| Ordinary general question | Understand and answer in the same call | 1 |
| General question needing calculation | Request calculation, execute in code, explain result | Usually 2 |
| Exact campus fact | Request data, then render eligible fields with code | 1; 2 if a second lookup is needed before rendering |
| Campus explanation | Request evidence, write answer, review factual claims | Usually 3 initially |
| Complex campus question | Additional retrieval/planning and required review, within the total cap | Target 3–4 |
| Failed factual draft | Return eligible exact facts and a limitation using code | No additional model call after the failed review |

An ambiguity can produce a clarification instead of a factual answer. Targets are not measured latency or cost claims. Later, a proven review exemption could reduce a campus explanation to two calls; a proven repair extension could require up to five total calls; semantic search would add query-embedding usage. None is assumed in the first build.

## 4. The conversation controller

### Request and context handling

Keep `POST /v1/chat` with ordered user and assistant messages. Validate roles, sizes, and schema before any paid work. Resolve the current campus date and time in `America/New_York` and assign a request ID.

Use conversation history to interpret follow-ups and corrections. Previous assistant prose is context, never authoritative campus evidence. Retrieve applicable campus facts again as needed.

Do not silently drop conversation turns or add a paid summarizer. If accepted history cannot fit the input and spending bounds, return a clear context-limit response. Later summarization would need its own evidence of benefit and regression coverage.

### Answer or request a tool in one call

Give the selected chat model a compact tool contract. Its first call can answer a general question, request one or more independent lookups, or ask for clarification. There is no preliminary paid topic classifier and no separate planning essay.

Preserve mixed and multi-part requests. For example, a dinner-and-shuttle question may require a date, venue, meal, dietary filter, route, departure, and an explicitly stated travel duration. Record only operational fields and completion statuses, not private reasoning transcripts.

Each requested part ends as answered, requiring clarification, missing evidence, or failed. A missing shuttle detail must not erase a supported menu answer. Compare the final response to the outstanding parts before returning it.

Request independent evidence together when possible. If a second lookup depends on the first result, allow another controller step within the budget. Do not force all complex questions into one call or retrieve the entire campus corpus into the prompt.

### Small, typed tools

Extend the existing search/read tool surface rather than introducing a tool for every question pattern:

- `search_campus`: validated structured filters or passage search, returning source and coverage metadata.
- `read_campus`: bounded reading of selected records or passages by stable IDs.
- A calculation operation, added where useful, for approved arithmetic and schedule comparisons.

The model supplies structured arguments. Code validates them, selects allowlisted operations, and executes parameterized SQL. Model output cannot execute arbitrary SQL, Python, or shell commands.

## 5. Evidence and retrieval

### Improve the representation of existing sources

Publish stable entity identifiers and source-supported aliases for venues, offices, programs, routes, and other recurring entities. Keep aliases in data; avoid question-specific branches in Brain code. Semantic similarity can suggest candidates but cannot prove that two entities are identical.

Expose typed fields when the source supports them: service date, meal, dietary flags, opening intervals, dated exceptions, term, session, route, direction, stop order, and contact attributes. Do not invent missing fields or translate an unknown attribute into a negative value.

Maintain distinct timestamps for source fetch, fact verification, publication, and applicability. Republishing unchanged data must not renew its verification time. Review source-specific freshness rules; a changeable policy or deadline should not become permanently reliable merely because it is classified as static.

Publish coverage metadata for represented entities, dates, and attributes. Distinguish an empty result, uncovered information, a retrieval failure, and a complete authoritative result establishing no matching service. Only the last supports an appropriately scoped negative claim.

Historical evidence can answer historical questions when its applicability matches. Preserve headings, exceptions, document versions, and surrounding qualifications in policy passages.

### Structured retrieval first for exact facts

Use typed, parameterized filters for dates, venues, meals, dietary flags, terms, sessions, routes, and directions. A vegan lunch question should retrieve the correct filtered records rather than ask the model to sort a mixed menu in prose.

Return bounded records with exact field values, evidence IDs, source URLs, applicability, freshness, and coverage. Conflicting records remain visible as conflicts; the model must not silently choose a convenient answer.

### Deferred: semantic search where it improves recall

Begin with PostgreSQL full-text search and measure what it misses. Build the following semantic-search extension only if labeled retrieval failures and a small comparison demonstrate meaningful gains on paraphrases and policy questions. If those failures block acceptance, address them before release; deferral does not waive the retrieval gate. Use `pgvector` in the existing database rather than a separate vector service. [pgvector hybrid-search guidance](https://github.com/pgvector/pgvector#hybrid-search)

`text-embedding-3-small` is the initial embedding candidate, with the same model and dimensions in both environments. Its documented input price is $0.02 per million tokens. Reconfirm pricing before implementation. [Embedding model documentation](https://developers.openai.com/api/docs/models/text-embedding-3-small)

Embed changed passages once, keyed by content hash, chunking version, embedding model, and dimensions. Query embeddings are needed only for semantic retrieval and can be reused for identical normalized queries under the same embedding configuration.

Start with bounded keyword and semantic candidate sets, merge them with a simple rank-based method in code, and return a small evidence set. Initial configurable limits can be 20 candidates per search method and eight final passages, subject to a stricter total token allowance. Preserve relevant qualifiers when trimming.

Use exact vector search first; add an approximate index only if measured database latency warrants it. An embedding failure may fall back to keyword search, with reduced coverage recorded. It must not turn incomplete retrieval into a definitive negative answer.

No separate AI query-expansion service or AI reranker is included. The controller's tool request already provides the search wording.

### Calculations and schedules

Perform time comparisons, next/last departures, durations, counts, sorting, and arithmetic with explicit units in code. Every input must come from retrieved evidence or an explicit user value. Return input references and any assumptions with the result.

For dinner before a shuttle, code can compare service hours and departure times. It cannot invent eating or walking duration. Preserve overnight intervals, daylight-saving transitions, holidays, term/session boundaries, and explicit exceptions.

## 6. Answer construction, checking, and limits

### Exact responses assembled by code

When the request is fully satisfied by validated fields or calculation results, use small reusable formats to produce the answer and citations. Eligible examples include a dated closing time, an office phone number, a scheduled departure, or a filtered menu list.

Require resolved entities, requested attributes, adequate coverage, applicable dates, and no unresolved conflict or policy inference. The selected fields must answer the user's actual request; accurate values for the wrong venue or meal still fail. Model confidence alone never enables this path.

These formats describe reusable data shapes, not hardcoded campus answers. Values, aliases, URLs, and applicable dates come from published data.

### Generated factual explanations

Give the chat model the relevant evidence and ask for an answer with claim-to-evidence references and necessary conditions. Keep the representation compact. Code validates cited IDs, exact fields, dates, calculation references, and permitted URLs.

Initially, require an independent-context review call using the same selected chat model for generated campus factual prose. Give the reviewer the draft claims and their relevant evidence, not the writer's private reasoning or unsupported prior assistant text.

The reviewer checks whether the evidence supports the meaning, scope, conditions, exceptions, and conclusions. Code cannot generally prove that a policy interpretation is faithful. A self-check instruction inside the writing call is not equivalent to this separate review, and a separate review is still fallible.

Remove the review only from answer categories for which held-out evaluation supports the exemption. Start with deterministic exact responses and ordinary general answers. Any further exemption must be based on observable answer/evidence structure and independent results, not a phrase list or the model's confidence score. Keep complex policy interpretation, conflicting evidence, and cross-source inference under review unless their own evidence justifies a change.

**First build: write the explanation, check it, and return it only if supported. If checking fails, return independently renderable supported facts and a limitation, or an unavailable response. There is no automatic repair or recheck call.** Never release rejected prose, splice together unreviewed fragments, or skip a required check to finish within budget.

Measure how often this fallback makes an answerable request incomplete. Keep the existing completion requirements; a smaller implementation does not justify lowering them.

### Deferred: one repair only if it measurably helps

Consider repair when recorded failures show that the necessary evidence was already retrieved but the draft expressed it incorrectly. Missing or contradictory source information requires better retrieval, clarification, or an honest gap, rather than another writing attempt.

Compare the fallback-only path with one repair and a recheck on the same independent evaluation set. Report the number of complete correct answers recovered, new unsupported claims, extra tokens, and latency, as well as overall completion. Add repair only if the improvement justifies its cost and preserves all quality and budget gates.

If introduced, allow at most one repair and review the changed factual content again. Both calls must fit a tested total ceiling of at most five chat calls. Otherwise keep the original safe fallback. A complex turn that already used four calls has no capacity for both repair and recheck under that ceiling.

### General and mixed questions

Ordinary stable general explanations, writing help, and study assistance should usually finish in one call. Use the approved calculation tool when necessary. Mixed answers retain the supported campus portions, general portions, and unresolved portions without attaching unrelated campus citations to general claims.

With bot browsing disabled, do not claim to have verified current external news, prices, laws, or schedules. Explain the limitation when current information matters. Evaluate general knowledge and reasoning separately from campus retrieval.

### Shared execution bounds

| Limit | Initial proposed default |
|---|---:|
| Chat calls per turn | Maximum 4 in the first build, including planning, writing, and required review |
| Retrieval rounds | Maximum 2 |
| Retrieval operations | Maximum 8 |
| Independent retrieval concurrency | Up to 4 |
| Query-embedding calls | 0 in the first build; at most 2 if semantic search is later justified |
| Real-time execution deadline | 30 seconds |
| HTTP deadline | 32 seconds |
| Admitted maximum cost per turn | $0.01, additionally constrained by the environment's remaining budget |
| Automatic SDK retries | Disabled |

These are safety ceilings, not typical usage targets. A $0.01 ceiling does not imply that either environment can afford that average.

Reserve calls, time, and money for required verification before further retrieval. If another lookup would leave insufficient capacity to write and check the answer, use supported exact facts or a clear limitation. The four-call first-build cap can cover two controller/tool-request calls, one answer-writing call, and one review. A future repair extension requires an explicit tested configuration change, applied equally to both environments; caps never expand implicitly.

Do not add AI calls solely to label uncertainty or format errors. Use explicit code statuses for missing evidence, limits, failures, and budget exhaustion.

### Cache the reusable work

Cache public evidence by dataset version, entity, filters, and resolved dates. Recheck freshness and time-dependent applicability before use. Preserve invalidation on source and schema changes.

Reuse suitable frozen public-data snapshots in development. If semantic search is later added, reuse unchanged document embeddings as well. Provider prompt caching may reduce repeated-prefix cost where supported, but measure actual billed cache usage. Do not assume batch and cache discounts stack.

Do not share personalized conversation answers between students. During answer-quality evaluation, bypass any full-answer cache and record whether the model was actually called.

## 7. Model choice and development/production parity

### One selected configuration in both environments

Development must test the model that production will use. Keep the same selected model identity, supported reasoning settings by role, prompt versions, tool schemas, retrieval settings, rendering eligibility, review policy, and execution bounds in the released configuration.

If embeddings are added, their model, dimensions, chunking, and enablement must also match. Development can use a frozen source snapshot for reproducibility; record its identity and run separate smoke checks against the current published dataset.

Proposed changes are tested in development. Promote the exact tested configuration to production as a versioned release. Do not use a weaker development model, a development-only relaxed verifier, or a different reasoning profile to make the bill look smaller.

Use a pinned model snapshot where the provider offers one. Otherwise record the requested alias, returned model identity, provider, configuration hash, and test date; treat observed provider changes as reasons to re-evaluate. Do not assume aliases are immutable.

### Initial model comparison

The current documented Brain default is GPT-5.4; editing this plan does not change it. **GPT-5.6 Luna remains the provisional comparison baseline. Mistral Small 4 is the first cost challenger.** Neither is declared the final production choice by this document.

The next comparison should first establish whether Mistral can match the necessary grounded-answer quality at lower total cost. Gemini 3.1 Flash-Lite, DeepSeek Flash, and GPT-OSS 120B through Groq remain later candidates if the first pair cannot satisfy the accuracy/cost requirements. Do not pay to run every alternative on 1,000 daily questions.

Use the cheapest configuration that passes the independent quality gates and fits the measured workload. Sticker price alone does not account for extra tool rounds, reasoning, incomplete answers, or repairs. A model that cheaply refuses answerable questions does not pass.

### Keep provider integration small

Keep SDK objects inside a thin adapter covering messages, tool calls/results, structured responses, reasoning options, deadlines, normalized usage, and errors. Build the first working path on one provider, then integrate the first challenger for a bounded comparison after the baseline works. Add other adapters only when that comparison requires them.

Provider compatibility must be tested; changing a base URL is not sufficient. Preserve tool-call IDs, structured-output behavior, continuation metadata, and provider-specific handling of reasoning tokens and limits. [Gemini function-calling documentation](https://ai.google.dev/gemini-api/docs/function-calling), [DeepSeek Responses compatibility](https://api-docs.deepseek.com/guides/responses_api/)

Set reasoning explicitly where supported. For Luna, evaluate `none` for straightforward work and `low` for synthesis/review as initial profiles; these are hypotheses, not accepted quality settings. Apply the winning profiles identically in development and production. [Luna model documentation](https://developers.openai.com/api/docs/models/gpt-5.6-luna)

Deploy one primary model initially. Missing source evidence never triggers a more expensive model, and budget exhaustion never triggers a free-provider fallback. A provider change needs its own evaluation before deployment.

## 8. Two independent budgets and what the traffic costs

### Price reference and illustrative costs

Rates below were checked against official documentation on September 11, 2026. They are standard uncached text rates; recheck before paid implementation work.

The estimates assume **6,000 total input tokens and 800 total billable output tokens per completed question across every chat call**, including reasoning where billed. That allowance is an illustration, not a measurement of the current or proposed Brain. Actual evidence size, history, repairs, and model behavior can change it substantially.

| Candidate | Input / output per 1M tokens | Production: 3,000 questions | Optional stress scenario: 30,000 questions | Same optional scenario using eligible batch generation |
|---|---:|---:|---:|---:|
| Mistral Small 4 | $0.15 / $0.60 | $4.14 | $41.40 | $20.70 |
| GPT-5.6 Luna | $0.20 / $1.20 | $6.48 | $64.80 | $32.40 |
| Gemini 3.1 Flash-Lite | $0.25 / $1.50 | $8.10 | $81.00 | $40.50 |
| DeepSeek Flash, currently V4.1 | $0.15–$0.30 / $0.60–$1.20 | $4.14–$8.28 | $41.40–$82.80 | No batch discount assumed |
| GPT-OSS 120B through Groq | $0.15 / $0.60 | $4.14 | $41.40 | $20.70 |

Standard sources: [Mistral pricing](https://docs.mistral.ai/inference/pricing), [OpenAI pricing](https://developers.openai.com/api/docs/pricing), [Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing), [DeepSeek pricing](https://api-docs.deepseek.com/quick_start/pricing/), [Groq model pricing](https://console.groq.com/docs/models).

Batch sources: [Mistral batch processing](https://docs.mistral.ai/studio/batch-processing), [OpenAI pricing](https://developers.openai.com/api/docs/pricing), [Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing), [Groq batch processing](https://console.groq.com/docs/batch). Groq documents that its batch discount does not stack with prompt caching. DeepSeek's range reflects peak/off-peak pricing; plan conservatively using peak pricing until the measured traffic schedule supports another estimate.

The 30,000-question columns illustrate repeating the optional stress volume every day for 30 days; they are **not the regular development workload**. For a single 1,000-question real-time run under these assumptions, Mistral Small 4 would cost $1.38 and Luna $2.16 for generation alone. Those are calculations, not measured quotes for the new Brain.

The table excludes embeddings, separate AI graders, failed attempts beyond the assumed token totals, source infrastructure, and taxes. It also assumes one tested candidate per question. Comparing two models on every question approximately adds their separate costs.

### Explicit feasibility targets

| Environment/workload | Monthly AI cap | Feasibility rule |
|---|---:|---:|
| Production: approximately 3,000 turns/month | $10 | Approximately $0.00333 per completed turn before other production AI work |
| Regular development/testing | Separate $10 | Admit the relevant change-driven tests and release suite within the remaining allowance; no fixed daily turn count |
| Optional stress at 1,000/day for 30 days | Uses the same development $10 | Would allow only approximately $0.000333 per fresh turn before other development AI work; not an operating requirement |

Other AI work comes from the same environment allowance, so the available generation spend is lower. A 31-day month or additional usage also changes the production average. Reserve required development acceptance work before discretionary stress tests or provider experiments.

Production appears plausible under the example, subject to measurement. **Repeating the optional 1,000-answer stress test every day does not fit the example, even at the listed batch prices.** This no longer blocks the normal development workflow, because there is no mandatory daily test count. It also provides no reason to build a batch runner before the core Brain is measured.

First measure compact tool schemas, selective evidence, deterministic responses, fewer unnecessary calls, and actual prefix-cache savings. Consider batch execution later against measured test demand. Never count weaker reasoning, skipped required verification, incomplete answers, or cached-answer replay as equivalent savings.

If required validation cannot fit the remaining development budget, save the checkpoint and defer the release until validation can finish within an available allowance. An optional stress test can be shortened or skipped with its actual scope reported. Increasing either $10 cap requires a user decision. Do not transfer production funds, lower development quality, or declare an incompletely tested release verified.

### Enforce spending before calls

Use separate provider projects/credentials for production and development, plus separate ledger namespaces and grants. Environment identity comes from trusted server/job configuration, never a client-supplied field. Development cannot read production credentials or spend its balance.

Add persistent accounting in a separate operational PostgreSQL schema. Keep the campus retrieval role read-only. Implement one small paid-call gateway with separate environment balances and credentials. Initially it covers the real-time Brain and evaluation calls; any later embeddings, graders, provider experiments, or batch jobs must use that same accounting implementation rather than an independent spending system.

For each paid operation:

1. Determine its environment and applicable calendar-month budget in `America/New_York`.
2. Estimate a conservative provider-aware upper cost from serialized inputs, supported maximum output, reasoning, cache behavior, and current price configuration.
3. Atomically reserve that amount only if settled charges plus outstanding reservations stay within the environment's $10 cap and the operation's limits.
4. Execute with explicit limits and an idempotent local operation identity.
5. Settle from returned provider usage and release only the proven unused reservation.
6. Retain the conservative reservation for ambiguous timeouts/cancellations until usage is reconciled. Unknown usage is not zero usage.

Admission must work across concurrency, processes, and restarts. If the ledger or a usable price configuration is unavailable, paid calls stop. Budget reset must not erase unsettled commitments. Attribute delayed charges consistently with the provider's billing rules and reconcile them.

**If batch is later introduced:** reserve queued work before submission, not when results arrive, and prevent month-boundary evasion. A cancelled or expired job can still have billable completed requests. Never resubmit an uncertain batch as though the first attempt were free. Batch job submission, polling, and reconciliation are deferred implementation work, not first-build requirements.

Use provider usage reconciliation to identify differences and unexpected external spending. Dedicated credentials are necessary because unrelated scripts using shared keys can bypass application accounting. Dashboard alerts alone do not implement a hard cap.

Track usage categories within each environment for diagnosis. Production has its own $10; development has its own $10. The caps cover AI API usage; hosting and applicable taxes remain outside the stated allowance.

At exhaustion, stop new paid calls in that environment. Return a stable, non-retryable `budget_exhausted` result with the reset date and available official resources. Use the existing source catalog and non-AI rendering for that response; do not start an LLM to explain the limit. A failed development budget must not interrupt production.

## 9. Development and evaluation without misleading savings

### Run tests for changes and known risks

| Trigger | Required work |
|---|---|
| Retrieval, calculation, rendering, or accounting code changes | Run the relevant local checks; run fresh affected conversations when answer behavior can change |
| Prompt, model, reasoning, or tool-contract changes | Run a balanced fresh regression subset and repeat relevant difficult cases before assessing the candidate |
| Campus source/data changes | Validate affected records, applicability, and coverage; run affected fresh smoke cases where the change can alter answers |
| Release candidate | Run the complete 200-turn acceptance suite, held-out repetition, and production-like runtime checks before release |
| A new failure or ambiguous result | Add a source-grounded regression case; repeat it enough to investigate variability and include it in future relevant runs |
| No relevant change | No mandatory daily generation quota; spend only when a stated diagnostic or consistency question justifies a run |
| Optional stress test | State the purpose, question mix, freshness, concurrency, and admitted cost before running up to 1,000 fresh turns |

Maintain the full corpus, but start implementation feedback with a small representative subset. Increase fresh coverage as the changed behavior requires it. Repeating a difficult case measures consistency; it does not increase the count of distinct scenarios. Report both distinct-case coverage and repeated-run stability.

The first build uses real-time tests with code-based checks and human assessment of meaning. The minimum release checks remain mandatory even if discretionary testing is reduced to fit the budget.

### Count three kinds of work separately

| Work | What it establishes | How it is counted |
|---|---|---|
| Fresh complete Brain run | Current model behavior with actual tools and required checks | One completed test turn; record every internal call and charge |
| Retrieval, rendering, contract, and calculation checks | Correctness of code and evidence selection | Local/component checks; zero chat-model calls, though uncached semantic queries can incur embedding usage |
| Replay of saved outputs | Regression behavior of downstream code | Replay checks; never counted as fresh model answers |

Count failures, timeouts, unavailable answers to answerable questions, and budget-skipped turns explicitly. Report requested, admitted, completed, and passed counts. A conversation with five user turns is five test turns; an AI grader is additional paid work.

### Grade cheaply where the facts are checkable

Create source-grounded expected facts, acceptable answer conditions, citation IDs, and calculation results. Use code for exact values and structured checks. Do not rely solely on string matching for paraphrased explanations or policy interpretation.

Use human review for critical or disputed factual meaning. Defer AI grading from the first build. If a measured review workload later justifies it, a sampled grader can use the selected chat model, with its own metered cost and calibration against human judgments. It must not automatically run for every test or certify its own model's accuracy. Its grading prompt does not alter the Brain configuration being tested.

No recurring AI question generator is included. Maintain a curated corpus and add paraphrases, failures, and new source cases deliberately. Any future paid question generation belongs to the development budget.

### Deferred: batch only after its full-workload economics are measured

Before implementing a batch runner, project the cost of the measured test mix using the actual provider's eligible batch rates. Include every dependent model round, the required real-time subset, failures, indexing, and any grading. Compare savings with the runner's implementation and maintenance work. If the projection does not make the intended workload affordable, do not build a batch system solely to pursue that workload.

Batch is an optional transport optimization for fresh offline tests. Reuse the same controller, prompts, model settings, tool execution, evidence checks, and per-turn call ceilings. Avoid a second, simpler implementation that bypasses production behavior.

Process independent pending model calls together. Resume each test with its own actual result, execute requested tools through the real code, and submit the next dependent round when needed. Do not prefill ideal tool choices or ideal intermediate answers that the tested model did not produce.

Use a fixed dataset and logical campus time for reproducibility. Batch jobs need an explicit offline wall-clock policy because results are delayed; they cannot run under the live HTTP deadline. Label that difference. Batch results measure quality and cost, not interactive latency, live concurrency, or timeout behavior.

Keep a representative real-time subset using the exact production deadlines and request path. Include its cost in the $10 development cap. Mixed batch/real-time spending will be higher than the all-batch column in section 8.

Implement this runner only after the working real-time Brain and cost profiling show that batching is justified. Its pausing/resuming, tool-round coordination, and delayed-charge accounting are a separate extension, not a prerequisite for first-build acceptance or routine development.

## 10. Implementation sequence and ownership

Phases 1–4 produce and evaluate the smaller working version. Phase 5 is conditional: use only extensions justified by measured failures or costs, then rerun affected gates. A passing baseline can proceed directly to release preparation without implementing Phase 5 features.

The first end-to-end milestone is **one real campus question working through the existing browser, evidence retrieval, answer rendering, citations, and spending records, including its failure paths**. Connect these pieces before expanding retrieval coverage. Use the same controller and contracts throughout the later work.

### Phase 1 — Baseline, configuration, and accounting

- Capture the current branch's code/configuration identity, public contract, representative dataset, and existing regression corpus.
- Add a versioned configuration shared by both environments, with environment-owned credentials and budgets supplied separately.
- Add the paid-call gateway, reservation ledger, and usage/error normalization before paid experiments.
- Measure model calls, tokens by category, retrieval time, writing/review time, fallback frequency, and total cost per complete turn.
- Test budget isolation, admission races, restart recovery, unknown usage, and month boundaries without paid calls. Batch-specific tests belong to a later batch extension.

**Exit:** no paid caller can bypass its environment's accounting, and instrumentation can explain each charge. No model is selected by an unmeasured cost estimate.

### Phase 2 — Connect one complete path, then expand retrieval

Development acceptance completed September 16, 2026. The menu-context regression is fixed and covered by HTTP/SQL/accounting tests and a successful student-browser dinner conversation. See [requirement-by-requirement evidence and remaining later-phase limitations](docs/phase2.md).

#### 2A. One real question from browser to evidence to answer

Choose an office-contact question that the existing published dataset actually supports. Verify the record and source first; this is a representative implementation case, not a hardcoded question or a claim about a particular office's available data.

1. Submit the question through the existing browser interface and `POST /v1/chat`.
2. Validate the request, establish its trusted environment and request ID, and reserve spending capacity before the model call.
3. Let the baseline model request the relevant record through the real tool contract.
4. Execute parameterized retrieval and validate the entity, requested fields, applicability, source identity, coverage, and conflicts.
5. For an eligible exact response, assemble the contact fields and citation in code. Add no model-written explanation or evidence-review call merely to restate those validated fields.
6. Return the answer and its source through the existing response contract and display them in the browser.
7. Settle the actual model usage and connect the request, tool result, evidence IDs, elapsed time, and spending record through the request ID.

Build this path with the reusable controller, tools, accounting, and response formats that later questions will use. Avoid a separate temporary implementation or logic keyed to the example's wording or expected answer.

The same milestone must demonstrate these outcomes, using controlled failure injection where appropriate:

| Case | Required behavior |
|---|---|
| Supported record | Browser shows the correct contact fields and supporting source; actual usage is recorded |
| Missing record or uncovered attribute | Clear missing-information response without invented details |
| Ambiguous entity or conflicting evidence | Clarification or an explicit limitation rather than an unsupported exact answer |
| Database failure | Safe failure response, request ID, and correct accounting for any model work already performed |
| Provider failure or uncertain timeout | Safe failure response; keep uncertain charges reserved until reconciled |
| Exhausted environment budget | Code returns the limit/reset information and available resources; no paid call is made |

**Milestone exit:** the real browser-to-evidence path and its failure handling work, with observable usage and citations. This establishes the first complete path, not release readiness. The remaining answer paths and the full acceptance requirements still apply.

#### 2B. Expand evidence coverage using the same path

- In the Data repository, make the minimum additive improvements to identities, aliases, applicability, coverage, and typed fields needed by the representative cases. Do not require a complete corpus redesign before the first build.
- In Brain, improve parameterized filters, bounded reading, and evidence contracts.
- Add calculation operations and exact rendering for supported data shapes.
- Measure keyword retrieval on labeled paraphrases and policy cases, and record evidence it misses. An embedding comparison is a later targeted remedy for demonstrated misses.
- Use a frozen public-data snapshot for reproducible development checks.

**Exit:** required evidence is retrieved for the labeled cases with correct entities, dates, qualifiers, and coverage. Retrieval can be evaluated independently of answer generation.

### Phase 3 — Extend the same controller to the remaining answer paths

- Extend the controller established in Phase 2A: use one model call to answer, request tools, or clarify, and preserve multi-part tasks without a preliminary classifier.
- Extend the reusable deterministic response formats and add a one-call general path, using calculations where needed.
- Keep generated campus prose under focused review initially.
- On failed review, return only independently renderable supported facts and a limitation. Implement no automatic repair/recheck loop in this version.
- Enforce all call, time, context, and spend ceilings in one place.
- Preserve adversarial, follow-up, correction, partial-answer, and source-attribution behavior while removing superseded scenario-specific machinery.

**Exit:** the simpler engine passes the development corpus using reusable logic, with every AI operation visible in usage metrics.

### Phase 4 — Compare only what is needed

Use the development $10 allowance for this work and all other development AI usage. It is not an extra evaluation allowance.

1. Begin with a small balanced screen, approximately 32 representative turns per candidate, comparing Luna and Mistral Small 4 on the same snapshot.
2. Project the next run's conservative maximum cost before admitting it; reduce the run scope or stop when the remaining allowance cannot cover it.
3. Evaluate the strongest eligible configurations on the full acceptance corpus and held-out portion as funding permits.
4. Evaluate the fallback-only policy against completion requirements. A higher partial-answer rate is a recorded failure when a question was answerable, not evidence that the smaller design passed.
5. Measure complete-turn cost distributions, including failed attempts, required review, reasoning, and any already-justified extension.
6. Add another provider candidate only if needed to address a demonstrated quality or cost failure.

Select first for critical correctness and completeness, then for measured cost at the target traffic, then latency. Hold model, prompts, retrieval, and test data fixed when isolating the effect of one change.

**Exit:** one configuration is selected on evidence and the first-build quality, completion, runtime, and budget gates pass. If funds run out or neither candidate passes, preserve the checkpoint and report an incomplete result. Use the failure diagnosis to consider a targeted Phase 5 extension; an unfinished comparison does not establish an optimal model.

### Phase 5 — Optional extensions that earn their place

- Semantic search: demonstrate that it retrieves necessary evidence missed by the simpler retrieval path, with acceptable indexing and query cost.
- Repair: demonstrate that one repair plus recheck recovers sufficient complete correct answers to justify additional code, latency, and spend. Preserve the same quality gates and model in both environments.
- Further review exemptions: compare independently assessed held-out results before removing a check from an additional answer category.
- Additional providers: investigate only when the initial comparison exposes a specific quality or cost gap.
- Batch testing: first project the complete measured workload at eligible rates, then implement only if the savings justify the runner. Verify both logical behavior and accounting.
- AI grading: add sampled assistance only when its value against human judgment and its cost are measured.
- Optional stress testing: reserve required validation costs first, then admit a clearly scoped run of up to 1,000 fresh turns when useful and affordable. It is not a daily completion requirement.

**Exit for each extension:** it improves an identified deficiency, the resulting whole configuration passes the applicable gates, and the projected workload fits its environment budget. Record unused extensions as deferred, not incomplete core work. If optional stress testing is unaffordable, report the admitted or skipped scope without blocking an otherwise validated release.

### Phase 6 — Finish client validation and prepare release

Keep the existing public request shape and response fields: `answer`, `status`, `citations`, `model`, `requestId`, `datasetVersion`, `elapsedMs`, `trace`, and `metrics`.

Add compatible optional evidence references and operational metrics where useful. Do not expose private reasoning, internal rejection prose, or detailed billing/accounting internals in student answers.

Finish client coverage for all answer paths and verify budget exhaustion, context limits, missing evidence, and provider failure without automatic retry storms. The initial browser integration and basic failure handling already work from Phase 2A. Reuse a simple non-AI resource view; add a small read-only source-catalog endpoint only if the existing client contract cannot supply it. Leave unrelated campus panels and dashboard features outside this work.

Prepare the Brain deployment image, grants, configuration release, and health checks in the appropriate repositories. Keep the prior release available for rollback. A deployment candidate must match the tested model/configuration identity.

**Exit:** clients retain compatibility, the deployment is reviewable, and the exact selected Brain configuration can be released and rolled back. This document does not itself authorize deployment.

### Repository ownership and code quality

| Repository | Planned responsibility |
|---|---|
| `rockygpt-brain` | Controller, tool contracts, evidence validation/rendering, provider boundary, request accounting |
| `rockygpt-data` | Existing-source ingestion, identities, coverage, freshness; changed-passage indexing only if justified later |
| `rockygpt-evals` | Fixtures, relevant fresh runs, deterministic checks and human-review reports; batch transport and AI grading deferred |
| `rockygpt-ui` and `rockygpt-dev` | Compatible chat handling and clear limit/failure states |
| `rockygpt-infra` | Deployment configuration, credentials/grants, operational storage, rollback |

Keep small typed modules for orchestration, retrieval, evidence, providers, and accounting. Prefer pure functions for domain transformations and validators. Do not create a class hierarchy or service boundary solely because this document has a section for a concern.

Version prompts, schemas, price configuration, model settings, and source representations. Keep facts, aliases, source URLs, and source-specific policy metadata in data/configuration. Keep query allowlists, time arithmetic, budget enforcement, and validation in code.

Avoid phrase-specific answers and generated executable code. Explicit safety rules remain appropriate: a missing allergen field is not a safety guarantee, and public evidence does not imply access to a student's private account. Use additive migrations and independent repository builds rather than importing sibling source trees.

## 11. Evaluation and acceptance criteria

### Corpus and evidence

Maintain an initial **200 scored-turn** corpus using current-branch regression cases and source snapshots:

| Category | Turns |
|---|---:|
| Exact campus facts and filters | 50 |
| Complex questions and plans | 40 |
| Follow-ups, corrections, and topic changes | 35 |
| Policy and passage interpretation | 25 |
| General explanations and calculations | 25 |
| Missing data, conflicts, injection, and safety | 25 |

Reserve 80 turns as held-out evidence. Split by conversation/scenario family so near-duplicate follow-ups do not leak between tuning and held-out sets. When a held-out failure is used for tuning, replace it with new held-out coverage before claiming an independent result.

Keep dated expected facts tied to source snapshots and supplement them with current-dataset smoke checks. Fixture-specific answers are allowed in tests, never in production logic.

Cover meal/date/venue/dietary filtering; missing menu versus closed venue; route direction and intermediate stops; overnight schedules and daylight saving; holidays; term/session deadlines; ambiguous entity names; policy conditions and waivers; incomplete multi-part questions; corrections; source and user prompt injection; allergy claims; general calculations; unavailable current external facts; and budget/provider/database failures.

### Release and operating gates

| Measure | Requirement |
|---|---|
| Material unsupported campus claims | Zero observed in acceptance and held-out repetition |
| Citation attribution | Every factual citation supports its actual claim; wrong-record attribution fails |
| Answerable-task completion | At least 98% across the acceptance set |
| Complex-task completion | At least 95%, accounting for every requested part |
| Required-evidence retrieval | At least 99% on the labeled retrieval set |
| Simple-answer latency | Target p95 below 8 seconds on a warm real-time service |
| Complex-answer latency | Target p95 below 25 seconds on a warm real-time service |
| Production feasibility | Measured production mix supports approximately 100 questions/day within its $10/month AI cap |
| Development feasibility | Required change-driven and release validation stays within its separate $10/month AI cap; unfinished required validation blocks release |
| Optional stress testing | Run only within remaining development funds; report actual scope, cost, and distinct/repeated coverage; not a release requirement |
| Environment parity | Released model, reasoning, prompts, tools, retrieval, and review policy match |
| Spending enforcement | Isolation, concurrency, restart, uncertain usage, and boundary tests pass; queued-work tests additionally required if batch is introduced |

Report sample sizes, failed/unrun counts, and the uncertainty of cost projections. These are pilot gates, not a claim of universal correctness or statistically established zero risk.

An unavailable answer passes only when the fixture establishes missing evidence or another legitimate limitation. Refusing an answerable question counts as a failure. Assess meaning against original evidence independently of the production verifier, with manual review of disputed and critical deadline, policy, contact, and safety cases.

### Demonstrate that each extra step helps

For each proposed extension, compare it against the working baseline: keyword versus hybrid retrieval; deterministic versus generated exact answers; fallback-only versus one repair and recheck; generated prose with versus without specific review exemptions; supported reasoning profiles; and real-time versus batch quality. These comparisons are conditional experiments, not a mandatory matrix before the first build can pass. Measure complete-answer accuracy, retrieval coverage, refusal/partial-answer rate, citation correctness, tail latency, and total billed work.

Retain an added component or review step when it addresses meaningful failures. Remove it when independent evidence shows it does not justify its cost or delay. Revalidate the resulting whole configuration, rather than assuming isolated improvements combine safely.

## 12. Rollout, maintenance, and completion

Release behind a server-side configuration switch. Run the held-out suite before exposing student traffic, then begin a small pilot. Avoid duplicating every production request as a paid shadow call under this budget.

Monitor supported-answer completion, unsupported claims, missing-data categories, latency, review failures and fallbacks, actual token usage, and each environment's remaining commitments. Add repair-specific metrics only if repair is introduced. Store operational metrics without saving student conversation text by default. Synthetic evaluation traces can contain detailed evidence under separate retention controls.

Roll back on a critical unsupported claim, incompatible provider/model change, or budget-enforcement failure. Re-evaluate model aliases, prompts, tool schemas, source schema changes, retrieval changes, and review exemptions before promotion. A changed model version must not reach production without corresponding development validation.

Completion requires a measured configuration that answers supported campus and general questions well, preserves model parity, supports the production traffic within $10, and completes required development validation within its separate $10 allowance. Optional daily stress volume and unused roadmap extensions are not completion gates. The priorities are accurate evidence, a small understandable controller, measured use of extra AI calls, and enforceable spending.
