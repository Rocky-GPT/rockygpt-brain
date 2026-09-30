# RockyGPT Brain

The service behind RockyGPT, the Ramapo College student assistant. It is being
rebuilt from zero, one milestone at a time. The old Brain is commit c00eb91, and it
still runs in production from `main`.

## Status

The Brain was started again from line one after commit 3dec0bd. It takes in a chat request,
checks it against [docs/contract.md](docs/contract.md), makes a `Turn` (`turn.py`) and
answers `not_ready`. Nothing answers questions yet. Each step adds to `api/app.py` and
`turn.py`.

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
