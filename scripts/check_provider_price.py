"""Check the bundled model price against OpenAI's model page, and renew it when nothing changed.

The Brain refuses to run once `provider-release.json` passes `valid_until`, so a forgotten
renewal takes production down (2026-11-01). The scheduled `price-renewal` workflow runs this
every day:

    python scripts/check_provider_price.py                        # report only
    python scripts/check_provider_price.py --write               # also renew the file if due

It prints one line per finding and, under GitHub Actions, writes `status`, `days_left` (on the
price as it was before any renewal) and `valid_until` (after it) to $GITHUB_OUTPUT. Status is
one of:

    ok          the published price matches and more than --renew-days remain
    renew       the published price matches and the release is due (rewritten with --write)
    changed     the published price differs from the bundled one: a person must review it
    unreadable  the page could not be fetched or its prices could not be found

Only an exact match renews. Anything the page does not clearly say is "unreadable", never a match.
"""

import argparse
import html
import json
import os
import re
import sys
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "src" / "rockygpt_brain" / "provider-release.json"
CAMPUS = ZoneInfo("America/New_York")
NUSD_PER_DOLLAR_PER_MILLION = 1000  # $1 per 1M tokens = 1,000 nanoUSD per token
# Labels on the model page, each followed by its Standard price per 1M tokens.
LABELS = {
    "input": r"(?<![A-Za-z])Input(?![A-Za-z])",
    "cached_input": r"Cached input",
    "cache_write": r"Cache writes?",
    "output": r"(?<![A-Za-z])Output(?![A-Za-z])",
}


# Between a label and its price: a few words, but no dollar amount and no other label, so a label
# with no price of its own (the Modalities list) never borrows the next one's.
GAP = r"(?:(?!Input|input|Output|Cache|Pricing)[^$]){0,40}?"


class Unreadable(Exception):
    pass


def page_text(raw: str) -> str:
    """The page's words: scripts, styles and tags removed, entities decoded, spaces collapsed."""
    raw = re.sub(r"(?is)<(script|style|noscript)\b.*?</\1>", " ", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", html.unescape(raw)).strip()


def published_prices(text: str) -> dict[str, int]:
    """nanoUSD per token for each label, read from the page's Pricing section.

    Each label must be followed, within a short gap (GAP), by its price.
    The word "Pricing" can also sit in the site's menu, so the first "Pricing" followed by all
    four prices counts. No such place is Unreadable."""
    starts = [match.start() for match in re.finditer("Pricing", text)]
    if not starts:
        raise Unreadable("no Pricing section")
    missing = ""
    for start in starts:
        section = text[start:start + 4000]
        found = {}
        for name, label in LABELS.items():
            match = re.search(label + GAP + r"\$\s?(\d+(?:\.\d+)?)", section)
            if not match:
                missing = name
                break
            found[name] = round(float(match[1]) * NUSD_PER_DOLLAR_PER_MILLION)
        else:
            return found
    raise Unreadable(f"no {missing} price in any Pricing section")


def expected_prices(release: dict[str, Any]) -> dict[str, int]:
    """What the page must say for the bundled release to still be right.

    The release charges uncached input at the cache-write rate (the conservative basis in its
    `cost_basis`), so the page's cache-write price must equal the bundled input rate, and the
    page's ordinary input price must not be higher than it."""
    return {
        "cache_write": release["input_nusd_per_token"],
        "cached_input": release["cached_input_nusd_per_token"],
        "output": release["output_nusd_per_token"],
    }


def differences(release: dict[str, Any], page: dict[str, int]) -> list[str]:
    wrong = [f"{name}: page {page[name]} nanoUSD/token, bundled {value}"
             for name, value in expected_prices(release).items() if page[name] != value]
    if page["input"] > release["input_nusd_per_token"]:
        wrong.append(f"input: page {page['input']} nanoUSD/token is above the bundled "
                     f"{release['input_nusd_per_token']}")
    return wrong


def next_month(moment: datetime) -> datetime:
    """Midnight campus time on the first day of the month after `moment`."""
    local = moment.astimezone(CAMPUS)
    year, month = (local.year + 1, 1) if local.month == 12 else (local.year, local.month + 1)
    return datetime(year, month, 1, tzinfo=CAMPUS)


def renewed(release: dict[str, Any], now: datetime) -> dict[str, Any]:
    """The same release, checked today and valid one month past its current expiry."""
    today = now.astimezone(CAMPUS)
    until = next_month(datetime.fromisoformat(release["valid_until"]))
    while until - now < timedelta(days=21):  # an old expiry still lands a few weeks out
        until = next_month(until)
    out = dict(release)
    out["version"] = f"{today:%Y-%m-%d}-{release['model']}"
    out["verified_at"] = datetime(today.year, today.month, today.day, tzinfo=CAMPUS).isoformat()
    out["valid_until"] = until.isoformat()
    return out


def fetch(url: str) -> str:
    if not url.startswith("https://developers.openai.com/"):
        raise OSError("the release's source is not an OpenAI docs page")
    request = urllib.request.Request(url, headers={  # noqa: S310 (https checked above)
        "User-Agent": "Mozilla/5.0 (RockyGPT price check; +https://rockygpt.danielrajakumar.com)",
        "Accept": "text/html"})
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        return str(response.read().decode("utf-8", errors="replace"))


def check(release: dict[str, Any], raw_page: str | None, now: datetime,
          renew_days: int) -> tuple[str, list[str]]:
    days_left = (datetime.fromisoformat(release["valid_until"]) - now).total_seconds() / 86400
    notes = [f"{release['model']} price valid until {release['valid_until']} "
             f"({days_left:.1f} days left)"]
    if raw_page is None:
        return "unreadable", [*notes, "could not fetch the model page"]
    try:
        page = published_prices(page_text(raw_page))
    except Unreadable as error:
        return "unreadable", [*notes, f"could not read the model page: {error}"]
    notes.append("page (nanoUSD/token): " + ", ".join(f"{k} {v}" for k, v in page.items()))
    wrong = differences(release, page)
    if wrong:
        return "changed", [*notes, *wrong]
    if days_left <= renew_days:
        return "renew", [*notes, "price unchanged; renewal due"]
    return "ok", [*notes, "price unchanged"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--release", type=Path, default=RELEASE)
    parser.add_argument("--page", type=Path, help="read this saved page instead of fetching")
    parser.add_argument("--renew-days", type=int, default=10)
    parser.add_argument("--write", action="store_true", help="rewrite the release when due")
    args = parser.parse_args(argv)

    release = json.loads(args.release.read_text())
    now = datetime.now(UTC)
    raw: str | None
    if args.page:
        raw = args.page.read_text()
    else:
        try:
            raw = fetch(release["source"])
        except OSError as error:
            print(f"fetch failed: {error}", file=sys.stderr)
            raw = None
    status, notes = check(release, raw, now, args.renew_days)
    # Days left on the price production runs now, even after a renewal written here.
    days_left = (datetime.fromisoformat(release["valid_until"]) - now).days
    if status == "renew" and args.write:
        new = renewed(release, now)
        args.release.write_text(json.dumps(new, indent=2) + "\n")
        notes.append(f"renewed: valid until {new['valid_until']}")
        release = new
    for note in notes:
        print(note)
    print(f"status: {status}")
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a") as handle:
            handle.write(f"status={status}\ndays_left={days_left}\n"
                         f"valid_until={release['valid_until']}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
