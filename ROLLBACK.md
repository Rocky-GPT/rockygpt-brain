# Rollback

A rollback changes `rockygpt-ui`'s `BRAIN_URL` back to the previous known-good
brain. It does not migrate or delete either brain's private persistence.

## Conversation continuity

Hybrid V1 is stateful. Its server-owned assistant-claim ledger, corrections,
selected entities, and durable evidence snapshots are authoritative for what
Rocky previously said and why. The UI also sends up to ten recent turns, but that
client history is untrusted fallback context; it cannot replace or prove the
server ledger.

After rollback, the previous brain may use the UI's recent history to preserve
basic conversational flow. It may not have Hybrid V1's claim/evidence ledger, so
exact previous-utterance provenance and longer-lived corrections can be lost
across brain versions. In that case the brain should say it cannot verify the
earlier claim, not reconstruct it as current campus truth.

Requests in flight during the configuration change can fail and use the UI's
normal retry path.

## Procedure

1. Repoint the UI's `BRAIN_URL` to the previous known-good brain and redeploy or
   restart the UI.
2. Confirm UI readiness and one new chat round-trip.
3. Test one follow-up using visible UI history. Record whether server-ledger
   continuity was lost; do not treat client history as authoritative evidence.
4. Confirm no eval run, operator dashboard, monitor, or other direct client still
   depends on the candidate URL before stopping it.
5. Preserve the candidate's brain-owned database. Do not drop, truncate, or
   migrate it during rollback. Its normal 30/90-day retention continues.
6. Record the end-to-end rollback time and the continuity limitation observed.

No DATA rollback is implied unless the incident is independently traced to a
DATA release. Brain database credentials remain isolated from DATA storage.

## Immediate rollback triggers

- readiness remains unready;
- sustained chat/feedback 5xx or latency approaching the UI's 60-second timeout;
- fabricated, mismatched, or missing provenance for campus claims;
- broken first/next or destination/route shuttle semantics;
- emergency or privacy policy regression;
- corrupted memory, cross-conversation leakage, or failed-turn memory mutation.

Malformed-request 400s and correct staging-token 401s are caller failures, not by
themselves rollback triggers.

## Required rehearsal

Before promotion:

1. Point a preview UI at Hybrid V1 and complete a cited shuttle conversation.
2. Ask a follow-up that relies on the server claim ledger.
3. Repoint the preview UI to the previous brain.
4. Verify new chat traffic recovers.
5. Ask another follow-up and document whether only UI-history fallback remains.
6. Confirm the candidate database was preserved and no cleanup was required.
