# RockyGPT Brain

The service behind RockyGPT, the Ramapo College student assistant. It is being
rebuilt from zero, one milestone at a time. The old Brain is commit c00eb91, and it
still runs in production from `main`.

## Status

The Brain was started again from line one after commit 3dec0bd. `/v1/chat` checks the request
against [docs/contract.md](docs/contract.md), makes a `Turn` (`turn.py`), gives danger help or an
account limit when the message needs it (`boundary.py`), and otherwise answers `not_ready`.
Built but not used by the route yet: the conversation `Context` (`context.py`), the Jev call
that reads it into an `Understanding` (`understanding.py`) and the code `Plan` made from both
(`plan.py`). Nothing answers questions yet.

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
