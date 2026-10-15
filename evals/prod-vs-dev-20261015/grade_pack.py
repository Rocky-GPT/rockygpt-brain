"""Write blind grading batches from two runs, and score the graders' labels.

    python3 grade_pack.py pack <prod.json> <dev.json> <outdir>      # batches + hidden key
    python3 grade_pack.py tally <outdir>                           # after graders write g*.json

Which side is A is drawn per case with a fixed seed and kept in key.json, which graders never see.
Each batch is graded by two graders (g1-*, g2-*); a label counts only when both agree, every
disagreement is listed, and a case is scored only when both say it is scorable.
"""

import json
import random
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent
SEED, BATCH = 20261015, 20
LABELS = ("answered", "partial", "referral", "declined", "wrong")


def answer_of(row: dict) -> str:
    if row["http"] != 200:
        return f"[no answer: HTTP {row['http']} {json.dumps(row['body'])[:200]}]"
    return row["answer"] or "[empty answer]"


def pack(prod: Path, dev: Path, out: Path) -> None:
    cases = json.loads((HERE / "cases.json").read_text())
    runs = {"prod": {r["id"]: r for r in json.loads(prod.read_text())},
            "dev": {r["id"]: r for r in json.loads(dev.read_text())}}
    rng = random.Random(SEED)
    key, items = {}, []
    for case in cases:
        first = rng.choice(("prod", "dev"))
        second = "dev" if first == "prod" else "prod"
        key[case["id"]] = {"A": first, "B": second}
        items.append({"id": case["id"], "question": case["message"], "official_page": case["url"],
                      "official_answer": case["official_answer"],
                      "A": answer_of(runs[first][case["id"]]),
                      "B": answer_of(runs[second][case["id"]])})
    out.mkdir(parents=True, exist_ok=True)
    (out / "key.json").write_text(json.dumps(key, indent=1) + "\n")
    for n in range(0, len(items), BATCH):
        name = out / f"batch-{n // BATCH + 1}.json"
        name.write_text(json.dumps(items[n:n + BATCH], indent=1, ensure_ascii=False) + "\n")
    print(f"{len(items)} cases in {(len(items) + BATCH - 1) // BATCH} batches -> {out}")


def tally(out: Path) -> None:
    key = json.loads((out / "key.json").read_text())
    graders: dict[str, dict[str, dict]] = {"g1": {}, "g2": {}}
    for grader in graders:
        for path in sorted(out.glob(f"{grader}-*.json")):
            for row in json.loads(path.read_text()):
                assert row["A"] in LABELS and row["B"] in LABELS, (path, row)
                graders[grader][row["id"]] = row
    ids = [i for i in key if i in graders["g1"] and i in graders["g2"]]
    final, disputes, unscorable = {"prod": Counter(), "dev": Counter()}, [], []
    rows = []
    for i in ids:
        a, b = graders["g1"][i], graders["g2"][i]
        if not (a["scorable"] and b["scorable"]):
            unscorable.append(i)
            continue
        row = {"id": i}
        for side in ("A", "B"):
            system = key[i][side]
            if a[side] == b[side]:
                final[system][a[side]] += 1
                row[system] = a[side]
            else:
                final[system]["disputed"] += 1
                row[system] = f"{a[side]}|{b[side]}"
                disputes.append({"id": i, "system": system, "g1": a[side], "g2": b[side],
                                 "notes": [a.get("note"), b.get("note")]})
        row["notes"] = [a.get("note"), b.get("note")]
        rows.append(row)
    result = {"graded": len(ids), "scored": len(rows), "unscorable": unscorable,
              "prod": dict(final["prod"]), "dev": dict(final["dev"]), "disputes": disputes,
              "rows": rows}
    (out / "result.json").write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n")
    print(json.dumps({k: result[k] for k in ("graded", "scored", "prod", "dev")}, indent=1))
    print(f"unscorable {len(unscorable)}, disputed labels {len(disputes)}")


if __name__ == "__main__":
    if sys.argv[1] == "pack":
        pack(Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]))
    else:
        tally(Path(sys.argv[2]))
