"""The differential harness: record a baseline, compare against it, or A/B two ports.

    python -m tools.differential.run record  --port http --out baselines/node.json
    python -m tools.differential.run compare --port postgres --baseline baselines/node.json
    python -m tools.differential.run ab      --left http --right postgres

`record` freezes what an implementation answers today. That is worth doing
before anything is ported: the baseline is the contract, and the Node service
is the only thing that currently knows what it is. `compare` holds a new
implementation to a recorded baseline. `ab` runs two live ports side by side,
which is what a cutover rehearsal looks like once both exist.

Exit status is meant for CI: 0 when nothing blocking was found, 1 when
something was, 2 when the harness itself could not run. `--strict` promotes
ordering differences to failures.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
for path in (str(ROOT), str(ROOT / "src")):
    if path not in sys.path:
        sys.path.insert(0, path)

from tools.differential import corpus  # noqa: E402
from tools.differential.capture import Capture, capture  # noqa: E402
from tools.differential.diff import Divergence, Severity, compare  # noqa: E402

from rockygpt_brain.config import get_settings  # noqa: E402
from rockygpt_brain.services.data import DataPort, HttpData  # noqa: E402


class HarnessError(Exception):
    pass


def port(name: str) -> DataPort:
    settings = get_settings()
    if name == "http":
        return HttpData(settings.data_url, settings.data_timeout_seconds)
    if name == "postgres":
        try:
            from rockygpt_brain.services.postgres_data import PostgresData
        except ImportError as exc:
            raise HarnessError(
                "There is no PostgresData yet — it is the next phase's work. "
                "Record an `http` baseline now; this harness is what will "
                "hold PostgresData to it when it lands."
            ) from exc
        url = settings.secret_value(settings.database_url)
        if not url:
            raise HarnessError("DATABASE_URL is unset, so the postgres port has nothing to read.")
        candidate: DataPort = PostgresData(url)
        return candidate
    raise HarnessError(f"unknown port {name!r} — expected 'http' or 'postgres'")


async def run_all(
    implementation: DataPort, cases: tuple[corpus.Case, ...], lanes: int
) -> list[Capture]:
    gate = asyncio.Semaphore(max(1, lanes))

    async def one(case: corpus.Case) -> Capture:
        async with gate:
            return await capture(case, implementation)

    return list(await asyncio.gather(*(one(case) for case in cases)))


def write_baseline(path: Path, name: str, captures: list[Capture]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "port": name,
        "pinnedNow": corpus.PINNED_NOW.isoformat(),
        "cases": [c.as_json() for c in captures],
    }
    path.write_text(json.dumps(body, indent=2, default=str) + "\n", encoding="utf-8")


def read_baseline(path: Path) -> tuple[str, dict[str, Capture]]:
    body: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    recorded = {c["case"]: Capture.from_json(c) for c in body.get("cases", [])}
    return str(body.get("port", "baseline")), recorded


def report(found: list[Divergence], left: str, right: str, cases: int, strict: bool) -> int:
    counts = Counter(d.severity for d in found)
    blocking = counts[Severity.BLOCKING]
    warned = counts[Severity.WARN]

    print(f"\n{cases} case(s)  {left} vs {right}")
    print(
        f"  BLOCKING {blocking}   WARN {warned}   INFO {counts[Severity.INFO]}"
        f"   clean {cases - len({d.case for d in found})}"
    )

    if found:
        print()
        by_case: dict[str, list[Divergence]] = {}
        for d in found:
            by_case.setdefault(d.case, []).append(d)
        for case, items in sorted(by_case.items()):
            covers = items[0].covers
            print(f"  {case}" + (f"   — {covers}" if covers else ""))
            for d in items:
                print(f"    {d.severity.value:<8} {d.locus}  {d.kind}: {d.detail}")
            print()

    if blocking:
        print("Blocking divergence: these methods are not safe to cut over.\n")
        return 1
    if warned and strict:
        print("Ordering divergence, and --strict was given.\n")
        return 1
    if warned:
        print("Records agree; ordering does not. Check the fetch limit before cutting over.\n")
    else:
        print("No divergence.\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tools.differential.run", description=__doc__)
    parser.add_argument("mode", choices=("record", "compare", "ab"))
    parser.add_argument("--port", default="http", help="implementation for record/compare")
    parser.add_argument("--left", default="http", help="ab mode: the reference implementation")
    parser.add_argument("--right", default="postgres", help="ab mode: the candidate")
    parser.add_argument("--baseline", type=Path, help="baseline file for record/compare")
    parser.add_argument("--out", type=Path, help="alias for --baseline in record mode")
    parser.add_argument("--only", help="run cases whose name or capability starts with this")
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--strict", action="store_true", help="treat ordering as failure")
    parser.add_argument("--json", type=Path, help="also write the divergence list here")
    args = parser.parse_args(argv)

    cases = corpus.cases(args.only)
    if not cases:
        print(f"No cases match {args.only!r}.", file=sys.stderr)
        return 2

    try:
        if args.mode == "record":
            target = args.out or args.baseline
            if target is None:
                raise HarnessError("record needs --out")
            captures = asyncio.run(run_all(port(args.port), cases, args.concurrency))
            write_baseline(target, args.port, captures)
            rows = sum(len(c.output or []) for c in captures)
            failed = [c.case for c in captures if c.failure]
            print(f"Recorded {len(captures)} case(s), {rows} record(s) -> {target}")
            if failed:
                print(f"  {len(failed)} case(s) failed while recording: {', '.join(failed)}")
                print("  A baseline holding failures freezes them as expected behaviour.")
            return 0

        if args.mode == "compare":
            if args.baseline is None:
                raise HarnessError("compare needs --baseline")
            name, recorded = read_baseline(args.baseline)
            captures = asyncio.run(run_all(port(args.port), cases, args.concurrency))
            found: list[Divergence] = []
            for current in captures:
                before = recorded.get(current.case)
                if before is None:
                    print(f"  (new case not in baseline, skipped: {current.case})")
                    continue
                found.extend(compare(before, current, name, args.port))
            code = report(found, name, args.port, len(captures), args.strict)
        else:
            left_port, right_port = port(args.left), port(args.right)
            left_caps = asyncio.run(run_all(left_port, cases, args.concurrency))
            right_caps = asyncio.run(run_all(right_port, cases, args.concurrency))
            paired = zip(left_caps, right_caps, strict=True)
            found = [
                d for before, after in paired for d in compare(before, after, args.left, args.right)
            ]
            code = report(found, args.left, args.right, len(cases), args.strict)
    except HarnessError as exc:
        print(f"{exc}", file=sys.stderr)
        return 2

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(
                [
                    {
                        "case": d.case,
                        "covers": d.covers,
                        "locus": d.locus,
                        "kind": d.kind,
                        "severity": d.severity.value,
                        "detail": d.detail,
                    }
                    for d in found
                ],
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
