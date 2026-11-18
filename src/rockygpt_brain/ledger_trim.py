"""Delete the spending-ledger rows that are history only.

The ledger gains a row per model call (brain_ops.operations) and one per answer (brain_ops.turns);
on the free Neon plan's 0.5 GB that adds up (October 2026 alone added 45 MB). The monthly cap
reads the current month's settled rows and every unsettled one, so only settled operations, and
turns, older than the kept window can go. Reserved and uncertain holds are never deleted: they
still count against the cap until settled (scripts/clear_stuck_holds.py).

This used to run from the data repo's weekly cleanup (audit 2026-11-17, item 15: the ledger is
the Brain's, so its trim lives here). It needs a login that may delete, which the Brain's own
runtime roles may not, so it runs from the weekly ledger-trim workflow, never inside the Brain.
"""

from dataclasses import dataclass
from datetime import timedelta

from psycopg import AsyncConnection

KEPT = timedelta(days=60)
BATCH = 5000


@dataclass(frozen=True)
class TrimResult:
    operations: int
    turns: int
    database_bytes: int
    dry_run: bool


_WHERE = {
    "brain_ops.operations": "state = 'settled' AND created_at < clock_timestamp() - %(kept)s",
    "brain_ops.turns": "created_at < clock_timestamp() - %(kept)s",
}


async def _trim_table(connection: AsyncConnection, table: str, kept: timedelta,
                      dry_run: bool, batch: int) -> int:
    where = _WHERE[table]
    values = {"kept": kept, "batch": batch}
    if dry_run:
        cursor = await connection.execute(
            f"SELECT count(*) FROM {table} WHERE {where}", values)  # noqa: S608 (fixed names)
        row = await cursor.fetchone()
        return int(row[0]) if row else 0
    deleted = 0
    while True:
        # One batch per statement keeps each inside the database's statement timeout.
        cursor = await connection.execute(
            f"DELETE FROM {table} WHERE ctid IN "  # noqa: S608 (fixed names)
            f"(SELECT ctid FROM {table} WHERE {where} LIMIT %(batch)s)", values)
        deleted += cursor.rowcount
        if cursor.rowcount < batch:
            return deleted


async def trim_ledger(connection: AsyncConnection, *, kept: timedelta = KEPT,
                      dry_run: bool = False, batch: int = BATCH) -> TrimResult:
    """Delete (or with dry_run, count) the settled operations and the turns older than kept.
    The connection must be in autocommit mode, so each batch commits on its own."""
    if kept < timedelta(days=32):
        raise ValueError("kept must cover more than a month: the cap reads this month's rows")
    operations = await _trim_table(connection, "brain_ops.operations", kept, dry_run, batch)
    turns = await _trim_table(connection, "brain_ops.turns", kept, dry_run, batch)
    cursor = await connection.execute("SELECT pg_database_size(current_database())")
    row = await cursor.fetchone()
    return TrimResult(operations, turns, int(row[0]) if row else 0, dry_run)
