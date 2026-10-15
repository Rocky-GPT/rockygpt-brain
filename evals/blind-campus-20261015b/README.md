# Fresh blind check 2026-10-15 (evening), after the blind-check fixes

Asked by Dan ("do a fresh blind test") after the fixes for the first blind check (brain f49e2ac..
6c07354, combined with another session's 910392c). Same method as blind-campus-20261015, new
writer personas, nothing reused: writers see only writer-brief.md; the set is frozen with its
sha256 and committed before any question is sent; each question is asked once on :8019 (written
mode, the main checkout at the commit in release-under-test.txt); two independent graders per
batch judge against the published data only (graph read from :8019, the same release).

Pass bar (set before the run, same as the first check): no answer graded "wrong" with severity
"serious" by both graders, and correct + declined_ok at least 80% of the scored answers. The share
with asked_ok counted as good is reported beside it, as last time.

Dropped before freezing (exact repeats of earlier sets or of each other): none.
