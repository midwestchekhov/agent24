"""Shared text heuristics.

Regexes and predicates that decide whether a span carries a checkable
assertion. They are pure functions on parsed text so every stage can reuse the
same definition of "claim-worthy" without importing another stage.
"""

from __future__ import annotations

import re

from ..state import PaperState, Span


NOVELTY_MARKERS = ("first", "novel", "state-of-the-art", "unprecedented", "최초")
NUM_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*(%|AUC|HR|OR|ms|GB|x)?")

ASSERTION_RE = re.compile(
    r"\b(?:we\s+(?:find|show|observe|demonstrate|discover|report|establish)|"
    r"results?\s+(?:show|indicate|suggest)|"
    r"identif\w*|reveal\w*|indicat\w*|"
    r"outperform\w*|improv\w*|worsen\w*|reduc\w*|increas\w*|decreas\w*|"
    r"affect\w*|influenc\w*|correlat\w*|associat\w*|predict\w*|"
    r"calibrat\w*|miscalibrat\w*|overconfident|effective(?:ness)?|"
    r"arise\w*|originate\w*|caus\w*|driv\w*|depend\w*|enable\w*)\b",
    re.I,
)
FINITE_ASSERTION_RE = re.compile(
    r"\b(?:is|are|was|were|has|have|can|may|tends?\s+to)\b", re.I
)
DEFINITION_RE = re.compile(
    r"\b(?:is|are)\s+(?:defined|measured|interpreted)\s+as\b|"
    r"\b(?:we\s+define|denotes?|means?)\b",
    re.I,
)


def _looks_like_numeric_dump(text: str) -> bool:
    """Detect flattened table/chart text that PyMuPDF labels as a paragraph."""
    numbers = NUM_RE.findall(text)
    words = re.findall(r"[A-Za-z][A-Za-z-]+", text)
    return len(numbers) >= 8 and len(numbers) >= max(8, len(words) // 2)


def _looks_like_metadata(span: Span) -> bool:
    text = " ".join(span.text.split())
    lowered = text.lower()
    excluded = (
        "graduate school", "department of", "these authors", "author contributions",
        "correspondence", "copyright", "all rights reserved", "received:",
        "accepted:", "published online", "https://doi.org/", "reviewer information",
        "nature |", "proceedings of the", "equal contribution",
    )
    if any(marker in lowered for marker in excluded):
        return True
    # Author blocks in two-column PDFs are often just names followed by
    # superscript affiliations, e.g. ``Chuan Guo * 1 Geoff Pleiss * 1``.
    affiliation_marks = re.findall(r"(?:\*\s*)?\d+(?:,\s*\d+)*", text)
    name_words = re.findall(r"\b[A-Z][a-z]+\b", text)
    if span.page == 1 and len(text) < 140 and len(affiliation_marks) >= 3 \
            and len(name_words) >= 5 and not re.search(r"[.!?]", text):
        return True
    return False


def _claimworthy_span(span: Span) -> bool:
    """Whether a span itself makes a checkable assertion.

    Captions, equations and table dumps may support an assertion, but cannot
    be the sole prose that turns a table description into a claim.
    """
    if span.origin != "paper" or span.kind != "paragraph":
        return False
    text = " ".join(span.text.split())
    lowered = text.lower()
    if len(text) < 55 or _looks_like_metadata(span) or _looks_like_numeric_dump(text):
        return False
    if re.match(r"^(?:\d+\.\s+){2,}|^\d+\.\s+[A-Z].*\b(?:train|validation|test)\b", text):
        return False
    if re.match(r"^(?:table|figure)\s+\d+\b.*\b(?:shows?|displays?|contains?)\b", lowered):
        return False
    if DEFINITION_RE.search(text) and not ASSERTION_RE.search(text):
        return False
    if ASSERTION_RE.search(text):
        return True
    # Keep ordinary scientific assertions outside the ML vocabulary, while
    # still rejecting noun-phrase titles such as "Calibration Error Values …".
    return len(text.split()) >= 12 and bool(FINITE_ASSERTION_RE.search(text))


def _falsifiable_claim(text: str, spans: list[Span]) -> bool:
    normalized = " ".join(text.split())
    lowered = normalized.lower()
    if len(normalized) < 18 or not any(_claimworthy_span(span) for span in spans):
        return False
    if _looks_like_numeric_dump(normalized):
        return False
    if re.match(
        r"^(?:calibration error values|dataset(?:s)?\b|model(?:s)?\b|"
        r"comparison of|results? of applying|table\s+\d+|figure\s+\d+)",
        lowered,
    ) and not ASSERTION_RE.search(normalized):
        return False
    if DEFINITION_RE.search(normalized) and not ASSERTION_RE.search(normalized):
        return False
    return bool(ASSERTION_RE.search(normalized) or FINITE_ASSERTION_RE.search(normalized))


def _claim_sections(state: PaperState) -> set[str]:
    for item in state.doc.sections:
        if item.get("claim_sections_reliable") is False:
            return {"abstract"}
    return {"abstract", "intro", "results", "discussion"}
