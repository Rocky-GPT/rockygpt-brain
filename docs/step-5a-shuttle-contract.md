# Step 5A: shuttle capability contract

Step 5A defines transportation-specific request and result types only. The
types are not connected to chat, a model, the database, response generation,
or either UI yet.

## Trusted database inspected

The live `rockygpt_v2` database was inspected on 2026-08-31. Its active
dataset is `v2-20260831175938`, activated at `2026-08-31T18:02:48.527Z` from
source commit `287949c30a4d586da00a4346ec8f4ce4edb35dd6`.

The shuttle tables store:

- routes: name, `weekday`/`saturday`/`sunday` service day, optional validity
  dates, collection time, content hash, dataset, and source references;
- trips: route, sequence, departure text, arrival text, ordered JSON stops
  (`location` and `time`), optional validity dates, collection time, content
  hash, dataset, and source references;
- provenance: dataset version/activation and source title, canonical URL,
  trust tier, freshness SLA, and collection time.

The active data contains 51 trips:

| Route | Service day | Trips | First departure | Last departure | Final arrival |
| --- | --- | ---: | --- | --- | --- |
| Ramsey Route 17 | weekday | 18 | 7:00 AM | 5:30 PM | End of Service |
| Weekday Roadrunner Express | weekday | 12 | 7:00 AM | 9:40 PM | 10:45 PM |
| Saturday Roadrunner Express | saturday | 12 | 9:00 AM | 9:55 PM | 11:00 PM |
| Sunday Roadrunner Express | sunday | 9 | 10:00 AM | 6:55 PM | 8:00 PM |

Representative records include a weekday Roadrunner trip departing at 7:00
AM, stopping at Garden State Plaza at 7:30 AM, and returning at 7:55 AM; and a
Saturday trip departing at 9:55 PM, stopping at Interstate Plaza, Garden State
Plaza, and Ramsey Rt 17 Train, and returning at 11:00 PM. Every active shuttle
row points to the official-primary Transportation Services source.

## Supported questions

Deterministic code can use these records to answer:

- immediate next shuttle and next N shuttles;
- first, remaining, or full schedules for an ordinary weekday, Saturday,
  Sunday, today, tomorrow, a named weekday, or a calendar date mapped to its
  weekly template;
- canonical route filtering and filtering by stop occurrences actually
  encoded in a trip;
- campus departure time, encoded intermediate-stop time, and return-to-campus
  arrival time;
- whether a departure or arrival exists at an exact time or within the
  contract's fixed 15-minute “around” window;
- no-result behavior before, at, and after schedule boundaries;
- counts, first/last departures, and comparisons between two schedules;
- follow-up references once a model later interprets the ordered conversation
  into a complete request.

Campus is implicit in the trip-level departure and arrival fields rather than
stored as a stop. A future executor must describe that behavior explicitly and
must not claim an off-campus direction whose required stop occurrence is not
encoded.

## Typed request

`ShuttleRequest` is one discriminated union:

- `query`: one trip listing or availability check;
- `comparison`: exactly two full-schedule queries;
- `clarification`: an ambiguous shuttle request, unresolved reference, or safe
  interpretation failure that needs user input;
- `unsupported`: a shuttle request that requires facts absent from the data.

Each `ShuttleQuery` carries only interpretation, never campus facts:

- a typed day scope (`upcoming`, today/tomorrow offset, named weekday,
  service-day template, or calendar date);
- `next`, bounded `last`, or `all`, plus a bounded count/offset for next-trip questions;
- optional route, origin, and destination mentions copied from the
  conversation for later database-only entity resolution;
- an optional exact/around departure-or-arrival time constraint.

The model will not be allowed to provide route IDs, trip records, schedules,
sources, calculated dates, or other trusted facts. Step 5A does not implement
that future interpretation.

## Typed result

`ShuttleResult` echoes the interpreted request and has an explicit outcome:
`success`, `empty`, `no_match`, `needs_clarification`, or `unsupported`.

An executed result contains:

- the evaluation timestamp and resolved date/service-day scope;
- ordered `ShuttleTripFact` records with trusted departure, stops, arrival,
  identifiers, hashes, and optional deterministic calculations;
- matched/returned completeness and the fixed around-time window when used;
- a typed two-sided schedule comparison when requested;
- active dataset and source provenance.

Validation prevents incompatible shapes such as an unbounded full schedule,
an availability check without a time, a comparison of non-full schedules, an
executed result without provenance, or a successful comparison without two
resolved query results.

## Data gaps: do not answer by guessing

The current trusted tables cannot support:

- live vehicle position, delays, cancellations, or “is it running right now?”;
- semester/effective schedule claims, holidays, closures, or special-service
  exceptions (the active rows have no validity dates or exception calendar);
- capacity, occupancy, fares, accessibility, booking, or travel advice;
- Shortline, train-connection, or special-event schedules not published into
  the active shuttle tables;
- historical schedule reconstruction from the active release alone;
- arbitrary stop-to-stop directions when the necessary ordered occurrences
  are absent.

The database also does not store a timezone with its clock strings, a campus
stop record, or a structured term label. Those values must not be attributed
to the database. A later executor may apply an explicit campus-time behavior,
but that behavior must remain separate from trusted facts and visible in the
result.

The active structured rows are published from a checked-in schedule reference,
while collection provenance points to the Transportation Services scrape.
Some normalized scrape values differ from the active structured rows, and some
outbound Roadrunner stop occurrences are absent from the structured data. The
active database remains the only fact source for this capability, but those
provenance and coverage limits must be visible rather than silently filled in.
