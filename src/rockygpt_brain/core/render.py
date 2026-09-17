"""Answer rendering and validation. References resolve to retrieved sources."""

from __future__ import annotations

import re
from typing import Any

from rockygpt_brain.contracts import Answer


class InvalidAnswer(Exception):
    """The provider did not produce a safe, complete output contract."""

    def __init__(self, message: str, code: str = "invalid_answer") -> None:
        super().__init__(message)
        self.code = code


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
            safe_url = url.replace("(", "%28").replace(")", "%29").replace(" ", "%20")
            links[url] = f"[{title}]({safe_url})"
            citations[evidence_id] = {
                key: record.get(key)
                for key in (
                    "id",
                    "title",
                    "url",
                    "collection",
                    "collected_at",
                    "freshness",
                    "valid_from",
                    "valid_until",
                    "trust_tier",
                    "limitations",
                )
            }
            citations[evidence_id].update(title=source_title, record_title=record["title"])
        text = part.text.strip()
        if links:
            text += " " + " ".join(links.values())
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
