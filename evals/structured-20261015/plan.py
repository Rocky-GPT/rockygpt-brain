"""Draw what each structured question asks about (code), and the official pages to label it from.

    python3 -I plan.py <rockygpt-data/data/raw> <offices.json>

`offices.json` is the dev graph's office list with each office's source page URLs (the Ramapo
pages its facts came from). Writers later word each slot as a student would; labelers read the
attached official text and write the expected answer. Nothing here reads either Brain's answer.

Mix (60): 36 offices, one ask each (email 7, phone 7, room 6, hours on a named day 8, how to
reach 5, fax 3); 10 dining-venue hours on a named day; 14 shuttle departures on a named day.
No "now", "today" or menu questions: their answers move with the clock and the menu date.
"""

import json
import random
import re
import sys
from pathlib import Path

SEED = 20261015
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
OFFICE_ASKS = ["email"] * 7 + ["phone"] * 7 + ["room"] * 6 + ["hours_on_day"] * 8 + \
    ["how_to_reach"] * 5 + ["fax"] * 3
STYLES = ["short", "natural", "natural", "typo"]
PAGE_LIMIT = 20000
SHUTTLE = {
    "Weekday Roadrunner Express": ("ramapo-roadrunner-express-shuttle", DAYS[:5]),
    "Mid-Day Weekday Express Train": ("shuttle-mid-day-weekday-express-train-schedule", DAYS[:5]),
    "Saturday Roadrunner Express": ("saturday-shuttle-schedule", ["Saturday"]),
    "Sunday Roadrunner Express": ("sunday-shuttle-schedule", ["Sunday"]),
}
SHUTTLE_ASKS = ["first_from_campus", "last_from_campus", "next_from_campus_after",
                "next_from_campus_after", "last_arrival_on_campus"]


def page_text(page: dict, words: list[str]) -> str:
    parts = [f"## {s.get('heading') or ''}\n{s.get('text') or ''}" for s in page.get("sections") or []]
    parts += [json.dumps(t, ensure_ascii=False) for t in page.get("tables") or []]
    parts += [json.dumps(c, ensure_ascii=False) for c in page.get("contacts") or []]
    text = "\n".join(parts)
    if len(text) > PAGE_LIMIT and words:
        hit = [p for p in parts if any(w in p.lower() for w in words)]
        text = "\n".join(hit)
    return text[:PAGE_LIMIT]


def main() -> None:
    raw = Path(sys.argv[1])
    pages: dict[str, dict] = {}
    for name in ("office-pages.raw.json", "academic-sites.raw.json", "transportation.raw.json",
                 "directory.raw.json", "reviewed-contacts.raw.json"):
        for page in json.loads((raw / name).read_text())["pages"]:
            pages.setdefault(page["url"], page)
    rng = random.Random(SEED)
    slots = []

    offices = [o for o in json.loads(Path(sys.argv[2]).read_text())
               if any(u in pages for u in o["urls"])]
    rng.shuffle(offices)
    asks = OFFICE_ASKS[:]
    rng.shuffle(asks)
    for office, ask in zip(offices, asks):
        words = [w for w in re.findall(r"[a-z]{4,}", office["name"].lower())
                 if w not in {"office", "center", "services", "program", "programs"}]
        slot = {"kind": "office", "entity": office["name"], "ask": ask}
        if ask == "hours_on_day":
            slot["day"] = rng.choice(DAYS)
        slot["sources"] = [{"url": u, "text": page_text(pages[u], words)}
                           for u in office["urls"] if u in pages]
        slots.append(slot)

    dining = json.loads((raw / "dining-hours-rendered.raw.json").read_text())
    combos = [(v["name"], a, d) for v in dining["venues"]
              for a in ("open_hours_on_day", "closing_time_on_day") for d in DAYS]
    for name, ask, day in rng.sample(combos, 10):
        slots.append({"kind": "dining", "entity": name, "ask": ask, "day": day,
                      "sources": [{"url": dining["sourceUrl"], "rendered_at": dining["renderedAt"],
                                   "text": json.dumps(dining["venues"], ensure_ascii=False)}]})

    seen: set[tuple] = set()
    while len(seen) < 14:
        route = rng.choice(sorted(SHUTTLE))
        slug, days = SHUTTLE[route]
        url = f"https://www.ramapo.edu/about/transportation-services/{slug}/"
        slot = {"kind": "shuttle", "entity": route, "ask": rng.choice(SHUTTLE_ASKS),
                "day": rng.choice(days)}
        if slot["ask"] == "next_from_campus_after":
            hour = rng.randint(8, 20)
            slot["time"] = f"{hour % 12 or 12}:{rng.choice(['00', '15', '30', '45'])} " \
                           f"{'AM' if hour < 12 else 'PM'}"
        key = (route, slot["ask"], slot["day"], slot.get("time"))
        if key in seen:
            continue
        seen.add(key)
        slot["sources"] = [{"url": url, "text": page_text(pages[url], [])}]
        slots.append(slot)

    for n, slot in enumerate(slots, 1):
        slot["id"] = f"s{n:03d}"
        slot["style"] = rng.choice(STYLES)
    out = Path(__file__).with_name("slots.json")
    out.write_text(json.dumps(slots, indent=1, ensure_ascii=False) + "\n")
    print(f"{len(slots)} slots -> {out}")


if __name__ == "__main__":
    main()
