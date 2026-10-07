"""Score a running Brain on the frozen field-selection cases.

    python3 evals/overfetch/run.py [port] [out.json] [baseline.json]

The Brain must run with BRAIN_OUTPUT=json. Each case is sent once. What the Brain asked for is read
from its Fact Packet's `request.fields`: the fields it fetched for the office, whatever the facts
turned out to be. A case is judged against the fields its labelers agreed the student asked for:
  ok         it fetched exactly those fields
  OVER       it fetched those and more
  UNDER      it left out a field the student asked for
  BOTH       it left one out and fetched others the student did not ask for
  NO LOOKUP  it looked nothing up (it asked a question, or could not resolve the office)
A general request to contact or reach an office ("generic") needs email and phones; the room
(`offices`) is also allowed, because the Brain reads the three contact fields for it. Anything else
is OVER. "soft" cases (writer and labelers disagreed) are run and reported, never scored.
"""

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

CASES = Path(__file__).with_name("cases.json")
GENERIC_EXTRA = {"offices"}


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
        return {"fields": [], "looked_up": False, "detail": json.dumps(body.get("error"))[:120]}
    request = packet["request"]
    fields = sorted(set(request["fields"]))
    return {"fields": fields, "looked_up": bool(request["entities"]) and bool(fields),
            "detail": f"{packet['status']}"}


def allowed(case: dict) -> set[str]:
    """The fields a good answer may fetch: those asked for, and the room for a general request."""
    return set(case["needs"]) | (GENERIC_EXTRA if case.get("generic") else set())


def judge(case: dict, seen: dict) -> str:
    if not case["scored"]:
        return "soft"
    if not seen["looked_up"]:
        return "NO LOOKUP"
    got, need = set(seen["fields"]), set(case["needs"])
    missing, extra = need - got, got - allowed(case)
    if missing and extra:
        return "BOTH"
    return "UNDER" if missing else "OVER" if extra else "ok"


def summarize(rows: list[dict]) -> dict:
    scored = [r for r in rows if r["verdict"] != "soft"]
    verdicts = ("ok", "OVER", "UNDER", "BOTH", "NO LOOKUP")
    count = {v: sum(r["verdict"] == v for r in scored) for v in verdicts}
    extras = sum(len(set(r["fields"]) - allowed(r)) for r in scored if r["looked_up"])
    return {"scored": len(scored), **count, "under": count["UNDER"] + count["BOTH"],
            "extra_fields": extras}


def bar(summary: dict, baseline: dict | None) -> list[str]:
    """What the pass bar (fixed before any run, see README) says about this run; empty is a pass."""
    failed = []
    if summary["under"] > 1 or (baseline and summary["under"] > baseline["under"]):
        failed.append(f"left out a field the student asked for in {summary['under']} cases")
    if summary["ok"] < 0.9 * summary["scored"]:
        failed.append(
            f"only {summary['ok']} of {summary['scored']} cases fetched exactly what was asked")
    if baseline and summary["NO LOOKUP"] > baseline["NO LOOKUP"]:
        failed.append(
            f"{summary['NO LOOKUP']} cases looked nothing up, up from {baseline['NO LOOKUP']}")
    return failed


def main(port: str, out_path: str | None, baseline_path: str | None) -> None:
    rows = []
    for case in json.loads(CASES.read_text()):
        body = ask(port, [*case["history"], {"role": "user", "content": case["question"]}])
        seen = observe(body)
        row = {**case, **seen, "verdict": judge(case, seen)}
        rows.append(row)
        print(f"{row['verdict']:9} {case['id']:13} {case['question'][:42]!r:46} "
              f"need={'+'.join(case['needs']):22} got={'+'.join(seen['fields'])}")
    summary = summarize(rows)
    print(f"\n{summary['ok']} of {summary['scored']} scored cases fetched exactly what was asked; "
          f"OVER {summary['OVER']}, UNDER {summary['UNDER']}, BOTH {summary['BOTH']}, "
          f"NO LOOKUP {summary['NO LOOKUP']}; {summary['extra_fields']} extra fields in all; "
          f"{len(rows) - summary['scored']} soft, not scored")
    baseline = json.loads(Path(baseline_path).read_text())["summary"] if baseline_path else None
    failed = bar(summary, baseline)
    print("pass bar:", "met" if not failed else "NOT met: " + "; ".join(failed))
    if out_path:
        Path(out_path).write_text(json.dumps({"summary": summary, "rows": rows}, indent=1))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "8000",
         sys.argv[2] if len(sys.argv) > 2 else None, sys.argv[3] if len(sys.argv) > 3 else None)
