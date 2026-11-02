"""Combine the two graders per batch for the mixed A/B run: worst of two per item, then unmask.

  python grading/merge_ab.py   (reads grading/batch-*.json, labels-*-{A,B}.json, mapping.json)
Batches mixed both runs' answers under hidden ids (h001...), each question's two answers in two
different batches; mapping.json was kept out of the graders' folder until all labels were in."""
import collections, json
from pathlib import Path
G = Path(__file__).parent
RANK = ["wrong", "declined_wrong", "asked_wrong", "incomplete", "asked_ok", "declined_ok", "correct"]
mapping = json.loads((G / "mapping.json").read_text())
items = {i["id"]: i for k in range(1, 5) for i in json.loads((G / f"batch-{k}.json").read_text())}
merged, agree = [], 0
for k in range(1, 5):
    ids = [i["id"] for i in json.loads((G / f"batch-{k}.json").read_text())]
    a = {x["id"]: x for x in json.loads((G / f"labels-{k}-A.json").read_text())}
    b = {x["id"]: x for x in json.loads((G / f"labels-{k}-B.json").read_text())}
    for h in ids:
        la, lb = a[h], b[h]
        worst = min((la, lb), key=lambda x: RANK.index(x["verdict"]))
        agree += la["verdict"] == lb["verdict"]
        merged.append({"hid": h, **mapping[h], "verdict": worst["verdict"],
                       "g1": la["verdict"], "g2": lb["verdict"],
                       "severity": max((la["severity"], lb["severity"]),
                                       key=["none", "minor", "serious"].index),
                       "both_serious_wrong": la["verdict"] == lb["verdict"] == "wrong"
                       and la["severity"] == lb["severity"] == "serious",
                       "noise": la["unasked_noise"] or lb["unasked_noise"],
                       "question": items[h]["question"], "answer": items[h]["answer"],
                       "note_1": la["note"], "note_2": lb["note"]})
merged.sort(key=lambda x: (x["question_id"], x["side"]))
(G.parent / "labels-merged.json").write_text(json.dumps(merged, indent=1, ensure_ascii=False))
print(f"{len(merged)} items; graders agreed on {agree}")
for side in "AB":
    m = [x for x in merged if x["side"] == side]
    c = collections.Counter(x["verdict"] for x in m)
    good = c["correct"] + c["declined_ok"] + c["asked_ok"]
    print(f"{side}: {dict(c)}; good {good}/{len(m)} = {good / len(m):.0%}; "
          f"wrong-serious by both graders: {sum(x['both_serious_wrong'] for x in m)}")
