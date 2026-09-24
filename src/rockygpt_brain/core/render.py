"""Answer rendering and validation. References resolve to retrieved sources."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import unquote, urlsplit

from rockygpt_brain.contracts import Answer


class InvalidAnswer(Exception):
    """The provider did not produce a safe, complete output contract."""

    def __init__(self, message: str, code: str = "invalid_answer") -> None:
        super().__init__(message)
        self.code = code


def page_name(url: str) -> str:
    """'…/locations/birch-tree-inn' → 'Birch Tree Inn'; a site root is its home page."""
    segment = urlsplit(url).path.strip("/").rsplit("/", 1)[-1]
    words = re.split(r"[-_\s]+", unquote(segment).rsplit(".", 1)[0]) if segment else []
    return " ".join(word.capitalize() for word in words if word) or "Home page"


CITATION_FIELDS = (
    "id", "title", "url", "collection", "collected_at", "freshness", "valid_from",
    "valid_until", "trust_tier", "limitations",
)


def consulted_sources(
    evidence: dict[str, dict[str, Any]], limit: int = 3
) -> list[dict[str, Any]]:
    """Where a turn looked, never what it concluded.

    A rejected draft's claims are not shown, but the published pages its
    evidence came from still help: a student who hits "couldn't verify" gets
    the shuttle page instead of a dead end.
    """
    sources: dict[str, dict[str, Any]] = {}
    for record in evidence.values():
        url = record.get("url")
        if not isinstance(url, str) or not url.startswith("https://") or url in sources:
            continue
        citation = {key: record.get(key) for key in CITATION_FIELDS}
        citation.update(
            title=str(record.get("source_title") or record.get("title") or "Source"),
            record_title=record.get("title"),
        )
        sources[url] = citation
        if len(sources) >= limit:
            break
    return list(sources.values())


def render_answer(answer: Answer, evidence: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """References resolve to retrieved sources; links never come from model text."""
    paragraphs: list[str] = []
    citations: dict[str, dict[str, Any]] = {}
    for part in answer.parts:
        if not part.text.strip() or re.search(
            r"https?://|www\.|\]\(|\]\s*\[|\]:\s*\S|<(?:[a-z][a-z0-9+.-]*:|//)",
            part.text,
            re.IGNORECASE,
        ):
            raise InvalidAnswer(
                "Model text contains an unvalidated link or is blank", "answer_text"
            )
        if part.kind == "campus_fact" and not part.evidence_ids:
            raise InvalidAnswer("Campus assertion without evidence", "missing_citation")
        links: dict[str, str] = {}
        for evidence_id in dict.fromkeys(part.evidence_ids):
            record = evidence.get(evidence_id)
            if record is None:
                raise InvalidAnswer("Unknown citation", "unknown_citation")
            if part.kind == "campus_fact" and record["freshness"] not in {"fresh", "static"}:
                raise InvalidAnswer("Campus assertion uses stale or unknown evidence", "stale_fact")
            url = record["url"]
            if not isinstance(url, str) or not url.startswith("https://"):
                raise InvalidAnswer("Unsafe source URL", "source_url")
            source_title = str(record.get("source_title") or record["title"])
            title = source_title.replace("[", "").replace("]", "")
            links[url] = title
            citations[evidence_id] = {key: record.get(key) for key in CITATION_FIELDS}
            citations[evidence_id].update(title=source_title, record_title=record["title"])
        text = part.text.strip()
        if links:
            # Two pages published under one title read as the same link twice
            # ("Ramapo Dining Ramapo Dining"); each is labelled with its page.
            counts: dict[str, int] = {}
            for title in links.values():
                counts[title] = counts.get(title, 0) + 1
            rendered_links = []
            for url, title in links.items():
                label = f"{title} · {page_name(url)}" if counts[title] > 1 else title
                safe_url = url.replace("(", "%28").replace(")", "%29").replace(" ", "%20")
                rendered_links.append(f"[{label}]({safe_url})")
            text += " " + " ".join(rendered_links)
        paragraphs.append(text)
    rendered = "\n\n".join(paragraphs)
    if len(rendered) > 12000:
        raise InvalidAnswer(
            "Answer is too long for conversation history; shorten it", "answer_length"
        )
    return {
        "answer": rendered,
        "status": answer.status,
        "citations": list(citations.values()),
    }
