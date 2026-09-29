# RockyGPT Brain

The service behind RockyGPT, the Ramapo College student assistant. It is being
rebuilt from zero, one milestone at a time. The old Brain is commit c00eb91, and it
still runs in production from `main`.

## Status

The Brain accepts questions in the shape the student app and dev UI already send.
It gives danger help first. Jev reads what each question asks, and code answers with
words it wrote when RockyGPT can't do the work (the student's own account, private
information, a live look) or can't tell what was asked. If Jev fails, the turn fails with
a clear failure (retryable for most causes) instead of guessing. Everything else gets `not_ready` with emergency
help until later milestones teach it to answer.

| Milestone | Where |
| --- | --- |
| 1. Brain contract | `contract.py`, [docs/contract.md](docs/contract.md) |
| 2. Conversation context | `context.py`: the conversation read once, with the cut-off count and one campus clock |
| 3. Safety and boundaries | `safety.py` (danger help first, what the Brain won't do), `failures.py` (every failure, each with the 911/988 help), a turn log without the student's words |
| 4. Jev decisions | `spending.py` (the spending cap: every paid call holds money in the ledger first and settles after), `jev.py` (Jev's client and price), `decisions.py` (Jev's questions, one call per turn, and the route code picks from them in Dan's nine-route table), `scripts/check_decisions.py` with `evals/decisions/` (the 130 labeled questions and their live results), [docs/jev.md](docs/jev.md) |

`api/app.py` is the web service. `turn.py` runs one turn, and each milestone adds its
step there.

## Run

```sh
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
uvicorn rockygpt_brain.api.app:app --host 127.0.0.1 --port 8000
```

Jev runs only with `BRAIN_ENVIRONMENT`, `BRAIN_LEDGER_DATABASE_URL` and
`BRAIN_TYPESAFE_API_KEY` set (see `.env.example`). Without them the Brain still runs,
without Jev.

## Check

```sh
ruff check . && mypy src tests && pytest -q
```

To try a scripted conversation on the running dev Brain, with the history written in the
file and not whatever the dev UI kept:
`python scripts/check_conversation.py evals/conversations/mixed-follow-ups.json`
(one Jev call per user message, about $0.0001 each).

The ledger tests need a disposable local PostgreSQL:
`BRAIN_TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:5432/brain_accounting_test`.
They apply `migrations/` to it themselves. CI runs all of these on every push.
