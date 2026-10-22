"""Blind grading for the structured comparison: pack batches with the key, then tally.

    python3 grade.py pack <prod.json> <dev.json> <outdir>
    python3 grade.py tally <outdir>              # after g1-*.json and g2-*.json

Same method as ../prod-vs-dev-20261015/grade_pack.py: A/B drawn per case with a fixed seed (hidden
in key.json), two graders per batch following grading.md, a label counts only when both agree,
and soft cases are reported, never scored.
"""

import json
import random
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent
SEED, BATCH = 20261015, 20
LABELS = ("correct", "incomplete", "declined", "wrong", "unverified")


def answer_of(row: dict) -> str:
    if row["http"] != 200:
        return f"[no answer: HTTP {row['http']} {json.dumps(row['body'])[:200]}]"
    return row["answer"] or "[empty answer]"


def pack(prod: Path, dev: Path, out: Path) -> None:
    key = {k["id"]: k for k in json.loads((HERE / "key.json").read_text())}
    runs = {"prod": {r["id"]: r for r in json.loads(prod.read_text())},
            "dev": {r["id"]: r for r in json.loads(dev.read_text())}}
    rng = random.Random(SEED)
    sides, items = {}, []
    for i, case in key.items():
        first = rng.choice(("prod", "dev"))
        sides[i] = {"A": first, "B": "dev" if first == "prod" else "prod"}
        items.append({"id": i, "question": case["message"], "published": case["published"],
                      "accept": case["accept"], "expected": case["expected"],
                      "A": answer_of(runs[sides[i]["A"]][i]),
                      "B": answer_of(runs[sides[i]["B"]][i])})
    out.mkdir(parents=True, exist_ok=True)
    (out / "sides.json").write_text(json.dumps(sides, indent=1) + "\n")
    for n in range(0, len(items), BATCH):
        (out / f"batch-{n // BATCH + 1}.json").write_text(
            json.dumps(items[n:n + BATCH], indent=1, ensure_ascii=False) + "\n")
    print(f"{len(items)} cases -> {out}")


def tally(out: Path) -> None:
    key = {k["id"]: k for k in json.loads((HERE / "key.json").read_text())}
    sides = json.loads((out / "sides.json").read_text())
    grades = {g: {r["id"]: r for p in sorted(out.glob(f"{g}-*.json"))
                  for r in json.loads(p.read_text())} for g in ("g1", "g2")}
    count = {"prod": Counter(), "dev": Counter()}
    by_kind: dict[str, dict[str, Counter]] = {}
    rows, disputes = [], []
    for i, case in key.items():
        a, b = grades["g1"][i], grades["g2"][i]
        row = {"id": i, "kind": case["kind"], "soft": case["soft"]}
        for side in ("A", "B"):
            assert a[side] in LABELS and b[side] in LABELS, (i, a, b)
            system = sides[i][side]
            label = a[side] if a[side] == b[side] else "disputed"
            row[system] = label if label != "disputed" else f"{a[side]}|{b[side]}"
            if label == "disputed":
                disputes.append({"id": i, "system": system, "g1": a[side], "g2": b[side],
                                 "notes": [a.get("note"), b.get("note")]})
            if not case["soft"]:
                count[system][label] += 1
                by_kind.setdefault(case["kind"], {"prod": Counter(), "dev": Counter()})[
                    system][label] += 1
        row["notes"] = [a.get("note"), b.get("note")]
        rows.append(row)
    result = {"scored": sum(1 for k in key.values() if not k["soft"]),
              "soft": [i for i, k in key.items() if k["soft"]],
              "prod": dict(count["prod"]), "dev": dict(count["dev"]),
              "by_kind": {k: {s: dict(c) for s, c in v.items()} for k, v in by_kind.items()},
              "disputes": disputes, "rows": rows}
    (out / "result.json").write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n")
    print(json.dumps({k: result[k] for k in ("scored", "prod", "dev", "by_kind")}, indent=1))
    print(f"soft {len(result['soft'])}, disputed labels {len(disputes)}")


if __name__ == "__main__":
    if sys.argv[1] == "pack":
        pack(Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]))
    else:
        tally(Path(sys.argv[2]))
