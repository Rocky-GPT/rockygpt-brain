"""Share the active release's graph file and meaning index in the file store.

The daily data run calls this after build_graph.py and build_meaning_index.py. Production's
Brain and the laptop then download this same copy (rockygpt_brain.retrieval.graph_sync), so both
serve the same bytes. Release files go up first and the pointer (active.json) last.

    python scripts/publish_graph.py --dir .published

Reads BRAIN_GRAPH_STORE_BUCKET, _ENDPOINT, _REGION, _PREFIX, _ACCESS_KEY_ID and
_SECRET_ACCESS_KEY. It reads no campus database.
"""

import argparse
import sys
from pathlib import Path

import httpx

from rockygpt_brain.retrieval.graph_sync import GraphStore, SyncError, publish


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Share the release graph in the file store.")
    parser.add_argument("--dir", required=True, type=Path, help="build_graph.py's --out folder")
    args = parser.parse_args(argv)
    try:
        store = GraphStore.from_env()
        if store is None:
            raise SyncError("BRAIN_GRAPH_STORE_BUCKET is not set.")
        print(publish(store, args.dir))
    except (SyncError, httpx.HTTPError, OSError) as error:
        text = str(error) if isinstance(error, SyncError) else type(error).__name__
        print(f"publish_graph: {text}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
