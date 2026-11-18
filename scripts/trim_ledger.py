"""Delete the spending-ledger rows past the kept window (settled operations and turns).

    LEDGER_OWNER_DATABASE_URL=... python scripts/trim_ledger.py --dry-run   # count only
    LEDGER_OWNER_DATABASE_URL=... python scripts/trim_ledger.py             # delete

The URL is a login that may delete from brain_ops (the database owner); the Brain's runtime
logins may not. rockygpt-infra's weekly Ledger trim workflow runs it.
See rockygpt_brain/ledger_trim.py.
"""

import argparse
import asyncio
import os
import sys
from datetime import timedelta
from typing import Any

import certifi
from psycopg import AsyncConnection
from psycopg.conninfo import conninfo_to_dict

from rockygpt_brain.ledger_trim import KEPT, trim_ledger


async def main(arguments: argparse.Namespace) -> int:
    url = os.environ.get("LEDGER_OWNER_DATABASE_URL")
    if not url:
        print("LEDGER_OWNER_DATABASE_URL is not set", file=sys.stderr)
        return 2
    # Neon's URL asks for certificate checks (sslmode=verify-full); a runner has no
    # ~/.postgresql/root.crt, so give libpq trusted roots, as the Brain's own reader does.
    tls: dict[str, Any] = {}
    if not os.getenv("PGSSLROOTCERT") and "sslrootcert" not in conninfo_to_dict(url):
        tls["sslrootcert"] = certifi.where()
    async with await AsyncConnection.connect(
            url, autocommit=True, application_name="rockygpt-ledger-trim",
            options="-c statement_timeout=60000", **tls) as connection:
        result = await trim_ledger(connection, kept=timedelta(days=arguments.kept_days),
                                   dry_run=arguments.dry_run)
    verb = "Would delete" if result.dry_run else "Deleted"
    print(f"{verb}: {result.operations} settled operation(s) and {result.turns} turn(s) older "
          f"than {arguments.kept_days} days. Database size now "
          f"{result.database_bytes / 1e6:.1f} MB.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--kept-days", type=int, default=KEPT.days)
    parser.add_argument("--dry-run", action="store_true", help="count, delete nothing")
    sys.exit(asyncio.run(main(parser.parse_args())))
