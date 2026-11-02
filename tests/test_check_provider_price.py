import json
from datetime import UTC, datetime
from pathlib import Path

import check_provider_price as price

ROOT = Path(__file__).resolve().parents[1]
RELEASE = json.loads((ROOT / "src/rockygpt_brain/provider-release.json").read_text())

PAGE = """<html><head><script>var x = "Input $9.99";</script></head><body>
<nav><a href="/pricing">Pricing</a> <a>Models</a></nav>
<h1>GPT-6 Luna</h1><p>$0.1&bull;$0.5</p>
<h2>Modalities</h2><p>Input</p><p>Text, image</p><p>Output</p><p>Text</p>
<h2>Pricing</h2><p>Per 1M tokens</p>
<div><span>Input</span><span>$0.10</span></div>
<div><span>Cached input</span><span>$0.01</span></div>
<div><span>Cache writes</span><span>$0.125</span></div>
<div><span>Output</span><span>$0.50</span></div>
</body></html>"""

NOW = datetime(2026, 11, 25, 13, 0, tzinfo=UTC)


def test_reads_the_pricing_section_not_the_menu_or_scripts() -> None:
    assert price.published_prices(price.page_text(PAGE)) == {
        "input": 100, "cached_input": 10, "cache_write": 125, "output": 500}


def test_unchanged_price_renews_only_when_due() -> None:
    assert price.check(RELEASE | {"valid_until": "2026-12-01T00:00:00-05:00"},
                       PAGE, NOW, 10)[0] == "renew"
    assert price.check(RELEASE | {"valid_until": "2027-01-01T00:00:00-05:00"},
                       PAGE, NOW, 10)[0] == "ok"


def test_a_changed_price_is_never_renewed() -> None:
    page = PAGE.replace("$0.50", "$0.60")
    status, notes = price.check(RELEASE, page, NOW, 10)
    assert status == "changed"
    assert any("output: page 600" in note for note in notes)
    higher_input = PAGE.replace("$0.10", "$0.20")
    assert price.check(RELEASE, higher_input, NOW, 10)[0] == "changed"


def test_a_page_without_prices_is_unreadable() -> None:
    assert price.check(RELEASE, "<html>Pricing coming soon</html>", NOW, 10)[0] == "unreadable"
    assert price.check(RELEASE, PAGE.replace("Cache writes", "Writes"), NOW, 10)[0] == "unreadable"
    assert price.check(RELEASE, None, NOW, 10)[0] == "unreadable"


def test_renewal_moves_one_month_in_campus_time() -> None:
    new = price.renewed(RELEASE | {"valid_until": "2026-12-01T00:00:00-05:00"}, NOW)
    assert new["valid_until"] == "2027-01-01T00:00:00-05:00"
    assert new["verified_at"] == "2026-11-25T00:00:00-05:00"
    assert new["version"] == "2026-11-25-gpt-6-luna"
    assert {k: v for k, v in new.items() if k not in {"valid_until", "verified_at", "version"}} \
        == {k: v for k, v in RELEASE.items() if k not in {"valid_until", "verified_at", "version"}}


def test_renewal_after_a_missed_expiry_still_lands_weeks_out() -> None:
    late = datetime(2026, 12, 20, tzinfo=UTC)
    new = price.renewed(RELEASE | {"valid_until": "2026-12-01T00:00:00-05:00"}, late)
    assert new["valid_until"] == "2027-02-01T00:00:00-05:00"
    summer = price.renewed(RELEASE | {"valid_until": "2027-06-01T00:00:00-04:00"},
                           datetime(2027, 5, 25, tzinfo=UTC))
    assert summer["valid_until"] == "2027-07-01T00:00:00-04:00"


def test_write_renews_the_file(tmp_path: Path) -> None:
    release = tmp_path / "provider-release.json"
    soon = datetime.now(UTC).astimezone(price.CAMPUS).replace(microsecond=0)
    release.write_text(json.dumps(RELEASE | {"valid_until": soon.isoformat()}))
    page = tmp_path / "page.html"
    page.write_text(PAGE)
    assert price.main(["--release", str(release), "--page", str(page), "--write"]) == 0
    assert datetime.fromisoformat(json.loads(release.read_text())["valid_until"]) > soon
