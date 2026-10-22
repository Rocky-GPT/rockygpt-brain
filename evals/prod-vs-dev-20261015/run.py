"""Ask one Brain every frozen case once, one at a time, and keep each full response.

    python3 run.py <chat url> <out.json> [--diagnostics] [--cases <cases.json>]

Production: https://rockygpt-brain.onrender.com/v1/chat. Dev (written answers, graph-only):
http://127.0.0.1:8013/v1/chat --diagnostics. Each case is a new one-message conversation. A busy
reply (429 "busy") or a network failure is retried once after 10 s; anything else is kept as is.
Rows are written after every case, so a stopped run keeps what it has and resumes where it ended.
"""

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

CASES = Path(__file__).with_name("cases.json")


def post(url: str, message: str, diagnostics: bool) -> tuple[int, dict]:
    headers = {"content-type": "application/json", "accept": "application/json"}
    if diagnostics:
        headers["x-rockygpt-diagnostics"] = "1"
    body = json.dumps({"messages": [{"role": "user", "content": message}]}).encode()
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310 - fixed hosts
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        try:
            return error.code, json.load(error)
        except ValueError:
            return error.code, {}


def ask(url: str, message: str, diagnostics: bool) -> dict:
    for attempt in (1, 2):
        started = time.monotonic()
        try:
            status, body = post(url, message, diagnostics)
        except (urllib.error.URLError, TimeoutError) as error:
            status, body = 0, {"transport_error": str(error)}
        seconds = round(time.monotonic() - started, 2)
        if attempt == 1 and (status == 0 or (status == 429 and body.get("error") == "busy")):
            time.sleep(10)
            continue
        return {"http": status, "seconds": seconds, "answer": body.get("answer") or "",
                "status": body.get("status"), "body": body, "attempts": attempt}
    raise AssertionError("unreachable")


def main() -> None:
    url, out = sys.argv[1], Path(sys.argv[2])
    diagnostics = "--diagnostics" in sys.argv[3:]
    cases_path = Path(sys.argv[sys.argv.index("--cases") + 1]) if "--cases" in sys.argv else CASES
    cases = json.loads(cases_path.read_text())
    rows = json.loads(out.read_text()) if out.exists() else []
    done = {r["id"] for r in rows}
    for case in cases:
        if case["id"] in done:
            continue
        row = {"id": case["id"], "message": case["message"], **ask(url, case["message"],
                                                                    diagnostics)}
        rows.append(row)
        out.write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n")
        print(f"{row['id']} http={row['http']} {row['seconds']}s {row['status']} "
              f"{row['answer'][:70]!r}", flush=True)
    print(f"{len(rows)} rows -> {out}")


if __name__ == "__main__":
    main()
