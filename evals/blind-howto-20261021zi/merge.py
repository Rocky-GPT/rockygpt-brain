"""Combine the two graders per batch: worst-of-two verdict per item, agreement, and every miss."""
import collections, json
from pathlib import Path
HERE = Path(__file__).parent
RANK = ["wrong", "declined_wrong", "asked_wrong", "incomplete", "asked_ok", "declined_ok", "correct"]
items = {i["id"]: i for k in range(1, 5) for i in json.loads((HERE / f"batch-{k}.json").read_text())}
merged, agree = [], 0
for k in range(1, 5):
    a = {x["id"]: x for x in json.loads((HERE / f"labels-{k}-A.json").read_text())}
    b = {x["id"]: x for x in json.loads((HERE / f"labels-{k}-B.json").read_text())}
    for item_id in [i["id"] for i in json.loads((HERE / f"batch-{k}.json").read_text())]:
        la, lb = a[item_id], b[item_id]
        worst = min((la, lb), key=lambda x: RANK.index(x["verdict"]))
        agree += la["verdict"] == lb["verdict"]
        merged.append({"id": item_id, "verdict": worst["verdict"], "A": la["verdict"], "B": lb["verdict"],
                       "severity": max((la["severity"], lb["severity"]), key=["none", "minor", "serious"].index),
                       "both_serious_wrong": la["verdict"] == lb["verdict"] == "wrong"
                       and la["severity"] == lb["severity"] == "serious",
                       "noise": la["unasked_noise"] or lb["unasked_noise"],
                       "question": items[item_id]["question"], "answer": items[item_id]["answer"],
                       "note_A": la["note"], "note_B": lb["note"]})
(HERE / "labels-merged.json").write_text(json.dumps(merged, indent=1, ensure_ascii=False))
counts = collections.Counter(m["verdict"] for m in merged)
good = counts["correct"] + counts["declined_ok"] + counts["asked_ok"]
print(f"{len(merged)} items; graders agreed on {agree}; worst of two: {dict(counts)}")
print(f"good (correct + declined_ok + asked_ok): {good}/{len(merged)} = {good / len(merged):.0%}; "
      f"wrong-serious by both graders: {sum(m['both_serious_wrong'] for m in merged)}")
