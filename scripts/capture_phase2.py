"""Capture a bounded, public, read-only source snapshot. Never call a model."""

import gzip
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from psycopg import sql

from rockygpt_brain.retrieval.data import CampusData, SearchQuery

load_dotenv()
now = datetime.now(ZoneInfo("America/New_York"))
data = CampusData(os.environ["DATABASE_URL"], now)
try:
    ready = data.readiness()
    contacts = data.search(SearchQuery(collection="contacts", query="Registrar", limit=12))
    snapshot = {"captured_at": now.isoformat(), "readiness": ready, "contact_search": contacts}
    Path("docs/phase2/published-contact.json").write_text(
        json.dumps(snapshot, default=str, indent=2) + "\n"
    )
    tables = {}
    # Public retrieval data only: no chats, feedback, ingestion errors or credentials.
    for table in (
        "campus_contacts",
        "academic_dates",
        "campus_events",
        "clubs",
        "programs",
        "menu_items",
        "campus_hours",
        "dining_hours",
        "shuttle_routes",
        "shuttle_trips",
        "critical_facts",
        "documents",
    ):
        rows = data._fetch(
            sql.SQL(
                "SELECT * FROM rockygpt_v2.{} "
                "WHERE dataset_version_id=%s::uuid ORDER BY id LIMIT 10001"
            ).format(sql.Identifier(table)),
            (data.dataset["id"],),
        )
        if len(rows) > 10000:
            raise ValueError("Snapshot exceeds public collection bound")
        tables[table] = rows
    tables["sources"] = data._fetch(
        "SELECT * FROM rockygpt_v2.sources "
        "WHERE trust_tier IN ('official_primary','official_secondary') ORDER BY id"
    )
    tables["source_runs"] = data._fetch(
        "SELECT id,dataset_version_id,source_key,status,started_at,completed_at,source_url "
        "FROM rockygpt_v2.source_runs WHERE dataset_version_id=%s::uuid ORDER BY id",
        (data.dataset["id"],),
    )
    tables["document_chunks"] = data._fetch(
        "SELECT c.id,c.document_id,c.chunk_index,c.content,c.content_hash,c.metadata "
        "FROM rockygpt_v2.document_chunks c JOIN rockygpt_v2.documents d ON d.id=c.document_id "
        "WHERE d.dataset_version_id=%s::uuid ORDER BY c.document_id,c.chunk_index LIMIT 10001",
        (data.dataset["id"],),
    )
    if len(tables["document_chunks"]) > 10000:
        raise ValueError("Snapshot exceeds chunk bound")
    tables["release_artifacts"] = data._fetch(
        "SELECT * FROM rockygpt_v2.release_artifacts WHERE dataset_version_id=%s::uuid "
        "AND artifact_key IN ('menu-context','dining-hours',"
        "'events','courses','programs','faculty','search-vocabulary')",
        (data.dataset["id"],),
    )
    Path("docs/phase2/public-snapshot.json.gz").write_bytes(
        gzip.compress(
            json.dumps(
                {
                    "captured_at": now.isoformat(),
                    "dataset": data.dataset,
                    "tables": tables,
                },
                default=str,
                indent=2,
            ).encode(),
            mtime=0,
        )
    )
    print(json.dumps({"snapshot_rows": {key: len(rows) for key, rows in tables.items()}}))
    print(
        json.dumps(
            {
                "dataset": ready["dataset_version"],
                "records": [
                    {
                        "title": r["title"],
                        "source": r["url"],
                        "fields": r["fields"],
                        "freshness": r["freshness"],
                    }
                    for r in contacts["records"]
                ],
            }
        )
    )
except Exception as error:
    print(json.dumps({"capture": "failed", "error_type": type(error).__name__}))
    raise SystemExit(1) from None
finally:
    data.close()
