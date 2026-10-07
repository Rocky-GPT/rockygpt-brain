"""Score a running Brain on the frozen ambiguity cases.

    python3 evals/ambiguity/run.py [port] [out.json]

The Brain must run with BRAIN_OUTPUT=json. Each case is sent once. What the Brain did is read
from its Fact Packet:
  asked     the packet names ambiguities, or carries a clarification notice
  answered  the packet holds facts for at least one office (those offices are listed)
  other     anything else (not found, unsupported, an error)
A case expecting "ask" passes when the Brain asked; one expecting "answer" passes when it answered
and the expected office is among the offices; "soft" cases are run and reported, never scored.
"""

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

CASES = Path(__file__).with_name("cases.json")


def ask(port: str, messages: list[dict[str, str]]) -> dict:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/chat", data=json.dumps({"messages": messages}).encode(),
        headers={"content-type": "application/json", "x-rockygpt-diagnostics": "1"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310 - local http
            return json.load(response)
    except urllib.error.HTTPError as error:
        return {"error": json.load(error)}


def observe(body: dict) -> dict:
    packet = body.get("facts")
    if not isinstance(packet, dict):
        return {"observed": "other", "detail": json.dumps(body.get("error"))[:120], "offices": []}
    offices = sorted({f["subject"]["name"] for f in packet["facts"] if not f.get("purpose")})
    kinds = [n["type"] for n in packet["notices"]]
    if packet["ambiguities"] or "clarification" in kinds:
        choices = [c["name"] for a in packet["ambiguities"] for c in a["candidates"]]
        return {"observed": "asked", "offices": offices,
                "detail": ("choices: " + ", ".join(choices)) if choices else "generic question"}
    if offices:
        return {"observed": "answered", "detail": ", ".join(offices), "offices": offices}
    return {"observed": "other", "detail": f"{packet['status']} {kinds}", "offices": []}


def judge(case: dict, seen: dict) -> str:
    if case["expected"] == "soft":
        return "soft"
    if case["expected"] == "ask":
        return "ok" if seen["observed"] == "asked" else "MISS"
    if seen["observed"] != "answered":
        return "MISS"
    return "ok" if case["office"] in seen["offices"] else "WRONG OFFICE"


def summarize(rows: list[dict]) -> dict:
    scored = [r for r in rows if r["verdict"] != "soft"]
    out = {"scored": len(scored), "ok": sum(r["verdict"] == "ok" for r in scored)}
    for group in ("ask", "answer"):
        mine = [r for r in scored if r["expected"] == group]
        out[group] = {"scored": len(mine), "ok": sum(r["verdict"] == "ok" for r in mine)}
    return out


def main(port: str, out_path: str | None) -> None:
    rows = []
    for case in json.loads(CASES.read_text()):
        body = ask(port, [*case["history"], {"role": "user", "content": case["question"]}])
        seen = observe(body)
        calls = (body.get("metrics") or {}).get("modelCalls")
        row = {**case, **seen, "verdict": judge(case, seen), "modelCalls": calls}
        rows.append(row)
        print(f"{row['verdict']:12} exp={case['expected']:6} got={seen['observed']:8} "
              f"{case['id']:13} {case['question'][:44]!r:48} -> {seen['detail'][:60]}")
    summary = summarize(rows)
    print(f"\n{summary['ok']} of {summary['scored']} scored cases as expected  "
          f"(ask {summary['ask']['ok']}/{summary['ask']['scored']}, "
          f"answer {summary['answer']['ok']}/{summary['answer']['scored']}; "
          f"{len(rows) - summary['scored']} soft, not scored)")
    if out_path:
        Path(out_path).write_text(json.dumps({"summary": summary, "rows": rows}, indent=1))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "8000", sys.argv[2] if len(sys.argv) > 2 else None)
