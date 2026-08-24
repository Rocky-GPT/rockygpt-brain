# Brain contract

Normative. Every capability, present and future, obeys this document. It defines
semantics, not endpoints; `spec/data-api.openapi.yaml` defines transport.

## 1. The invariant

```text
The Listener interprets.
The Worker decides and computes.
The Writer communicates.
```

Each stage is defined as much by what it may not do as by what it does.

| Stage | Produces | May never |
| --- | --- | --- |
| Listener | typed Interpretation | resolve a date, sort, count, compare, select a record, name a field path, choose an endpoint, or decide an answer |
| Worker | discriminated OUT | emit prose, or leave a decision for a later stage |
| Writer | natural language | select, compute, resolve, infer a cause, or introduce a fact absent from OUT |

A stage that has to guess is a contract defect, not a prompt defect.

## 2. Pipeline

```text
USER
 ├─> hard trigger ─────────────────────────┐
 v                                         │
LISTENER ──> Interpretation                │
 v                                         v
ACCESS GATE  (deterministic)          SAFETY GATE
 v                                         │
WORKER  CODE | RAG | MEMORY | composed     │
 v                                         │
OUT (discriminated)                        │
 v                                         │
WRITER <───────── code-assembled block ────┘
 v
FINAL GUARD
 v
USER
```

The safety block is assembled in code and prepended unconditionally. It is not a
lane, it does not compete with other work, and no other task can dilute it.

## 3. Interpretation

The Listener returns exactly one Interpretation per turn.

```text
Interpretation
  scope        institutional | world
  danger       none | medical | fire | weapon | violence | self_harm | other
  tasks        [Task]                      (min 1)

Task
  domain       declared capability domain, or `unknown`
  operation    read | write
  access       public | institutional | personal
  relation     earliest | latest | next | current | all | count | exists | describe
  cardinality  one | many
  references   [Reference]                 (may be empty)
  constraints  { time: TimeReference | null, <domain-declared semantic keys> }
```

### 3.1 Rules binding the Listener

**No derived values.** The Listener never emits a weekday, a resolved date, a
duration in minutes, a sort field, a limit, a rank, or a count. It emits the
reference; the Worker resolves it.

**No predicates.** A `Reference` carries the user's mention verbatim, or an
anaphor. It is never used as a query filter.

```text
Reference
  role      domain-declared role (origin, destination, subject, venue, ...)
  mention   verbatim user text        }  exactly
  anaphor   prior_subject | prior_selection | pending_slot:<name>   }  one
```

**No absent-means-widen.** Every field above is required. There is no default
for any field the Worker reads. An unresolvable or missing field produces
`clarify`, never a broader query. Forgetting narrows execution; it never widens
it.

**Time is named, not computed.**

```text
TimeReference
  now
  named       today | tomorrow | yesterday | this_week | next_week | weekend
  offset      { minutes: signed integer }
  absolute    { date: YYYY-MM-DD }
```

A window is a **resolution product, not an interpretation form**. The Listener
names a point or a stated quantity; the Worker turns that into whatever span the
capability needs. A named time that denotes more than one day compiles to
`no_capability` unless the capability declares `accepts_range`, because
narrowing a span to a single day would be the widening defect inverted: a guess
about which day was meant.

Naming is interpretation. Resolving is computation. The Listener may write
`named: "next monday"`; only the Worker may turn that into a date, and only the
Worker knows the timezone.

**Scope gates lanes.** `scope: institutional` makes model knowledge inadmissible.
Such a task is answerable only from CODE, RAG, or MEMORY; exhausting them yields
an absence. `scope: world` is the only scope the Writer may answer from its own
knowledge, and it may not reference the institution when it does.

### 3.2 Cardinality

`cardinality` is stated by the Listener and independently derived by the Worker
from `relation`:

```text
earliest | latest | next | current | count | exists  ->  one
all | describe                                       ->  many
```

The derived value is authoritative. Disagreement means the interpretation is
internally incoherent, which is a Listener defect rather than a user ambiguity —
asking a reader whether they meant one result or many would be nonsense — so it
yields `error { incoherent_interpretation }`. The stated value exists only to
make that incoherence visible; it is not independent evidence, and it may be
removed if it never fires.

## 4. Gates

### 4.1 Hard trigger — defense in depth

A very small deterministic layer runs on the raw message **before** the Listener
and in parallel with it. It recognises only unmistakable emergency language, and
it may only ever *raise* the danger class, never lower it.

```text
final_danger = max(hard_trigger_class, listener_danger_class)
```

Its purpose is that a single misclassified model field cannot suppress an
emergency response. Its size is the constraint that keeps it honest: it must stay
small enough to read in one screen, and it must not attempt nuance. Questions
*about* a hazard are interpreted normally and do not trip it; only direct
statements of an emergency in progress do. If a candidate trigger requires
disambiguation to avoid false positives, it does not belong in this layer — it
belongs to the Listener.

### 4.2 Safety gate

Runs on `final_danger`. A non-`none` class produces a code-assembled block keyed
to the class. The block is written in code, never generated, and is prepended to
the turn's response regardless of what else the turn contains. Remaining tasks
still execute; they render after the block.

### 4.3 Access and operation gate

Fully deterministic, runs on typed Interpretation fields, and returns before any
DATA access.

```text
operation == write        ->  withheld { policy_write }
access    == personal     ->  withheld { policy_personal }
access    == public and the request targets system credentials
                          ->  withheld { policy_secret }
```

A withheld result is never justified by data availability. Its cause is
independent of whether any record exists, and the Writer receives a
code-assembled sentence, not a description of an empty result.

## 5. Compilation

The Worker converts Interpretation into deterministic operations. Compilation
fails closed at every step.

```text
1. capability lookup   domain -> declared capability, else absent { no_capability }
2. relation check      relation in capability.relations, else absent { no_capability }
3. constraint check    every constraint key declared, else absent { no_capability }
4. time resolution     TimeReference + now + tz -> absolute instant or window
5. entity resolution   mention -> canonical id, else absent { entity_unknown }
6. anaphor resolution  anaphor -> value from state, else clarify { missing }
7. plan                capability + resolved values -> operations
```

**Entity resolution is separate from existence.** A mention that does not resolve
is `entity_unknown`. A resolved entity with no qualifying rows is
`no_qualifying_records`. These are different facts, they render differently, and
no code path may collapse them.

**Time resolution is total.** Every temporal value that reaches an operation or
appears in OUT is an absolute value produced here. No weekday name, relative
phrase, or duration survives compilation.

## 6. Execution and composition

Each operation returns its own typed result. A task may require several.

```text
Task: "what did you say the first one was, and is that still right?"

operations
  1. memory.claim_lookup   { subject, relation }
  2. code.query            { same request, resolved against now }
composition
  compare
```

### 6.1 Tasks are independent; operations compose

A turn renders **one result per task**. Tasks never merge, and a turn may not
return fewer results than it received tasks. `len(results) == len(tasks)` is
asserted before the Writer is called.

Operations *within* a task compose by a fixed precedence:

```text
error > unavailable > withheld > clarify > absent > success
```

The composed result is the maximum of its inputs under that order.

**Composition cannot upgrade.** A composed result is `success` only if every
operation the composed claim depends on is `success`. A fallback lane that
returns weak evidence after a stronger lane returned nothing produces an absence,
not an answer. This rule is what makes lane composition safe to add.

### 6.2 Cardinality assertion

Before OUT is sealed:

```text
cardinality == one  and outcome == success  ->  assert len(records) == 1
cardinality == many and outcome == success  ->  assert len(records) >= 1
```

A violated assertion is a Worker defect and returns `error`, never a longer list.
The Worker is not finished while more records remain than the request denotes.

### 6.3 Extremal relations

Ordering a complete set of authoritative records and taking one end is Worker
work. Where the order is *defined* is a capability question; where the selection
is *computed* is not.

```text
An extremal relation may be computed by the Worker when the capability declares
a total order and the fetched result set is demonstrably complete.
```

Completeness must be asserted positively by the source — untruncated, and
`returned == matched` where both are reported. Silence is not a guarantee. When
completeness cannot be established the Worker reports
`absent { incomplete_source }`; it does not select an extremum from a set it
cannot see all of.

Where the transport has its own selector it is used, because a server-resolved
selection is complete by construction. Where it does not, the Worker fetches the
capability's `max_limit`, verifies completeness, and applies the declared
ordering. Both paths are declaration-driven; neither is a per-domain branch.

A capability that declares no ordering has no extremum to compute, and extremal
relations against it are `no_capability`.

### 6.4 Retrieval support

Retrieval returns ranked chunks with scores. **Quantity is never support.** A
result count cannot establish that anything retrieved bears on the question, and
a lane that treats it as though it can will answer confidently from whatever
ranked highest among irrelevant material.

```text
chunks scoring below the domain's declared floor are discarded
no surviving chunk            ->  absent { no_supporting_evidence }
surviving chunks              ->  success, evidence = surviving chunks only
```

An **undeclared** `evidence_floor` means the domain cannot report `success` at
all. This is the same rule that governs relations: an undeclared guarantee is not
a guarantee, and a domain that ranks its evidence but has no floor cannot
distinguish a supporting document from a returned one. The lane still runs and
still reports what it found; it reports it as
`absent { no_supporting_evidence }` until a floor is measured.

The floor is calibrated, never guessed. Leaving it unset is the conservative
state, not the permissive one.

Citations are drawn from surviving evidence, never from discarded chunks.

## 7. OUT

One discriminated union. The Writer sees nothing else.

```text
success {
  records:     [record]         (see measured zero below)
  cardinality: one | many
  evidence:    [evidence]
  resolved:    { entities, time window, applied constraints }
}

absent {
  cause: entity_unknown
       | no_qualifying_records
       | no_supporting_evidence
       | no_capability
       | out_of_scope
  resolved: { ... }             what was successfully resolved before the absence
}

withheld { cause: policy_write | policy_personal | policy_secret, text }

unavailable { dependency }

clarify { missing: [field], pendingRequest }

error { code }
```

### 7.1 Measured zero

A measurement of zero is a fact. An absence of measurement is not.

```text
relation == count and the source reported a value
    ->  success { value: 0 }            legal, and may be zero

any record relation with no records
    ->  absent { ... }                  never success
```

`success` with an empty `records` array is legal **only** for measurement
relations. This single rule closes the class of failure in which nothing found
becomes a claim about the world.

### 7.2 Absence causes are not interchangeable

| Cause | Means |
| --- | --- |
| `entity_unknown` | the name did not resolve; nothing is claimed about existence |
| `no_qualifying_records` | the entity resolved; no record satisfies the constraints |
| `no_supporting_evidence` | documents were retrieved; none support the question |
| `no_capability` | the system does not hold this kind of information at all |
| `incomplete_source` | records exist, but the set was truncated, so a relation defined over all of them cannot be computed |
| `out_of_scope` | outside the institutional dataset's subject matter |

No cause may be substituted for another, and none may be rendered as a statement
about the world.

## 8. State

Two truths are stored separately and never merged.

```text
conversation truth              current truth
what Rocky asserted             what DATA reports now
  { subject, relation,            re-executed on demand
    value, turnId,
    source: assistant_claim,
    backing: [evidenceId] }
```

`"what did you tell me"` reads conversation truth. `"is that still accurate"`
re-executes and compares. They may disagree, and disagreement is a reportable
result, not an error.

### 8.1 Turn state

```text
TurnState
  claims          [Claim]        conversation truth, as above
  active_subject  entity ref     typed, not prose
  selection       [recordId]     what was actually returned
  evidence        [evidenceId]
  resolved        applied constraints and time window
  pending         PendingRequest | null
```

The Listener receives typed references and opaque ids from this structure. It
does not receive prior assistant prose, and it may not re-derive a subject from
text. Referring expressions become `anaphor`, which the Worker substitutes.

### 8.2 Pending requests

`clarify` stores the incomplete Interpretation.

```text
PendingRequest { interpretation, missing: [field], expires_after: 1 turn }
```

The next turn is first tested as a completion of `pending`. A fragment that fills
a missing slot merges into the stored Interpretation and executes; it is not
interpreted as a standalone question. A message that is clearly a new question
discards `pending`. A message that fills nothing and asks nothing re-asks.

## 9. Writer

### 9.1 Permission

> You may phrase, order, summarise, explain, and combine the facts supplied in
> OUT. You may not create campus facts, numeric values, dates, times, locations,
> identifiers, action results, or citations.

`scope: world` tasks may be answered from the Writer's own knowledge, and may not
assert anything about the institution while doing so.

### 9.2 Obligations per outcome

| Outcome | The Writer must | The Writer must not |
| --- | --- | --- |
| `success` | report the supplied records | add, re-rank, or re-select |
| `absent` | state the supplied cause | describe the world, or substitute another record |
| `withheld` | pass the supplied text through | explain it as missing data |
| `unavailable` | say the source is temporarily unreachable | present it as absence |
| `clarify` | ask only for `missing` | also attempt an answer |
| `error` | say the request could not be completed | speculate about the cause |

### 9.3 Prohibited decisions

The Writer never decides which record is correct, what an empty result means,
what today's date is, which source is relevant, or whether an action occurred. If
any of those remain open when OUT is sealed, the Worker has not finished.

## 10. Final guard

Narrow and mechanical. It checks classes of token, never vocabulary.

```text
times, dates, room and building identifiers, phone numbers,
email addresses, currency amounts, quantities
    ->  must appear in OUT

citation and source identifiers
    ->  must be a subset of OUT evidence ids

action-completion language
    ->  requires an action result in OUT
```

This is deliberately not a support check over prose. A general word-overlap gate
was measured on this system refusing 6 of 14 ordinary questions, and withholding
a correct answer 3 times out of 3; see `rockygpt-evals/corpus/TS_FAILURE_HARVEST.md`
claim 8 and the module note in `rockygpt-brain/src/grounding.ts`. The guard's
value comes from its narrowness. Widening it requires a measurement, not an
argument.

## 11. Declaring a capability

A domain is executable only through a declaration. Anything undeclared is
`absent { no_capability }` — never a widened query, never a nearby endpoint.

```text
Capability
  domain
  transport            path and method
  entity_roles         role -> vocabulary source
  relations            which of earliest|latest|next|current|all|count|exists|describe
  measurement_relations   subset of relations whose success may carry a zero value
  constraints          semantic key -> transport parameter
  day_parameter        transport parameter filled from resolved time, if any
  accepts_range        whether a multi-day window is expressible
  time_references      which TimeReference forms are accepted
  ordering             { field, semantic, kind } — the total order extremal
                       relations are computed against, if one exists
  max_limit            largest page the transport returns, requested whenever
                       the Worker must see a complete set
  absence_causes       which causes this capability can emit
  evidence_floor       relevance floor, for retrieval-backed domains
```

A weekday is never a constraint. It is filled from resolved time through
`day_parameter`, because the Listener may not produce one at all.

Domain meaning lives in the declaration. Which field carries the order, and how
to read it, is stated once per capability; one generic mechanism then serves
every domain that declares one. No relation is ever implemented as a named
special case.

Two consequences worth stating plainly. An extremal relation requires a declared
`ordering` — without one, `latest` is `no_capability` rather than a guess at what
"last" could mean for these records. And a capability that declares no
`entity_roles` cannot accept a mention at all, so free text can never reach it as
a predicate.

## 12. Known dependencies

Stated so they are not mistaken for finished work.

- **Entity vocabulary.** Resolution requires a per-domain vocabulary source.
  Shuttle transport reports `entity_no_match` distinctly, so its roles declare
  `resolution: reported`. The `/v1/search/*` domains do not, and declare
  `unreported`. For those, an empty result to a request carrying a mention is
  read conservatively as `entity_unknown`: the data never asserted that the
  subject does not exist, so neither may the answer. Replacing that reading with
  real resolution is the repair; until then the conservative cause holds.
- **Extremal relations.** `POST /v2/capabilities/shuttle/query` implements
  `first`, `next` and `current` with server-side cardinality; it has no `last`.
  That is not a blocker: `latest` is computed by the Worker under section 6.3.
  A server-side selector remains preferable where one exists, because it is
  complete by construction and needs no completeness check.
- **Relevance floor.** `RetrievedChunk.score` is already returned, but DATA
  reports retrieval success from result count alone. The floor per domain is
  unset and must be measured. Until then section 6.4 keeps the documents lane
  conservative rather than permissive.

## 13. Conformance

The 30-question suite is evidence that these invariants hold. It is not a
specification. No phrase, entity, or expected answer from it appears in
production code or prompts. A repair that references a test case is a contract
defect that has been hidden rather than fixed.
