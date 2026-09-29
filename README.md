# RockyGPT Brain

The service behind RockyGPT, the Ramapo College student assistant. It is being
rebuilt from zero, one milestone at a time. The old Brain is commit c00eb91, and it
still runs in production from `main`.

## Status

The Brain accepts questions in the shape the student app and dev UI already send.
It gives danger help first, and replies `not_ready` with emergency help to
everything else until later milestones teach it to answer.

| Milestone | Where |
| --- | --- |
| 1. Brain contract | `contract.py`, [docs/contract.md](docs/contract.md) |
| 2. Conversation context | `context.py`: the conversation read once, with the cut-off count and one campus clock |
| 3. Safety and boundaries | `safety.py` (danger help first, what the Brain won't do), `failures.py` (every failure, each with the 911/988 help), a turn log without the student's words |
| 4. Jev decisions (in progress) | `spending.py`: the spending cap. Every paid call holds money in the ledger first and settles after |

`api/app.py` is the web service. `turn.py` runs one turn, and each milestone adds its
step there.

## Run

```sh
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
uvicorn rockygpt_brain.api.app:app --host 127.0.0.1 --port 8000
```

## Check

```sh
ruff check . && mypy src tests && pytest -q
```

The spending tests need a disposable local PostgreSQL:
`BRAIN_TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:5432/brain_accounting_test`.
They apply `migrations/` to it themselves. CI runs all of these on every push.
