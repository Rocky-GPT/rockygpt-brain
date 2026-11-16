"""Each office page's marks (plan "one simple page writer", step 1): who owns it, when it last
changed, and whether it is current, all set by code from what the release already holds."""

from datetime import date

from rockygpt_brain.retrieval.meaning import MeaningIndex
from rockygpt_brain.retrieval.page_marks import PageMark, address_until, page_marks, page_owners

TODAY = date(2026, 11, 16)


def test_an_address_naming_only_past_terms_or_years_is_not_current() -> None:
    # The pages that ranked above today's on 11-16: old cost and deadline pages, old newsletters.
    for url in ("https://www.ramapo.edu/student-accounts/costinfo/2024-2025-cost-information/",
                "https://www.ramapo.edu/student-accounts/costinfo/2017-2018-past-cost-information/",
                "https://www.ramapo.edu/student-accounts/previous-deadlines/previous-deadlines-2021/",
                "https://www.ramapo.edu/student-affairs/ramapo-college-wellness-services-fall-2022/",
                "https://www.ramapo.edu/summer/summer-sessions-2015/"):
        mark = page_marks({"url": url}, None)
        assert not mark.current(TODAY), url
    # A year still running, or one to come, is current: 2026 runs to the end of August 2027.
    for url in ("https://www.ramapo.edu/student-affairs/rcnj-student-handbook-2025-2026/",
                "https://www.ramapo.edu/president/strategic-plan-2023-2028/",
                "https://www.ramapo.edu/reslife/fall-2026-check-in/",
                "https://www.ramapo.edu/student-accounts/costinfo/",
                "https://www.ramapo.edu/library/early-newspaper-archives/"):
        assert page_marks({"url": url}, None).current(TODAY), url
    assert address_until("https://www.ramapo.edu/x/fall-2026-check-in/") == date(2026, 12, 31)
    assert address_until("https://www.ramapo.edu/x/handbook-2025-2026/") == date(2027, 8, 31)
    assert address_until("https://www.ramapo.edu/student-accounts/costinfo/") is None
    # The same page is current until its last named day and not after.
    fall = page_marks({"url": "https://www.ramapo.edu/x/fall-2026-check-in/"}, None)
    assert fall.current(date(2026, 12, 31)) and not fall.current(date(2027, 1, 1))


def test_a_page_of_previous_or_past_items_is_not_current_whatever_its_year() -> None:
    # The list of old deadline pages names no year of its own; "pervious" is the site's own typo.
    for url in ("https://www.ramapo.edu/student-accounts/previous-deadlines/",
                "https://www.ramapo.edu/student-accounts/previous-deadlines/pervious-deadlines-2025/",
                "https://www.ramapo.edu/x/past-cost-information/"):
        assert not page_marks({"url": url}, None).current(TODAY), url
    # A word that only contains them is not one ("pastoral", "previously-asked").
    for url in ("https://www.ramapo.edu/x/pastoral-care/", "https://www.ramapo.edu/x/previously/"):
        assert page_marks({"url": url}, None).current(TODAY), url


def test_a_page_says_when_it_last_changed_and_who_owns_it() -> None:
    row = {"url": "https://www.ramapo.edu/counseling/", "site_folder": "counseling",
           "page_modified_at": "2026-06-17T18:35:32+00:00"}
    assert page_marks(row, "office-counseling") == PageMark(
        owner="office-counseling", updated=date(2026, 6, 17), until=None, previous=False)
    assert page_marks({"url": "https://www.ramapo.edu/x/"}, None).updated is None


def test_the_owner_is_the_linking_node_that_links_most_of_the_pages_folder() -> None:
    # Housing links all of reslife's pages; a hall desk links two of them. A club page under
    # /clubs/ is the Center for Student Involvement's site, not the club's alone.
    links = {"r1": ["housing"], "r2": ["housing", "village"], "r3": ["village", "housing"],
             "c1": ["math-club", "csi"], "c2": ["csi"], "c3": ["chess-club", "csi"],
             "p1": ["emergency", "non-emergency"], "p2": ["non-emergency", "emergency"]}
    folders = {"r1": "reslife", "r2": "reslife", "r3": "reslife",
               "c1": "clubs", "c2": "clubs", "c3": "clubs",
               "p1": "publicsafety", "p2": "publicsafety"}
    owners = page_owners(links, folders)
    assert [owners[r] for r in ("r1", "r2", "r3")] == ["housing"] * 3
    assert [owners[c] for c in ("c1", "c2", "c3")] == ["csi"] * 3
    # A tie goes the same way on every page and every build: the lower id.
    assert owners["p1"] == owners["p2"] == "emergency"


def test_the_meaning_index_keeps_each_pages_marks_and_an_older_index_has_none() -> None:
    meta = {"dataset_version": "v", "identity_hash": "h", "model": "m", "dimensions": 2,
            "units": [["r1", "0", ["housing"]]], "owner": [0], "labels": [["reslife", "Rooms"]],
            "pages": {"r1": ["housing", "2026-06-17", "2025-05-31", False]}}
    import array
    index = MeaningIndex(meta, array.array("f", [1.0, 0.0]))
    assert index.pages["r1"] == PageMark("housing", date(2026, 6, 17), date(2025, 5, 31), False)
    assert not index.pages["r1"].current(TODAY)
    older = MeaningIndex({k: v for k, v in meta.items() if k != "pages"},
                         array.array("f", [1.0, 0.0]))
    assert older.pages == {}


def test_the_index_build_marks_each_page_once_from_its_units() -> None:
    import build_meaning_index as build

    def unit(rid: str, index: str, entities: list[str], url: str, folder: str,
             modified: str | None) -> dict[str, object]:
        return {"record_id": rid, "index": index, "entities": entities, "page": url,
                "heading": "H", "text": "T", "entry": False,
                "row": {"url": url, "site_folder": folder, "page_modified_at": modified}}

    units = [unit("a", "0", ["housing"], "https://www.ramapo.edu/reslife/rooms/", "reslife",
                  "2026-06-17T18:35:32+00:00"),
             unit("a", "1", ["housing"], "https://www.ramapo.edu/reslife/rooms/", "reslife",
                  "2026-06-17T18:35:32+00:00"),
             unit("b", "0", ["village", "housing"],
                  "https://www.ramapo.edu/reslife/fall-2024-move-in/", "reslife", None)]
    assert build.marks_of(units) == {"a": ["housing", "2026-06-17", None, False],
                                     "b": ["housing", None, "2024-12-31", False]}
