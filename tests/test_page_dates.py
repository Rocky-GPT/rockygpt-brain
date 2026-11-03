"""A newer page wins: each office page says when it was last changed (page_modified_at), and an
old page's sections rank below a newer page's and say how old they are."""

from datetime import UTC, date, datetime

NOW = datetime(2026, 11, 3, 12, tzinfo=UTC)


def _page(key: str, changed: str | None, text: str) -> dict[str, object]:
    return {"id": key, "source_key": "office-pages", "source_record_key": f"www.ramapo.edu/{key}/",
            "url": f"https://www.ramapo.edu/{key}/", "site_folder": "healthservices",
            "collected_at": NOW, "freshness_sla_hours": 168, "content_hash": key,
            **({"page_modified_at": changed} if changed else {}),
            "sections": [{"heading": "Appointments", "text": text}]}


def test_a_page_is_old_when_unchanged_for_three_years() -> None:
    from rockygpt_brain.retrieval.entity_facts import old_page, page_updated
    today = date(2026, 11, 3)
    assert page_updated({"page_modified_at": "2021-03-04T10:00:00+00:00"}) == date(2021, 3, 4)
    assert page_updated({"page_modified_at": None}) is None
    assert page_updated({}) is None
    assert old_page(date(2023, 11, 3), today)
    assert not old_page(date(2023, 11, 4), today)
    assert not old_page(None, today)  # A page that does not say is not old.
    assert old_page(date(2020, 2, 28), date(2024, 2, 29))


def test_the_newer_page_comes_first_and_the_old_one_says_its_date() -> None:
    from rockygpt_brain.retrieval.entity_facts import procedures_property
    old = _page("healthservices/faq-2021", "2021-03-04T10:00:00+00:00",
                "Book an appointment by calling the front desk to make an appointment.")
    new = _page("healthservices/appointments", "2026-06-17T18:35:32+00:00",
                "Book an appointment online in the patient portal to make an appointment.")
    # The old page comes first in the rows and matches as well: the newer still ranks first.
    prop, _ = procedures_property([old, new], NOW, "make an appointment")
    first, second = (v["value"] for v in prop["values"])
    assert first["page"].endswith("/appointments/") and first["page_updated"] == "2026-06-17"
    assert "old_page" not in first
    assert second["page_updated"] == "2021-03-04" and second["old_page"] is True
    # Pages that do not say when they changed rank as before, in their own order.
    undated, _ = procedures_property([_page("a", None, old["sections"][0]["text"]),  # type: ignore[index]
                                      _page("b", None, new["sections"][0]["text"])],  # type: ignore[index]
                                     NOW, "make an appointment")
    assert [v["value"]["page"] for v in undated["values"]][0].endswith("/a/")
    assert all("page_updated" not in v["value"] for v in undated["values"])


def test_code_says_when_an_old_page_was_last_updated() -> None:
    from rockygpt_brain.plain_writer import plain_text
    def view(value: dict[str, object]) -> dict[str, object]:
        return {"facts": [{"subject": "Health Services", "predicate": "procedures",
                           "value": value, "sources": [1]}]}
    section = {"heading": "Appointments", "page": "https://x.edu/a/", "text": "Call the desk."}
    assert plain_text(view({**section, "page_updated": "2021-03-04", "old_page": True})) == (
        'Health Services\'s page "Appointments" says: "Call the desk." '
        "(its page was last updated March 2021) [1]")
    assert plain_text(view({**section, "page_updated": "2026-06-17"})) == (
        'Health Services\'s page "Appointments" says: "Call the desk." [1]')


def test_the_office_pages_read_carries_the_page_date_even_before_the_column_exists() -> None:
    from rockygpt_brain.retrieval import postgres
    # Read through to_jsonb, so a database without the column yet gives null, not an error.
    assert "to_jsonb(o) ->> 'page_modified_at' AS page_modified_at" in postgres._OFFICE_SECTIONS


def test_the_answer_model_is_told_the_newer_page_wins() -> None:
    from rockygpt_brain import one_call
    assert "the newer page wins" in one_call.ONE_CALL_PROMPT
