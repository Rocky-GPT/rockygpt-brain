"""Build the answer cards beside the active release graph: the release's how-to tasks, listed page
by page, joined into one card per task by rules (rockygpt_brain/retrieval/card_join.py).

    python scripts/build_answer_cards.py --dir PUBLISHED_DIR

scripts/build_meaning_index.py runs it at the end of every index build; run it alone to join an
index already built again (after a change to the rules, or on a laptop's copy of a release). Two
tasks are the same task when the questions written for them mean the same, and those vectors are
in the meaning index. It calls no model and needs no key. It writes release-*.cards.json next to
the active release file; scripts/publish_graph.py shares it with the other release files. Which
term is over and which page is old are as of the day it runs (--today for another day).
"""

import argparse
import json
import os
import sys
import time
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import numpy

from rockygpt_brain.retrieval import answer_cards, card_join
from rockygpt_brain.retrieval.entity_facts import CAMPUS_TIMEZONE
from rockygpt_brain.retrieval.graph_store import POINTER, GraphOnlyFacts
from rockygpt_brain.retrieval.meaning import MEANING_SUFFIX, VECTORS_SUFFIX


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the answer cards for the active release.")
    parser.add_argument("--dir", required=True, type=Path, help="the published graph directory")
    parser.add_argument("--today", type=date.fromisoformat, default=None,
                        help="the day terms and page ages are judged on (default: today)")
    args = parser.parse_args(argv)
    started = time.monotonic()
    today = args.today or datetime.now(CAMPUS_TIMEZONE).date()
    pointer = json.loads((args.dir / POINTER).read_text())
    release_file = args.dir / pointer["file"]
    try:
        meta = json.loads(release_file.with_suffix(MEANING_SUFFIX).read_text())
        flat = numpy.fromfile(release_file.with_suffix(VECTORS_SUFFIX), dtype=numpy.float32)
    except OSError:
        print("No meaning index beside the active release; run build_meaning_index.py first.",
              file=sys.stderr)
        return 1
    version, identity, tasks, names = answer_cards.join_input(GraphOnlyFacts(args.dir), today)
    if (version, identity) != (pointer["dataset_version"], pointer["identity_hash"]) or (
            meta.get("dataset_version"), meta.get("identity_hash")) != (version, identity):
        print("The meaning index or the release changed while the cards were built; run it again.",
              file=sys.stderr)
        return 1
    cards = card_join.build(tasks, card_join.task_vectors(meta, flat, tasks), names)
    found = card_join.cards_file(version, identity, today, cards)
    # Written whole and renamed into place, so a running Brain never reads half of it.
    temporary = release_file.with_suffix(card_join.CARDS_SUFFIX + ".tmp")
    temporary.write_text(json.dumps(found, ensure_ascii=False, separators=(",", ":")))
    os.replace(temporary, release_file.with_suffix(card_join.CARDS_SUFFIX))
    status = Counter(card["status"] for card in cards)
    joined = [card for card in cards if len(card["members"]) > 1]
    print(f"answer cards for {version}: {len(cards)} cards from {len(tasks)} tasks on "
          f"{len({task.record_id for task in tasks})} pages; {len(joined)} join "
          f"{sum(len(card['members']) for card in joined)} tasks from two or more pages; "
          f"{status[card_join.READY]} ready, {status[card_join.NEEDS_LOOK]} need a look, "
          f"{status[card_join.DATE_PASSED]} date passed; "
          f"{sum(1 for card in cards if card['near'])} have a near-copy, "
          f"{sum(1 for card in cards if card['ahead'])} of them one whose page is ahead; "
          f"{time.monotonic() - started:.0f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
