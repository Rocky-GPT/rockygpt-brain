"""Split the run's answers into four grading batches, as the earlier blind checks did.

  python make_batches.py <answers.json> <outdir>
Each item: id, question, the reply's answer text, its answered_at campus clock and cited URLs."""
import json, sys
from pathlib import Path

rows = json.loads(Path(sys.argv[1]).read_text())
out = Path(sys.argv[2])
items = []
for r in rows:
    body = r["body"]
    answer = body.get("answer") or f"[error, no answer: {json.dumps(body.get('error') or body)[:200]}]"
    items.append({"id": r["id"], "question": r["question"], "answer": answer,
                  "answered_at": (body.get("facts") or {}).get("request", {}).get("asOf")
                  or (body.get("metrics") or {}).get("answeredAt") or r.get("asked_at"),
                  "cited_urls": [c.get("url") for c in body.get("citations") or []]})
for k in range(4):
    (out / f"batch-{k + 1}.json").write_text(
        json.dumps(items[k * 15:(k + 1) * 15], indent=1, ensure_ascii=False))
print(len(items), "items in 4 batches")
