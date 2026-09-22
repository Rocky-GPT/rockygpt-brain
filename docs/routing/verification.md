# Implementation verification — 2026-09-22

- Full suite with a disposable local PostgreSQL instance: **679 passed, 2 failed,
  1 skipped**. All 68 added tests passed, including the operational migration,
  environment isolation, input-only billing, paid admission, direct retrieval,
  evidence validation, HTTP cancellation, privacy and evaluation promotion gates.
- Both failures are the existing parameterizations of
  `test_short_dinner_chat_with_fifty_menu_records_and_hours` in
  `tests/test_phase2_database.py`. The fixture expects 51 menu matches but gets 50.
  Both failures reproduce on a clean export of the pre-change current HEAD.
- Repository-wide mypy has 89 pre-existing errors; comparison against that same
  clean baseline found no additional type errors. Focused type checking of the
  routing provider, router and evaluation runner passes.
- Repository-wide Ruff has existing findings. Changed routing modules, the
  evaluation runner, and their new tests pass focused lint checks. No new lint
  diagnostics remain compared with the clean baseline.
- `git diff --check` passes.
- Live preflight: **blocked only by `BRAIN_TYPESAFE_API_KEY`**. No paid evaluation
  calls were made. Accuracy and latency/cost gains are unverified; routing remains
  off. See `live-status.json` and the setup instructions before enabling it.

The migration was exercised only on the disposable test database. No deployment
database, account allowance, or runtime routing setting was changed.
