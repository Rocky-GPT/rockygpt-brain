# Field-selection evaluation (blind, frozen)

Question: when a student asks for one thing about an office, does the Brain fetch only that
thing, and when they ask for more, does it still fetch all of it? Fetching a field nobody asked for
makes the Fact Packet bigger and can make a complete answer look partial (an unrelated field is
unknown). Fetching too little loses an answer, which is worse.

**How the cases were made (2026-10-07).** Three writers who saw only the list of published offices
and the field definitions (not the prompt, the code or any Brain output) wrote 24 cases each from
different angles: terse one-detail messages, full sentences with context (including general
"how do I contact" requests), and multi-turn follow-ups with slang and vague wording. Two other
labelers, who never saw the writer's label, marked for every case the minimal set of fields a good
answer must contain. The two labelers agreed on all 72. A case is scored only when the writer and
both labelers agree (69 cases). The 3 others (all about whether "prefers email" counts beside
"preferred contact") are run and reported but never scored. `cases.sha256` is the hash of
`cases.json`; a test fails if the file changes after the freeze.

**What is scored.** The fields in the packet's `request.fields`, compared with the agreed `needs`:
`ok` (exactly those), `OVER` (those and more), `UNDER` (a needed field left out), `BOTH`, or
`NO LOOKUP` (the Brain looked nothing up). A general request to contact or reach an office
(9 scored cases) needs `email` and `phones` (what the labelers agreed on). The Brain's rule for such a request is
to read the three contact fields, so `offices` is also allowed there; anything else is `OVER`.
Whether a general contact request should also fetch the room is Dan's call, not the labelers'.

**Pass bar, set before any run.** On the scored cases: at most 1 case that leaves out a needed field
(`UNDER` plus `BOTH`) and no more than the Brain before the change; at least 90% `ok`; no more
`NO LOOKUP` cases than the Brain before the change. The Brain before the change is `2af0fe7` (the
prompt that read `email, phones and offices` for any contact request).

**Run.** Start the Brain with `BRAIN_OUTPUT=json`, then
`python3 evals/overfetch/run.py <port> out.json [baseline.json]`. Each case is sent once. Run the
baseline Brain first on its own port (a `git archive 2af0fe7` export started the same way), then the
changed Brain with the baseline's output file. Nothing is changed after a run: a miss is reported
with what the Brain fetched, and the thing to look for is a repeated general problem, not a sentence
to patch.
