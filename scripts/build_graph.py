"""Build the release graph from a campus database and publish it for a graph-only Brain.

This is the one step that reads the campus database. Run it where the database can be reached,
then point the Brain at the output directory with BRAIN_GRAPH_ONLY_DIR; that Brain never
connects to the campus database.

    python scripts/build_graph.py --out DIR [--dbname NAME]

It reads DATABASE_URL (a read-only login is enough) and never prints any part of it. --dbname
reads another database on the same server. The output directory keeps the newest releases and an
`active.json` pointer, which is written last and renamed into place, so a running Brain sees either
the old release or the whole new one. Run it again after every data release.
"""

import argparse
import os
import sys
import time
from pathlib import Path

from psycopg.conninfo import conninfo_to_dict, make_conninfo

from rockygpt_brain.retrieval import EvidenceUnavailable, PostgresEntityFacts
from rockygpt_brain.retrieval.graph_store import (
    GraphEntityFacts,
    GraphUnavailable,
    publish_release_graph,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Publish the active campus release as a graph.")
    parser.add_argument("--out", required=True, type=Path, help="directory the Brain will serve")
    parser.add_argument("--dbname", help="read this database instead of the one DATABASE_URL names")
    args = parser.parse_args(argv)
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        print("DATABASE_URL is not set.", file=sys.stderr)
        return 2
    if args.dbname:
        info = conninfo_to_dict(url)
        info["dbname"] = args.dbname
        url = make_conninfo("", **info)
    started = time.monotonic()
    try:
        built = publish_release_graph(PostgresEntityFacts(url), args.out)
        graph = GraphEntityFacts(built)
    except (EvidenceUnavailable, GraphUnavailable) as error:
        print(f"Could not publish the release graph: {error}", file=sys.stderr)
        return 1
    size = built.stat().st_size / (1024 * 1024)
    print(f"published {built.name}: release {graph.dataset_version}, "
          f"identity {graph.identity_hash[:12]}, {size:.1f} MiB, "
          f"{time.monotonic() - started:.1f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
