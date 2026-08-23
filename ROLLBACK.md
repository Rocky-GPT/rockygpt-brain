# Rollback

This document is the rollback rehearsal `spec/acceptance.md`'s promotion
rule requires before `rockygpt-ui`'s `BRAIN_URL` is ever repointed at this
service.

## Why rollback is cheap here

- **The switch itself is one config value.** `rockygpt-ui` treats the brain
  purely as an HTTP dependency (spec/system-boundaries.md) — promotion is
  repointing `BRAIN_URL`, nothing more. Rollback is repointing it back.
- **No shared mutable state crosses the boundary.** The brain owns its own
  Postgres schema, entirely separate from the existing brain's storage and
  from the data service's schema (spec/system-boundaries.md,
  spec/acceptance.md). Rolling the UI back to the previous brain does not
  require rolling back or migrating any database.
- **`/v1/chat` is stateless per call.** The UI sends the last ten turns of
  history with every request; the brain does not require a caller to have
  "started" a conversation against this specific instance. Conversation
  continuity survives a rollback because that history is client-supplied,
  not held by this service — but that is distinct from individual requests
  succeeding: any request literally in flight at the moment of the repoint/
  redeploy can still fail and needs the normal client-side retry, the same
  as any other brief cutover window.

## Rollback procedure

1. **Repoint `BRAIN_URL`** in `rockygpt-ui`'s configuration back to the
   previous (known-good) brain's URL and redeploy/restart the UI per its
   own deployment process. This is the entire rollback for user-facing
   traffic — no data migration, no schema rollback, no coordinated
   multi-service change.
2. **Confirm via smoke check**: `GET /readiness` on the UI and the
   previous brain both return `200`, and a manual chat round-trip through
   the UI returns a normal answer.
3. **Before stopping this service, confirm nothing else still depends on
   its URL** — a staging `rockygpt-evals` run, an operator dashboard open
   against `/v1/admin/logs*`, or a monitor/synthetic check pointed at it
   directly (independent of the UI) would all break if it's stopped out
   from under them. If nothing does, stopping it is safe; if something
   does and stopping isn't urgent, it's equally safe to leave it running
   with `BRAIN_URL` no longer pointed at it.
4. **Do not drop or truncate this service's `chat_logs` table** as part of
   rollback. It's an independent, brain-owned schema; leaving it in place
   avoids a destructive, hard-to-undo cleanup step and preserves whatever
   chat/feedback history was recorded while this service was receiving
   traffic — subject to the normal 30/90 day retention windows in
   `DESIGN.md` §6 either way. (Retaining it is not free: it still occupies
   database storage and, if the service keeps running, compute — weigh
   that against the cost of a destructive cleanup step when deciding
   whether to decommission it later.)

## What triggers a rollback

Any of the following, discovered either during the staged rollout or via
production monitoring, is sufficient reason to roll back immediately
rather than investigate forward:

- `/readiness` on this service reports `unready` and does not recover
  within a few minutes.
- A spike in `5xx` responses from `/v1/chat` or `/v1/feedback` (server-side
  is the actionable half — see below for the `4xx` exception).
- Citations failing validation or visibly not tracing to real campus
  sources (the acceptance gate this repository is built hardest against —
  see DESIGN.md §4/THREAT_MODEL.md §3.4; a regression here is a rollback
  trigger even if error rates look normal).
- Safety routing (`911`/`988`) not firing for the acceptance-gate scenarios
  in a spot check.
- Latency regressions that push `/v1/chat` responses close to or past the
  UI's 60-second upstream timeout.

**`4xx` responses are not automatically a rollback trigger.** `400`s from
malformed client requests, or `401`s from a genuinely missing/incorrect
staging token, reflect the *caller's* input, not a broken brain — check
whether the client behavior changed before assuming the brain regressed.

## Rollback rehearsal (run this before promotion)

To satisfy the promotion rule's rehearsal requirement, actually exercise
the procedure above against the preview/staging deployment, not just read
it:

1. Point a staging copy of `rockygpt-ui` at this service's staging
   deployment.
2. Confirm a normal chat round-trip works.
3. Repoint `BRAIN_URL` back to the previous brain and confirm traffic
   recovers with no manual data cleanup required on either side.
4. Record how long the repoint took end-to-end (config change + redeploy +
   confirmation) — that number is the actual rollback time budget to plan
   around for a real incident.
