from __future__ import annotations

import re
from dataclasses import dataclass

GENERIC_QUERY_WORDS = {
    # asking
    "what",
    "whats",
    "when",
    "where",
    "who",
    "whos",
    "which",
    "how",
    "why",
    "is",
    "are",
    "was",
    "do",
    "does",
    "did",
    "can",
    "could",
    "would",
    "should",
    "will",
    "am",
    # filler
    "the",
    "a",
    "an",
    "of",
    "for",
    "to",
    "about",
    "at",
    "in",
    "on",
    "and",
    "or",
    "my",
    "me",
    "you",
    "your",
    "their",
    "there",
    "this",
    "that",
    "it",
    "be",
    "get",
    "got",
    "please",
    "any",
    "some",
    "with",
    "from",
    "still",
    "also",
    "just",
    "need",
    "want",
    "tell",
    "know",
    "find",
    "looking",
    "look",
    "help",
    "give",
    "list",
    "all",
    "info",
    "information",
    # time framing
    "hour",
    "hours",
    "open",
    "opens",
    "opening",
    "close",
    "closes",
    "closed",
    "closing",
    "today",
    "tonight",
    "tomorrow",
    "yesterday",
    "now",
    "currently",
    "time",
    "times",
    "morning",
    "afternoon",
    "evening",
    "night",
    "week",
    "weekend",
    "weekday",
    "schedule",
    "date",
    "dates",
    "day",
    "days",
    "deadline",
    # generic attributes
    "office",
    "contact",
    "phone",
    "number",
    "email",
    "department",
    "dept",
    "location",
    "degree",
    "program",
    "programs",
    "requirement",
    "requirements",
    "major",
    "majors",
}

COMMON_SHARE = 0.1
COMMON_MINIMUM_ROWS = 4


@dataclass
class TermFrequencies:
    row_count: int
    document_frequency: dict[str, int]


@dataclass
class SearchTerms:
    primary: str
    fallback: str | None


def split_query_words(query: str, domain_words: set[str] | None = None) -> list[str]:
    words = re.split(r"[^\w]+", query.lower(), flags=re.UNICODE)
    return [
        w
        for w in words
        if len(w) > 1
        and w not in GENERIC_QUERY_WORDS
        and (not domain_words or w not in domain_words)
    ]


def build_term_frequencies(texts: list[str]) -> TermFrequencies:
    doc_freq: dict[str, int] = {}
    for text in texts:
        seen = {w for w in re.split(r"[^\w]+", text.lower(), flags=re.UNICODE) if len(w) > 1}
        for word in seen:
            doc_freq[word] = doc_freq.get(word, 0) + 1
    return TermFrequencies(row_count=len(texts), document_frequency=doc_freq)


def is_too_common(word: str, frequencies: TermFrequencies) -> bool:
    count = frequencies.document_frequency.get(word, 0)
    return count >= COMMON_MINIMUM_ROWS and count / max(frequencies.row_count, 1) > COMMON_SHARE


def search_terms_for(
    query: str,
    frequencies: TermFrequencies,
    domain_words: set[str] | None = None,
) -> SearchTerms:
    words = split_query_words(query, domain_words)
    if not words:
        return SearchTerms(primary="", fallback=None)

    distinctive = [w for w in words if not is_too_common(w, frequencies)]
    primary = " ".join(distinctive if distinctive else words)
    fallback = " ".join(words) if (distinctive and len(distinctive) < len(words)) else None
    return SearchTerms(primary=primary, fallback=fallback)
