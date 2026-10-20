"""Send the frozen question set once, one single-turn message each, keeping every full response.

  python run_blind.py <port> <out.json>
Verifies the question file's hash first; refuses to run on a changed file."""
import hashlib, json, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).parent
port, out = sys.argv[1], sys.argv[2]
blob = (HERE / "questions-frozen.json").read_text()
want = (HERE / "questions-frozen.sha256").read_text().split()[0]
if hashlib.sha256(blob.encode()).hexdigest() != want:
    sys.exit("the frozen question file changed")
cases = json.loads(blob)

def ask(case):
    req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat",
        data=json.dumps({"messages": [{"role": "user", "content": case["question"]}]}).encode(),
        headers={"content-type": "application/json", "x-rockygpt-diagnostics": "1"})
    t0 = time.time()
    try:
        body = json.load(urllib.request.urlopen(req, timeout=170))
    except Exception as exc:  # noqa: BLE001
        body = {"error": str(exc)[:300]}
    return {**case, "seconds": round(time.time() - t0, 1), "body": body}

with ThreadPoolExecutor(4) as pool:
    rows = list(pool.map(ask, cases))
Path(out).write_text(json.dumps(rows, indent=1, ensure_ascii=False))
import collections
print(len(rows), collections.Counter((r["body"].get("status") or "ERROR") for r in rows))
