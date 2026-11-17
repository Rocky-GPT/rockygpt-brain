"""List, and with --release clear, the model-spending holds no call settled.

A hold is written before every model call and settled with the call's cost after it. A call
whose process stopped, or whose provider answer never came back, leaves its hold "reserved" or
"uncertain", and the hold keeps counting against the month's allowance at its full amount
(audit item 16, 2026-11-17: $3.46 to $4.51 of them on dev, cleared only by hand-written SQL).

    BRAIN_LEDGER_DATABASE_URL=... BRAIN_ENVIRONMENT=development \\
        python scripts/clear_stuck_holds.py                    # list holds older than 30 minutes
    ... python scripts/clear_stuck_holds.py --release          # settle them at no cost

Check the provider's usage page before --release: a call the provider did charge is then not
counted. Released holds keep their row, marked "released by hand" in error_code.
"""

import argparse
import asyncio
import os
import sys
from datetime import timedelta

from rockygpt_brain.spending import PostgresLedger


async def main(arguments: argparse.Namespace) -> int:
    environment = os.environ.get("BRAIN_ENVIRONMENT", "development")
    if environment not in ("development", "production"):
        print("BRAIN_ENVIRONMENT must be development or production", file=sys.stderr)
        return 2
    url = os.environ.get("BRAIN_LEDGER_DATABASE_URL")
    if not url:
        print("BRAIN_LEDGER_DATABASE_URL is not set", file=sys.stderr)
        return 2
    ledger = PostgresLedger(url, environment)  # type: ignore[arg-type]
    await ledger.open()
    try:
        holds = await ledger.stuck_holds(timedelta(minutes=arguments.older_than_minutes),
                                         release=arguments.release)
    finally:
        await ledger.close()
    verb = "Released" if arguments.release else "Stuck"
    total = sum(int(hold["reservedNusd"]) for hold in holds)
    print(f"{verb} in {environment}: {len(holds)} hold(s), ${total / 1e9:.4f}")
    for hold in holds:
        print(f"  {hold['createdAt']:%Y-%m-%d %H:%M} UTC  {hold['state']:<9} "
              f"${int(hold['reservedNusd']) / 1e9:.4f}  {hold['errorCode'] or '-'}  "
              f"request {hold['request']}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--older-than-minutes", type=int, default=30)
    parser.add_argument("--release", action="store_true",
                        help="settle the holds at no cost (check the provider's usage first)")
    sys.exit(asyncio.run(main(parser.parse_args())))
