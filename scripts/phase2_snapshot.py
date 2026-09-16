"""Load the public snapshot ONLY into the explicitly named disposable local database."""

import gzip
import json
import os
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb

ROOT = Path(__file__).resolve().parents[1]


def snapshot() -> dict[str, Any]:
    return json.loads(gzip.decompress((ROOT / "docs/phase2/public-snapshot.json.gz").read_bytes()))  # type: ignore[no-any-return]


def local_database() -> str:
    url = os.environ["BRAIN_TEST_DATABASE_URL"]
    parts = conninfo_to_dict(url)
    if (
        parts.get("host") not in {"127.0.0.1", "localhost"}
        or parts.get("dbname") != "brain_accounting_test"
    ):
        raise ValueError("Only localhost/brain_accounting_test may be loaded")
    return url


def load_snapshot() -> dict[str, Any]:
    frozen = snapshot()
    with psycopg.connect(local_database()) as conn:
        conn.execute("DROP SCHEMA IF EXISTS rockygpt_v2 CASCADE")
        conn.execute("CREATE SCHEMA rockygpt_v2")
        conn.execute((ROOT / "docs/phase2/snapshot-schema.sql").read_text())
        dataset = frozen["dataset"]
        conn.execute(
            "INSERT INTO rockygpt_v2.dataset_versions (id,version,status,activated_at) "
            "VALUES (%s,%s,'active',%s)",
            (dataset["id"], dataset["version"], dataset["activated_at"]),
        )
        for table in (
            "sources",
            "source_runs",
            "campus_contacts",
            "academic_dates",
            "menu_items",
            "campus_hours",
            "dining_hours",
            "shuttle_routes",
            "shuttle_trips",
            "critical_facts",
            "documents",
            "document_chunks",
            "release_artifacts",
        ):
            for row in frozen["tables"][table]:
                conn.execute(
                    sql.SQL("INSERT INTO rockygpt_v2.{} ({}) VALUES ({})").format(
                        sql.Identifier(table),
                        sql.SQL(",").join(map(sql.Identifier, row)),
                        sql.SQL(",").join(sql.Placeholder() for _ in row),
                    ),
                    tuple(
                        Jsonb(value) if isinstance(value, (dict, list)) else value
                        for value in row.values()
                    ),
                )
    return frozen


if __name__ == "__main__":
    loaded = load_snapshot()
    print(json.dumps({"loaded": loaded["dataset"]["version"], "paid_calls": 0}))
