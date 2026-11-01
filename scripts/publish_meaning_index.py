"""Store the meaning index built beside a published graph in the campus database, beside its
release, where a Brain reading Postgres finds it (PostgresEntityFacts.meaning_index).

    python scripts/publish_meaning_index.py --dir PUBLISHED_DIR

Run it after scripts/build_graph.py and scripts/build_meaning_index.py, with DATABASE_URL naming a
login that may write rockygpt_v2.release_meaning_indexes (the data publisher's); it never prints
any part of it. The vectors are stored in 8 bits with one scale each (MeaningIndex.quantize), a
quarter of the float file. The row is replaced for the same release; it is removed with its
release, and a Brain uses it only while its release and identity publication are the active ones.
"""

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import psycopg

from rockygpt_brain.retrieval.graph_store import POINTER
from rockygpt_brain.retrieval.meaning import MEANING_SUFFIX, MeaningIndex

UPSERT = """
INSERT INTO rockygpt_v2.release_meaning_indexes
  (dataset_version_id, identity_hash, model, dimensions, meta, vectors, scales, content_hash)
SELECT v.id, %(identity_hash)s, %(model)s, %(dimensions)s, %(meta)s::jsonb, %(vectors)s,
       %(scales)s, %(content_hash)s
FROM rockygpt_v2.dataset_versions v WHERE v.version = %(version)s
ON CONFLICT (dataset_version_id) DO UPDATE SET
  identity_hash = EXCLUDED.identity_hash, model = EXCLUDED.model,
  dimensions = EXCLUDED.dimensions, meta = EXCLUDED.meta, vectors = EXCLUDED.vectors,
  scales = EXCLUDED.scales, content_hash = EXCLUDED.content_hash, created_at = now()
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dir", required=True, type=Path, help="the published graph directory")
    args = parser.parse_args(argv)
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        print("DATABASE_URL is not set.", file=sys.stderr)
        return 2
    active = json.loads((args.dir / POINTER).read_text())
    release_file = args.dir / active["file"]
    index = MeaningIndex.load(release_file)
    if index is None:
        print("No meaning index beside the active release; run build_meaning_index.py first.",
              file=sys.stderr)
        return 1
    if (index.dataset_version, index.identity_hash) != (active["dataset_version"],
                                                        active["identity_hash"]):
        print("The meaning index was built for another release.", file=sys.stderr)
        return 1
    meta_text = release_file.with_suffix(MEANING_SUFFIX).read_text()
    vectors, scales = index.quantize()
    digest = hashlib.sha256(meta_text.encode() + vectors + scales).hexdigest()
    with psycopg.connect(url, connect_timeout=10) as connection:
        written = connection.execute(UPSERT, {
            "identity_hash": index.identity_hash, "model": index.model,
            "dimensions": index.dimensions, "meta": meta_text, "vectors": vectors,
            "scales": scales, "content_hash": digest, "version": index.dataset_version,
        }).rowcount
    if written != 1:
        print(f"Release {index.dataset_version} is not in the database.", file=sys.stderr)
        return 1
    print(f"meaning index for {index.dataset_version}: {len(vectors) / 1e6:.1f} MB stored "
          f"({len(index.owner)} unit vectors, {index.dimensions} dimensions)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
