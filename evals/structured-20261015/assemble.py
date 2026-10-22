"""Build labeler batches from the slots and the writers' questions, then compare the two labelers.

    python3 assemble.py batches <outdir>     # labeler batches (message + slot + official pages)
    python3 assemble.py compare <outdir>     # after l1-*.json and l2-*.json: key + disputes

A case's key is kept when both labelers agree on `published`, `soft` and share an accepted value
(the union of their values is kept); everything else is a dispute for Dan to rule on.
"""

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
BATCH = 20


def norm(value: str) -> str:
    return re.sub(r"[^a-z0-9@.]", "", value.lower())


def batches(out: Path) -> None:
    slots = json.loads((HERE / "slots.json").read_text())
    asked = {q["id"]: q["message"] for n in (1, 2)
             for q in json.loads((HERE / f"questions-{n}.json").read_text())}
    assert set(asked) == {s["id"] for s in slots}, "every slot needs one question"
    cases = [{"id": s["id"], "message": asked[s["id"]], "kind": s["kind"], "entity": s["entity"],
              "ask": s["ask"], **{k: s[k] for k in ("day", "time") if k in s}} for s in slots]
    (HERE / "cases.json").write_text(json.dumps(cases, indent=1, ensure_ascii=False) + "\n")
    out.mkdir(parents=True, exist_ok=True)
    full = [{**c, "sources": s["sources"]} for c, s in zip(cases, slots)]
    for n in range(0, len(full), BATCH):
        (out / f"label-{n // BATCH + 1}.json").write_text(
            json.dumps(full[n:n + BATCH], indent=1, ensure_ascii=False) + "\n")
    print(f"{len(cases)} cases -> cases.json, {(len(full) + BATCH - 1) // BATCH} batches -> {out}")


def compare(out: Path) -> None:
    cases = {c["id"]: c for c in json.loads((HERE / "cases.json").read_text())}
    labels = {g: {r["id"]: r for p in sorted(out.glob(f"{g}-*.json"))
                  for r in json.loads(p.read_text())} for g in ("l1", "l2")}
    key, disputes = [], []
    for i, case in cases.items():
        a, b = labels["l1"][i], labels["l2"][i]
        shared = {norm(v) for v in a["accept"]} & {norm(v) for v in b["accept"]}
        agree = a["published"] == b["published"] and a["soft"] == b["soft"] and (
            not a["published"] or shared)
        row = {**case, "published": a["published"], "soft": a["soft"],
               "accept": sorted(set(a["accept"]) | set(b["accept"])),
               "expected": a["expected"], "notes": [a.get("note", ""), b.get("note", "")]}
        if not agree:
            row = {**case, "dispute": {"l1": a, "l2": b}}
            disputes.append(row)
        key.append(row)
    (out / "key-draft.json").write_text(json.dumps(key, indent=1, ensure_ascii=False) + "\n")
    print(f"{len(key)} cases, {len(disputes)} disputes, "
          f"{sum(1 for k in key if k.get('soft'))} soft, "
          f"{sum(1 for k in key if 'dispute' not in k and not k['published'])} not published")


if __name__ == "__main__":
    {"batches": batches, "compare": compare}[sys.argv[1]](Path(sys.argv[2]))
