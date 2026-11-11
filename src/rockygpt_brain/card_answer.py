"""What the bot says from one answer card, written by code.

An answer card is one task a student asks about, joined from every page that lists it
(retrieval/card_join.py), read in full by retrieval/answer_cards.view. Its answer is the task's
name; who it is for and its term, as the winning page gives them; that page's steps in the page
read's own words and order, each citing the page, with a link the page gives beside a step that
names none; the term or date that has passed, when the card is for one; and the deadlines its
left-out pages name that are over. Nothing else: no step of another page, no reworded sentence.
The engine adds the contacts of the offices that handle it, from the directory. No model call
writes any of it (Dan 11-03: boring is beneficial; 11-10: do the thinking at build time).

Code does not answer from every card (`refusal`): a card the rules flagged for a look, one whose
date passed after the cards were built, one with no step or no page address, and a list too long
to say are left to the path that reads the pages.
"""

import re
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from rockygpt_brain.plain_writer import literal

MAX_STEP_LINKS = 2  # At most this many of a step's page links are said beside it.
# A task with more steps than this is a table read as steps (a year's fees: 25 to 44 lines), not
# something to do: the path that reads the page says the part that was asked. Of 131 real tasks
# read on 11-11, 124 had 6 steps or fewer and the longest that was a procedure had 8.
MAX_STEPS = 12
_URL = re.compile(r"https?://[^\s()\[\]<>\"']+")


def refusal(card: Mapping[str, Any]) -> str | None:
    """Why code does not answer from a card, or None when it does: "needs_look" (the rules could
    not settle its owner or its date), "date_changed" (its term or date passed, or stopped being
    past, after the cards were built), "no_steps", "too_long", "no_page"."""
    if card.get("status") not in ("ready", "date_passed"):
        return "needs_look"
    if (card.get("over_now") or None) != (card.get("label") or None):
        return "date_changed"
    steps = [step for step in (card.get("task") or {}).get("steps") or []
             if str(step.get("text") or "").strip()]
    if not steps:
        return "no_steps"
    if len(steps) > MAX_STEPS:
        return "too_long"
    if not (card.get("page") or {}).get("url"):
        return "no_page"
    return None


def _plain(text: Any) -> str:
    """Page words as words: nothing in them reads as formatting, and a web address stays as the
    page wrote it (an escaped address is not the address the facts hold)."""
    value = " ".join(str(text or "").split())
    kept, at = [], 0
    for found in _URL.finditer(value):
        kept += [literal(value[at:found.start()]), found.group(0)]
        at = found.end()
    return "".join([*kept, literal(value[at:])])


def _sentence(text: str) -> str:
    return text if text[-1:] in ".!?:" else f"{text}."


def _trimmed(url: str) -> str:
    return url.rstrip(".,;:").rstrip("/")


def _step_links(step: Mapping[str, Any], said: Sequence[str]) -> list[str]:
    """The web addresses a step's page words give that no step and no source says: "Register
    using the registration link" is no help without the link the page put there."""
    given = {_trimmed(url) for text in said for url in _URL.findall(text)}
    links: list[str] = []
    for url in _URL.findall(str(step.get("quote") or "")):
        url = url.rstrip(".,;:")
        if _trimmed(url) not in given:
            given.add(_trimmed(url))
            links.append(url)
    return links[:MAX_STEP_LINKS]


def _day(iso: Any) -> str | None:
    try:
        found = date.fromisoformat(str(iso))
    except ValueError:
        return None
    return f"{found:%B} {found.day}, {found.year}"


def say(card: Mapping[str, Any], numbered_from: int = 0) -> tuple[str, list[dict[str, Any]]]:
    """A card's answer and the sources its marks stand for, numbered after `numbered_from`: the
    winning page first, then the page of each passed deadline it names. Call only for a card
    `refusal` gives no reason against."""
    task, page = card["task"], card["page"]
    n = numbered_from + 1
    sources = [{"n": n, "url": page["url"], "title": str(task["task"])}]
    blocks = [f"**{_plain(task['task'])}**"]
    label = card.get("label")
    if label:
        blocks.append(f"This is for {_plain(label)}, which has passed. The page said:")
    elif task.get("term"):
        blocks.append(f"Term: {_plain(task['term']).rstrip('.')}.")
    if task.get("applies_to"):
        blocks.append(f"Who it is for: {_plain(task['applies_to']).rstrip('.')}.")
    steps = [step for step in task["steps"] if str(step.get("text") or "").strip()]
    said = [str(step["text"]) for step in steps] + [str(page["url"])]
    lines = []
    for at, step in enumerate(steps, start=1):
        line = _sentence(_plain(step["text"]))
        if links := _step_links(step, said):
            line += " Link: " + " ".join(links)
            said += links
        lines.append(f"{at}. {line} [{n}]" if len(steps) > 1 else f"{line} [{n}]")
    blocks.append("\n".join(lines))
    if "old_page" in (card.get("notes") or []) and (changed := _day(page.get("updated"))):
        blocks.append(f"This page last changed on {changed}.")
    for item in card.get("passed") or []:
        if not item.get("url"):
            continue
        sources.append({"n": numbered_from + len(sources) + 1, "url": item["url"],
                        "title": str(item["task"])})
        blocks.append(f"Already passed: {_plain(item['task']).rstrip('.')} "
                      f"({_plain(item['label'])}). [{sources[-1]['n']}]")
    return "\n\n".join(blocks), sources
