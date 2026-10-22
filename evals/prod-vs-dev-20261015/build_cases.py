"""Draw the frozen comparison set from Ramapo's own FAQ headings (no AI-written questions).

    python3 -I evals/prod-vs-dev-20261015/build_cases.py <rockygpt-data/data/raw>

Every section heading on a captured office or academic page that ends in "?" is a candidate; the
section text under it is the official answer the graders compare against. Rules are mechanical:
4-25 words, an answer of at least 80 characters, one copy of each heading, at most 2 per site
(first path segment), then a fixed-seed sample of 120. A heading often only makes sense on its
page ("What is the application fee?"), so the message sent is the site's name from the page title,
a colon, then the heading with any leading "Q." removed.
"""

import json
import random
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

SEED, SIZE, PER_SITE = 20261015, 120, 2


def site_name(title: str) -> str:
    """The site part of a page title: "FAQs - Financial Aid || Ramapo ..." -> "Financial Aid"."""
    title = title.split(" || ")[0]
    return (title.split(" - ")[-1] if " - " in title else title.split(" | ")[0]).strip()


def candidates(raw: Path) -> list[dict]:
    seen, out = set(), []
    for name in ("office-pages.raw.json", "academic-sites.raw.json"):
        for page in json.loads((raw / name).read_text())["pages"]:
            for section in page.get("sections") or []:
                heading = " ".join((section.get("heading") or "").split())
                answer = " ".join((section.get("text") or "").split())
                key = heading.lower()
                if (heading.endswith("?") and 4 <= len(heading.split()) <= 25
                        and len(answer) >= 80 and key not in seen
                        and urlsplit(page["url"]).netloc.endswith("ramapo.edu")):
                    seen.add(key)
                    site = urlsplit(page["url"]).path.strip("/").split("/")[0] or "home"
                    asked = re.sub(r"^Q\.\s*", "", heading)
                    out.append({"message": f"{site_name(page.get('title') or '')}: {asked}",
                                "question": heading, "official_answer": answer[:2000],
                                "url": page["url"], "site": site})
    return out


def main() -> None:
    pool = candidates(Path(sys.argv[1]))
    rng = random.Random(SEED)
    rng.shuffle(pool)
    per_site: dict[str, int] = {}
    cases = []
    for case in pool:
        if per_site.get(case["site"], 0) < PER_SITE:
            per_site[case["site"]] = per_site.get(case["site"], 0) + 1
            cases.append({"id": f"q{len(cases) + 1:03d}", **case})
        if len(cases) == SIZE:
            break
    out = Path(__file__).with_name("cases.json")
    out.write_text(json.dumps(cases, indent=1, ensure_ascii=False) + "\n")
    print(f"{len(pool)} candidates, {len(per_site)} sites, {len(cases)} cases -> {out}")


if __name__ == "__main__":
    main()
