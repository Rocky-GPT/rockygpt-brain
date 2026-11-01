"""The catalog pick (catalog.py): the model's pick kept to names the catalog has, and the lookups
code answers across the whole catalog."""

from rockygpt_brain.catalog import (
    courses_listed,
    listing_facts,
    order_facts,
    pick_messages,
    read_pick,
)


def test_a_pick_keeps_only_names_the_catalog_has() -> None:
    programs = {"Communication Arts BA": {"name": "Communication Arts BA"},
                "Biology BS": {"name": "Biology BS"}}
    courses = {"CHEM 211": {"name": "CHEM 211"}, "MATH 122": {"name": "MATH 122"}}
    pick = read_pick({"programs": ["communication arts ba", "Business Administration BS"],
                      "courses": ["CHEM 211 ORGANIC CHEMISTRY I LECTURE", "math122", "XYZ 999"],
                      "find": ["programs_listing_course", "something_else"],
                      "level": "major", "track": "  pre-med "}, programs, courses)
    assert pick == {"programs": [programs["Communication Arts BA"]],
                    "courses": [courses["CHEM 211"], courses["MATH 122"]],
                    "find": ["programs_listing_course"], "level": "major", "track": "pre-med"}
    assert read_pick(None, programs, courses) is None
    (system, user) = pick_messages("orgo 1 prereqs?", ["Biology BS"], [("CHEM 211", "ORGO")])
    assert "Biology BS" in user["content"] and "CHEM 211 ORGO" in user["content"]


def test_code_answers_which_programs_list_a_course_and_which_comes_first() -> None:
    assert courses_listed("Required\n  - ACCT 221 Financial Accounting\n  - ECON 101") == {
        "ACCT 221", "ECON 101"}
    facts = [f["fact"] for f in listing_facts(
        ["ACCT 221", "ZZZ 100"], {"ACCT 221": ["Finance BS", "Accounting BS"]}, 3)]
    assert facts == ["The catalog lists ACCT 221 in the requirements of 2 programs: "
                     "Accounting BS; Finance BS.",
                     "No program's requirements in the catalog list ZZZ 100."]
    courses = [{"code": "CMPS 231", "n": 1}, {"code": "CMPS 361", "n": 2}]
    (fact,) = order_facts(courses, {"CMPS 361": {"CMPS 231"}, "CMPS 231": {"CMPS 148"}})
    assert fact["fact"].startswith("CMPS 361's published prerequisites name CMPS 231")
    assert fact["n"] == 2
