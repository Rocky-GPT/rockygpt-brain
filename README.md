# RockyGPT Brain

The service behind RockyGPT, the Ramapo College student assistant. It is being
rebuilt from zero, one milestone at a time. The old Brain is commit c00eb91, and it
still runs in production from `main`.

## Status

Milestone 1, the Brain contract, is done: [docs/contract.md](docs/contract.md). The
Brain accepts questions in the shape the student app and dev UI already send.
It replies `not_ready` with emergency help until later milestones teach it to answer.

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

CI runs the same three checks on every push.
