# Design

```text
    the question
         │
         ▼
    BRAIN #1        understand it — what is it actually asking?
         │          the only stage that is shown the conversation
         ▼
    BRAIN #2        plan it — what should be done about that?
         │          given the resolved question and nothing else, and
         │          checked against the registry before it goes on
         ▼
    PYTHON          normalize the plan, then run the lane it names
         │          GENERAL answers from what the model knows, or from the
         │          web when the answer has a shelf life; CODE looks it up,
         │          using one of the registry's implemented capabilities
         ▼
    BRAIN #3        translate what came back into an answer
```

Three brains and a lane, run in that order, each turning the one before it into
something else: words into an understanding, an understanding into a plan, a
plan into rows, rows into prose.

BRAIN #3 comes last because it writes from what PYTHON produced, on every lane:
it is handed `answerFrom` — `campusData` with the rows a lookup returned, or
`ownKnowledge` — and never has to infer what to do from a field that is not
there.

The trace and BRAIN #3 are told different things on purpose. The trace says
exactly why a lane did not run, because that is for a person debugging. BRAIN
#3 is told only where to answer from, because a model told its lookup failed
apologises for the capability rather than answering the question.

A resolution is checked before it is planned from. BRAIN #2 sees `resolved`
and nothing else, which holds only while `resolved` really stands on its own —
so a reading that says it used the conversation and then shows no sign of it
ends the turn at that seam, rather than after three more stages have built on
it.

A test enforces this rather than leaving it to memory: any module that sends
`instructions=` must load them with `beside(__file__)`. A new stage that
inlines
its prompt as a Python string fails the suite. `_unresolved` in `brain.py`
holds the two tests.

## Modules

```text
prompt.py             reads a prompt.md, minus the notes above its rule
brain/                the four stages, in the order they run
  brain.py            the lifecycle — what calls what, and what fails the turn
  values.py           the constrained scalars every stage schema is built from
  understand/         BRAIN #1: run, prompt.md, schema, validate
  plan/               BRAIN #2: run, prompt.md, schema, validate
  execute/            PYTHON: run (safety, then the lane), schema
  write/              BRAIN #3: run, prompt.md, schema
lanes/                where an answer comes from
  code/               a capability lookup, then the plan's operation
  rag/                cited passages from the campus document index
  general/            what the model knows, or a web search
capabilities/         what Rocky can look up
  registry.py         the only such list, and an entry needs its code
  filters.py          enum | entity | date | instant | text contracts
  narrow.py           keeping the rows a free-text filter actually meant
  transportation/     shuttle and bus departures, and temporal ordering
  dining/             today's menu items and dietary facts
  events/             upcoming event search and chronological filtering
  hours/              campus and dining opening hours
  courses/            course catalog search
  calendar/           academic dates, terms, breaks, deadlines
  clubs/              student organizations and Greek life
  directory/          public contacts for people, departments, offices
  locations/          buildings, rooms, parking, map links
  programs/           degree programs, majors, minors, certificates
safety/               the concerns, what to do about each, and applying them
context/              the conversation, and the record of it
services/             the outbound calls
  openai.py           the one way this brain talks to a model
  data.py             the campus data service
  web/                client and prompt.md — a search is a model call too
  rag/                the campus document retriever
api/                  the HTTP surface
config.py             settings from .env
```

Each stage directory holds the same four things, so the same question is always
asked in the same place: `run.py` makes the call, `prompt.py` is what the model
is told, `schema.py` is what comes back, `validate.py` decides whether it can
be used. A stage without one of those does not have the file — `execute` has no
`prompt.py` because it calls no model, and no `validate.py` because a plan is
checked before it runs, not after.

Every instruction sent to a model is a `prompt.md` — the three stages and the
web search alike — because they are prose and the highest-risk text here. A
paragraph added to the planning instruction has twice moved lane
routing on questions it was not about, and that change should diff as sentences
rather than as a quoted string — and a markdown file cannot quietly acquire an
f-string or a conditional, which is how a prompt starts behaving differently
from what the file appears to say.

Each file is the whole instruction and nothing else — no header, no notes, no
section stripped on the way out. What it reads as is byte for byte what the
model is sent, which is the same reason these are not Python: any rule for
subtracting part of a file is one more difference between what it says and what
it does. Notes for whoever edits one are under "Before editing a prompt" below,
where they cannot be sent by construction.

A directory exists only when there is code for it. The RAG lane and the ten
current CODE capability directories therefore correspond to implementations,
not roadmap placeholders. This is exactly the failure the registry rule below
exists to prevent.

## The plan

A plan is a lane, and whatever that lane needs.

```text
safety      what is wrong with the question, and empty when nothing is:
            emergency | privacy | secret | harmful
lane        CODE | RAG | GENERAL
capability  CODE: which lookup, from the registry
filters     CODE: field/value pairs, each checked against that field's
            enum | entity | date | instant | text FilterSpec
operation   CODE: orderBy + direction, select, limit, count, compare
topic       RAG: what to find in the documents
freshness   GENERAL: `stable` answers from what the model knows, `current`
            searches the web
query       GENERAL: what a `current` question means to search for, in
            words, with no date in it
effectiveQuery
            what Python actually searched: `query` plus today's date. Set by
            `validate`, never by the planner, and never on the response schema
```

Two things that look like lanes are not, because a lane says where an answer
lives and neither of them is a place.

There is no MEMORY lane. A question about the conversation is answered from the
conversation, and routing to such a lane would have meant choosing it in BRAIN
#2, the one stage denied sight of the conversation. It is `usesContext` on
BRAIN #1 instead.

There is no SAFETY lane either. It was one, and it had no executor, so the one
turn that must never fail was the only one guaranteed to — a question routed
there came back as "Rocky cannot look that up yet." It is `safety` on the plan
instead: a list, because a question can be more than one of these at once, and
Python acts on every entry before any lane runs. What it does about each is
`CONCERNS` in `execute.py`, written in Python because that is the part that
must not vary with how the question was phrased.

The vocabulary is fixed and small on purpose. It grows by capability — a new
kind of thing Rocky can look up — and never by question. A question Rocky has
not seen before should need no code, because the plan for it is the same
capability with different filters and a different sort.

Filters are a list of pairs rather than a map because a strict response schema
cannot describe an object with arbitrary keys. `Plan.filter_values` gives back
the map, and `Plan.summary` is the shape a human reads in the log. The generic
wire shape does not make the values generic: `Capability.filters` maps every
published name to a `FilterSpec`, and Python rejects a value that cannot belong
to that type before any campus lookup runs.

Finite domain concepts are enums. Open-world things are entities: BRAIN #2
keeps the mention a student used, then the capability's normalization hook
resolves it to dataset identity. The model is never asked to invent an ID.
Only genuinely open-ended language is `text`.

## The trace

`brainTrace` carries eight entries: `question`, `memory`, `understanding`,
`context`, `plan`, `normalizedPlan`, `execution`, `answer`.

`question` is what was asked and nothing else. Everything it was read against
is `memory` beside it — the clock, the earlier turns, the modes the client
asked for. The clock is there rather than in the question because the browser
never sends a time and the proxy would drop one, and because `today` means
nothing until an instant fixes it.

`understanding` is what BRAIN #1 made of the question; `context` is what it had
to borrow from the conversation to get there, and is empty unless BRAIN #1 says
the question needed it.

`plan` is exactly BRAIN #2's semantic output. `normalizedPlan` is what Python
actually executes after enum canonicalization, time resolution, and any
capability entity resolution. Keeping both is what makes a bad model choice
different from a bad resolver visible in the inspector.

The dev inspector orders them for reading rather than for the wire: `context`,
BRAIN #1, BRAIN #2, PYTHON, then `memory` and the leftovers shut by default,
with `question` and `answer` as the header and footer because prose reads badly
as a one-line JSON string.

The `execution` stage leads with `answerFrom` — the same value BRAIN #3 was
handed, so the handoff is visible — and then takes one of three shapes, which
is what says what happened: `{"note": ...}` did not run, `{"count": n}` ran and
counted, `{"results": [...]}` ran and listed. It carries no lane — the plan
stage already names it — and no `ran` flag, because the shape is the flag.

The distinction that matters is `{"results": []}` against `{"note": ...}`:
"Rocky looked and there is nothing" against "Rocky never looked". Those are
different answers, and the empty list is what says so.

## What Python contributes

The current date and time, in the campus timezone, because the model does not
know it — and so the resolution of every time word a plan carries.

The date on every `current` search. BRAIN #2 writes what the search means;
Python appends the server clock's date, with no condition on it — and strips
any date the planner wrote anyway, so the query carries exactly one and it is
the server's. Left to the planner the date appeared four times in five, which
is the worst possible rate: often enough to look correct, rarely enough that
the turns it missed came back years stale and looked like nothing in
particular.

The citations, when the answer came off the open web. `title` is the URL host,
because the search returns no page title and the host is the part a reader
recognises anyway. Only the web lane produces them: campus rows are Rocky's own
records and carry no page to point at.

Then the decision about whether a plan runs at all. The registry is the whole
authority: a plan naming a capability, a filter, a sort field, or a comparison
field that is not listed is rejected with a reason. A listed filter whose value
has the wrong type is rejected at the same seam; `meal="tomorrow"` is not
silently dated, dropped, or sent to DATA.

## Where it grows

Two places, and they are different.

A new thing Rocky can look up gets a capability directory containing its
executor and boundary normalization, a required entry in `capabilities/registry.py`,
and one method on `DataPort`. The registry cannot advertise a capability
without executable code.

Every executor translates the plan's published filter names into the data
service's request, then the CODE lane applies `orderBy`, `select`, `limit`, and
`count` over the records that came back. `shuttle` is the clearest example: the
data service has its own selection vocabulary — `first`, `next`, `current` —
while the executor asks for the full bounded set and leaves selection to the
generic operation. That keeps service-specific verbs out of a plan.

## Mentions in, canonical ids out

BRAIN #2 writes what the question said — `subject: "CS"`, `term: "Fall 2026"` —
and never a dataset identifier. `capabilities/entities.py` turns one mention
into identity, the same way for every capability, strongest evidence first:

    1. the canonical id or code, exactly
    2. the canonical name, exactly
    3. an alias the data gives the entity
    4. an abbreviation of the name, where only one entity claims it
    5. anything else, or several matches at the deciding step: refuse

The first step that matches anything decides, so weaker evidence never
overrules stronger. Two entities matching equally well raise `EntityAmbiguous`
and the turn stops; nothing weighs how many rows an entity has, because the
more popular reading of an abbreviation is a guess wearing a statistic.
`EntityNotFound` is the separate case a caller may still handle — a code the
catalogue has no name for is a weaker handle, not a wrong one.

**Aliases belong to the data.** `CS` means `CMPS` because
`src/reference/course-subject-aliases.json` in rockygpt-data says so, merged
into the subject catalogue at ingestion and served from
`/v1/search/course-subjects`. It is not in a prompt, not in the planner, and
not in the capability: a short form is known where the names are known, and one
buried in a capability is invisible to every other capability needing it.
`CS` and `CNST` both abbreviate to CS, so without that alias the mention is
ambiguous and correctly refused — the alias is what makes it answerable.

BRAIN #1 stays out of this. It normalizes language — spelling, punctuation,
phrasing — and knows nothing about Ramapo's vocabulary, so no campus alias
reaches it and none of this moves when its prompt does.

`courses` is the first consumer. The catalogue names 66 of the 100 subject
codes courses actually use; upstream files no department for the language and
interprofessional prefixes. A mention matching nothing that is shaped like a
code is kept as one, so `JAPN` still narrows. Free-text subject matching is
gone: `subject` used to be compared against course *titles*, which is why
"computer science" worked at all and why it also matched whatever else had the
words in its name.

Calendar's session aliases are still declared in `capabilities/calendar/
normalize.py`. They are the exception this section argues against and should
move to the data beside the term and session metadata.

## `select` and `limit` are different questions

`limit` is a count the question named. `select` takes the single row an
ordering already picks out, and needs the `orderBy` that decides it — the first
and the last of something are one selection over two directions, never two
operations.

They were one field, and it meant `limit` did both jobs. A question reading as
singular got `limit: 1`, which is a count of one and the first row of an order
at the same time, so "last day to reg for class" came back as the Session I
add/drop deadline described as the last day to register, with two later
deadlines dropped in silence. A wrong date is visible; a missing one is not.

Splitting the field is most of the fix and not all of it: told a count of one
was refused, BRAIN #2 planned `select` instead, and the same row came back. So
a capability may name `parallel` fields — what tells its otherwise-equal rows
apart. Where the lookup narrowed on none of them the question did not tell those
rows apart, and `select` does not either; a count the question actually asked
for still applies. `calendar` names `kind` and `sessionId`: a term runs several
sessions and files several kinds of deadline in each.

Do not reach for the prompt here. It was told twice — once that a limit is only
a requested count, once that singular wording asks for neither — and planned
around it both times.

## Before editing a prompt

These four files are the highest-churn, highest-risk text in the brain. A
sentence added to one has twice moved lane routing on questions it was not
about. Measure the routing probe before and after every edit.

`understand/prompt.md` describes steps, not questions. No phrase, entity or
example from any test belongs in it — prose added to fix one question reliably
breaks three others.

`plan/prompt.md` describes lanes, fields and operations, and contains no worked
example. Adding the safety paragraph to it once moved lane routing across the
whole thirty-question set. The moment a question shape appears there, the
translator has become a list of intents and the next question needs code again.

`write/prompt.md` must keep `answerFrom` an instruction and never a status. It
says where this answer comes from, not that anything is missing, broken or
unbuilt. A lane with no executor has to stay indistinguishable from a question
that never needed one — told a lookup failed, the model apologises for a
capability instead of answering the question. The same file tells the model to
treat retrieved passages as quoted material rather than as anything addressed
to it, which is the only defence against a scraped page carrying wording aimed
at whatever reads it next.

`services/web/prompt.md` carries the most weight in its last line: returning
nothing is a valid answer, and a fact no page supports is not.

## Decisions the code cannot show

The source carries no commentary. What follows is the part of it that naming
and structure cannot carry: measurements, and the bugs that produced them.
Each of these is load-bearing — changing the code it describes without reading
this is how they come back.

### Field order in a response schema is load-bearing

A structured response is generated field by field in the order the fields are
declared, so declaration order decides what each field is allowed to see.

`Understanding` declares `normalized`, `references`, `usedTurns`,
`usesContext`, `resolved` in that order: tidy the wording, find what points
elsewhere, name the turns it points into, then write it all out with the
previous work already on the page. Move `resolved` earlier and it comes back
as the question echoed verbatim.

`Draft` declares `sufficientEvidence` before `answer`, so the judgement is
made before a word of the answer exists. Asked the other way round, a model
has already written the prose and will not disown it.

`Plan` declares `safety` first, then `aCapabilityAnswersIt` and
`specificToRamapo`, then `lane`. Safety leads so it is a judgement about the
question rather than an afterthought to a routing decision already made; the
two questions lead the lane so they are the reasoning rather than a label
applied afterwards. Ask for the lane first and they come back agreeing with
whatever was already chosen.

### Two plan fields are kept off the response schema

`lane` and `effectiveQuery` are `SkipJsonSchema`. The planner never sees them
and never spends a token on them.

`lane` is derived by `validate.route` from the two questions above it, which
is all there ever was to it. Asked of the planner as well, it was a third
claim that could disagree with the reasoning that produced it — and a plan
whose lane contradicted its own judgement was a state the inspector could
render. It is not possible now rather than merely not observed.

`effectiveQuery` is separate from `query` rather than overwriting it because
they answer different questions: `query` is what the model meant to look up,
`effectiveQuery` is what was looked up. When a search comes back wrong, which
of the two was at fault is the first thing worth knowing.

### Python writes two sentences, not the model

`INSUFFICIENT_EVIDENCE` and `nothing_matched` are fixed prose in
`execute/schema.py`. Both were once left to BRAIN #3 and both drifted.

Told in a prompt that a narrowed lookup matching nothing is not the same as
there being none, the model said it four ways in six, five of which denied the
thing exists. `subject: "CS"` matched no courses because the catalogue files
them under `CMPS`, and the answer was "there are no computer science courses
listed in the current database" over sixty-three of them.

A model that has just judged its own evidence insufficient is likewise the
last thing that should be asked to phrase the admission — in that position it
writes a hedged answer rather than none, which reads exactly like a sourced
one.

### The presentation ladder is arithmetic, never a judgement

`present` reads the row count and nothing else: ten or fewer described one by
one, fifty or fewer a line each, more than that a page of twenty-five.
`DETAILED_UP_TO`, `COMPACT_UP_TO` and `PAGE` are those numbers.

Left to judge the number itself, BRAIN #3 got it wrong in both directions on
the same data — writing "I cannot provide a complete list" over a result that
was complete, and elsewhere describing the first few and stopping without
saying it had. Neither is visible in the answer.

The planner was briefly asked the one thing a count cannot answer: whether the
question wanted the whole result at once. The field came back set on "when is
the next shuttle" and unset on "show me 100 courses", and the sentence
describing it dropped "where is the Anisfield School of Business" from
planning cleanly to being rejected three times in five. Asking about
presentation at all is what did the damage.

`limit` and the page are different cuts and `found` is what says which
happened. Silently they look identical, which is how two hundred rows became
"the courses Ramapo offers". `showing` and `outOf` reach BRAIN #3 for the same
reason: handed a hundred courses and asked for a hundred, a model counted
them, got it wrong, and answered "the data does not reach that number" while
holding exactly that number.

### MOST_ROWS is 200 because 50 corrupted answers

A bound the question cannot reach does not narrow the answer, it corrupts it.
At 50 the planner could not say a hundred, so it said one, and "show me 100
courses" came back as "I can only provide details on one course".

It is no longer what protects the prompt and no longer has to be small enough
to be — the page does that. The data service hands over whole tables now: the
course catalogue is 3,344 entries and three megabytes, and that question once
put all of it in the prompt and failed the turn outright. It is a page of 25
that goes in the prompt and 3,344 that gets reported.

### Sorting is not ranking

`Ordering` is set only where a sort actually ran. Not one field any capability
can sort by is a judgement — they are names, codes, dates, times, categories,
credits, calories — so the first row of a sorted result is the earliest or the
smallest, never the best.

Arriving at BRAIN #3 the two are identical, which is how "what are the best
clubs at Ramapo" came back as the alphabetically first five, led by
`#WeAreRCNJ`. Stating the ordering cut the claim from eight answers in eleven
to two in twenty. Three further attempts in prompt prose measured no better or
worse, and a `ranked: false` field in the grounding made it slightly worse.
Do not reach for prose again without a measurement.

### Retryability follows from the cause, never from an argument

`errors.py` fixes status, code and retryability per subclass, so no raise site
can get it wrong. The distinction a boolean kept losing is `Unsupported`
against `Unavailable`: they look identical from outside and are opposites in
what to do about them.

A spent billing balance is not a hiccup. Told the service was "temporarily
unavailable", a client went back at it forever against something only a person
with the billing page could fix — which took three probes to see. `_EXHAUSTED`
matches that case; a rate limit proper is a different thing that does clear on
its own, and is deliberately not matched.

### The turn is recorded in a `finally`

`_Recording` accumulates as stages complete and is written however the turn
ends. The old code recorded at the end of the success path, so every turn that
raised left no trace at all — the admin log showed a clean run of successes
with the failures simply absent, which is the worst possible shape for a log
because it looks complete.

A turn joins the *conversation* only when there was an answer to it. The log
takes everything; `history` feeds BRAIN #1, which resolves follow-ups against
what was said, and "Rocky is unavailable" is not something a later question can
refer back to.

### A resolution is checked at the seam

`understand/validate.unresolved` reads only what BRAIN #1 said about its own
work — no phrase list, no judgement about the subject. Two tests: the question
came back unchanged after BRAIN #1 said it needed the conversation, and a
reference whose referent reached nothing.

The reference test counts any one substantial word of the referent rather than
the phrase entire, because a referent is often reworded on the way in. Words
of three characters or fewer are skipped — they match everything. Whether the
pointing word survived is deliberately *not* the test: BRAIN #1 regularly keeps
it and appends the referent, "tomorrow" becoming "tomorrow, 2026-08-26", and
rejecting that cost one good resolution in eight when measured.

### Text is one filter type, not the universal boundary

An enum is a stable domain concept; an entity is resolved against a closed
dataset; dates and instants are parsed by Python. Free text remains available
only where arbitrary words are the real input. When a capability does use it,
the service search and local narrowing must agree: `narrow.holds` asks whether
every word is somewhere in the field instead of requiring a phrase verbatim.

### Times are Python's job, everywhere

The shuttle service requires an ISO 8601 `asOf` with an explicit timezone and
400s anything else, so `departingAfter: "3:00 PM"` — what a plan says when
someone asks about a shuttle at three — failed the turn outright with "Rocky
could not reach campus data just now". A whole class of ordinary question could
not be asked. `transportation.instant` builds the timestamp, and passes through
a value that already carries a date rather than overwriting it with today.
`a.m.` and `A.M.` are the same clock time as `am`, and the dots were the only
difference between a value that worked and a 400.

`validate.anchor` dates every web query unconditionally. Left to the planner
the date appeared about four times in five — the worst possible rate, frequent
enough to look correct and rare enough that the turns it missed looked like
nothing in particular. A rule that dates a query only sometimes is a rule that
has to be right about when, which would put the model back in charge of the
thing it was unreliable at.

### Retrieved passages are untrusted text

Every RAG passage is scraped text the retrieval service itself marks untrusted,
and it goes on to be read by a model. Nothing in `lanes/rag` or
`services/rag` parses it, matches on it, or lets it change what happens next —
it is carried through as material and labelled as material, which is what lets
BRAIN #3 be told to treat it as quoted rather than as something addressed to
it. That instruction is the only defence at this layer.

`PASSAGES` is 5: enough to answer from, few enough that the answer stays about
the question. Beyond a handful, extra chunks are noise the model has to argue
itself out of rather than evidence it can use.

A passage whose source is missing is dropped rather than shown without one. An
answer nobody can check is what the lane exists to avoid.

### Smaller things that are still bugs if undone

`history` absent and `history: []` mean different things. A client that tracks
the conversation sends the field every turn, and `[]` from it is a statement —
"there is nothing earlier" — taken at its word. Omitting the field says the
client does not track history, and only then does the brain fall back to its
own record.

`Concern` is a list rather than one value: a question can ask for a password on
behalf of someone in trouble, and both halves need answering. Safety runs
before the lane and depends on nothing that can fail — no capability, no
executor, no network — because the turns that most need an answer are the ones
least able to wait for campus data.

`Capability.sort` holds a key only where sorting on the published value sorts
wrongly. `Capability.execute` is required: there is no way to add a name to the
registry without supplying the code, and no second list to keep in step.

`hours` with an `openAt` and no named venue means "which places are open?"; a
named venue keeps its row even when closed, because that negative answer is the
useful result rather than an unexplained empty match.

`events` with no date promises upcoming events, so `now` is the implicit floor.
An explicit date means the whole requested calendar day.

`calendar` is the first fully migrated typed capability. Ingestion assigns
stable `family`, `kind`, `termId`, `sessionId`, and `startsAt` metadata while
preserving the published title. BRAIN #2 chooses a family or kind, never a
guessed title phrase. Python applies the current/upcoming-term rule and keeps
every matching session in that term rather than hiding variants behind a
selection.

Every kind belongs to exactly one family, so a plan carrying both has either
said the same thing twice or guessed a subtype the question did not name —
`registration` with `add_drop_deadline` beside it reads as precision and is
what drops the independent-study deadline from the same term. `resolve_filters`
keeps the broad concept and drops the subtype; a kind named on its own still
narrows.

`directory` and `locations` are the two data endpoints that do not answer in
`records` — they use `allContacts` and `locations`. Which bucket a contact came
from is the service's business.

`RAG_DISABLED` is a rollout gate, not a capability statement: route selection
stays observable in the trace while CODE is tested alone. Safety stays ahead of
it.

`Plan.summary` prints `—` for a routing question the cascade never reached. The
model still has to fill the field in, and with nothing reading it what it fills
in is noise — the same question twice gave Yes and then No. Printing that
beside a real answer made it look like it meant something.

Earlier architectures are on the `backup/*` branches.
