"""Server rendering of exact supported values and their evidence boundaries."""

from datetime import UTC, datetime
from typing import Any

import pytest

from rockygpt_brain.answers import citation_url, literal, render_facts
from rockygpt_brain.retrieval import EvidenceUnavailable, MemoryEntityFacts

NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)


def facts(
    *observations: dict[str, Any],
    fields: list[str] | None = None,
) -> dict[str, Any]:
    records = [
        {
            "id": f"record-{index}",
            "source_key": "campus-directory",
            "source_record_key": "office:registrar",
            "name": "Registrar",
            "email": "registrar@example.edu",
            "office": "D224",
            "phones": [{"number": "+12015550100", "extension": "12", "type": "fax"}],
            "canonical_url": f"https://example.edu/contact/{index}",
            "collected_at": NOW,
            "freshness_sla_hours": 24,
            **observation,
        }
        for index, observation in enumerate(observations or ({},))
    ]
    service = MemoryEntityFacts(
        dataset_version="test",
        identity_hash="identities",
        contacts=records,
        now=lambda: NOW,
        entities=[
            {
                "id": "registrar",
                "kind": "office",
                "name": "Registrar",
                "aliases": [],
                "links": [
                    {
                        "collection": "contacts",
                        "source_key": "campus-directory",
                        "source_record_keys": ["office:registrar"],
                    }
                ],
            }
        ],
    )
    return service.get_office_facts("registrar", fields or ["email"], "test")


def test_structured_phones_and_rooms_are_readable_and_cited() -> None:
    rendered = render_facts(facts(fields=["phones", "offices"]))
    assert "+12015550100; extension 12 (fax)" in rendered.text
    assert "Offices: D-224" in rendered.text
    assert '{"number"' not in rendered.text
    assert '"D-224"' not in rendered.text
    assert rendered.supported is True
    assert rendered.complete is True
    assert [citation["id"] for citation in rendered.citations] == ["record-0"]


def test_unknown_field_preserves_useful_supported_part() -> None:
    rendered = render_facts(facts({"email": None}, fields=["email", "offices"]))
    assert "Email: not published" in rendered.text
    assert "D-224" in rendered.text
    assert rendered.supported is True
    assert rendered.complete is False


def test_incomplete_identity_evidence_never_looks_complete() -> None:
    result = facts()
    result["complete"] = False
    result["caveats"] = ["One linked source is missing."]
    rendered = render_facts(result)
    assert "linked evidence is unavailable" in rendered.text
    assert "registrar@example.edu" in rendered.text
    assert rendered.complete is False


def test_conflicts_show_both_values_even_when_one_observation_is_stale() -> None:
    rendered = render_facts(
        facts(
            {"email": "old@example.edu", "collected_at": "2026-08-01T12:00:00Z"},
            {"email": "new@example.edu"},
        )
    )
    assert "conflicting" in rendered.text
    assert "can't choose a current value" in rendered.text
    assert "old@example.edu" in rendered.text and "new@example.edu" in rendered.text
    assert "stale capture" in rendered.text
    assert "current value unverified" in rendered.text
    assert {citation["id"] for citation in rendered.citations} == {"record-0", "record-1"}
    assert rendered.supported is True
    assert rendered.complete is False


def test_multiple_values_retain_explicit_validity_ranges() -> None:
    rendered = render_facts(
        facts(
            {"email": "old@example.edu", "valid_from": "2026-01-01", "valid_until": "2026-08-31"},
            {"email": "new@example.edu", "valid_from": "2026-09-01", "valid_until": "2026-12-31"},
        )
    )
    assert "different published date ranges" in rendered.text
    assert "2026-01-01 through 2026-08-31" in rendered.text
    assert "2026-09-01 through 2026-12-31" in rendered.text
    assert "expired record" in rendered.text
    assert "old@example.edu" in rendered.text and "new@example.edu" in rendered.text
    assert rendered.supported is True


@pytest.mark.parametrize(
    "observation,phrase",
    [
        ({"collected_at": "2026-08-01T12:00:00Z"}, "stale capture"),
        ({"collected_at": None}, "freshness unknown"),
        ({"valid_until": "2026-09-30"}, "expired record"),
        ({"valid_from": "2026-10-02"}, "future record"),
    ],
)
def test_old_or_unverified_observations_are_never_presented_as_current(
    observation: dict[str, Any],
    phrase: str,
) -> None:
    rendered = render_facts(facts(observation))
    assert phrase in rendered.text
    assert "current value unverified" in rendered.text
    assert "can't verify a current value" in rendered.text
    assert rendered.supported is False
    assert rendered.complete is False


def test_missing_secure_citation_withholds_value() -> None:
    rendered = render_facts(facts({"canonical_url": "http://example.edu/contact"}))
    assert "registrar@example.edu" not in rendered.text
    assert "lack a usable secure citation" in rendered.text
    assert rendered.supported is False
    assert rendered.citations == []


@pytest.mark.parametrize(
    "url",
    [
        "http://example.edu/contact",
        "javascript:alert(1)",
        "//example.edu/contact",
        "https://private:password@example.edu/contact",
        "https://example.edu:99999/contact",
        "https://example.edu/hello world",
        "https://example.edu/\nhello",
        "https://example.edu/%0ahello",
        "https://example.edu\\@attacker.invalid/",
    ],
)
def test_unsafe_citation_urls_are_rejected(url: str) -> None:
    assert citation_url(url) is None


def test_secure_url_markdown_delimiters_are_encoded() -> None:
    assert citation_url('https://example.edu/a(b)<c>"') == (
        "https://example.edu/a%28b%29%3Cc%3E%22"
    )


def test_untrusted_record_text_does_not_become_html_or_markdown_links() -> None:
    text = "<script>x</script> [official](https://attacker.invalid)"
    rendered = render_facts(facts({"email": text}))
    assert "<script>" not in rendered.text
    assert "&lt;script&gt;" in rendered.text
    assert "\\[official\\]\\(https://attacker.invalid\\)" in rendered.text
    assert len(rendered.citations) == 1
    assert rendered.citations[0]["url"] == "https://example.edu/contact/0"
    assert literal("\x00secret") == "secret"


@pytest.mark.parametrize("mutation", ["value", "source", "assertion"])
def test_values_without_exact_current_result_evidence_are_rejected(mutation: str) -> None:
    result = facts()
    value = result["properties"][0]["values"][0]
    if mutation == "value":
        value["value"] = "invented@example.edu"
    elif mutation == "source":
        value["source_ids"] = ["not-in-this-result"]
    else:
        value["assertion_ids"] = ["not-in-this-result"]
    with pytest.raises(EvidenceUnavailable):
        render_facts(result)
