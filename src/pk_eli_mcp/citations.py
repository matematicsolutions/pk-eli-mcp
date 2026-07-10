"""Citation helpers for Pakistani statutes and Supreme Court judgments.

Statutes arrive as ``{file_name, text}`` records whose PDF file names are opaque
hashes, so the title and enactment year are derived from the leading lines of the
document text. Judgments carry their appeal number inside a Python-repr dict
string (``"{'id': 'Crl.A.93_2013.pdf', 'url': ''}"``); the id is the citation.

Original implementation for this connector (the upstream Legal Data Hunter
harvester is AGPL-3.0, so no code is reused from it - only the two public
HuggingFace datasets are consumed, under their own licenses).
"""

from __future__ import annotations

import ast
import re

PAKISTANCODE_PDF_BASE = "https://pakistancode.gov.pk/pdffiles/"

_PAGE_LINE_RE = re.compile(r"(?i)^page\s+\d+\s+of\s+\d+\s*$")
_TITLE_KEYWORD_RE = re.compile(
    r"\b(ACT|ORDINANCE|CODE|CONSTITUTION|REGULATIONS?|RULES|ORDER)\b"
)
_STOP_LINE_RE = re.compile(
    r"(?i)^(contents|sections?\s*:?|preamble\.?|arrangement of sections|index|chapter\b.*)$"
)
_YEAR_RE = re.compile(r"\b(1[6-9]\d{2}|20\d{2})\b")
_WS_RE = re.compile(r"\s+")


def clean_line(line: str) -> str:
    """Strip OCR artifacts (soft hyphens, replacement chars) and collapse whitespace."""
    cleaned = line.replace("\xad", "-").replace("�", "-")
    return _WS_RE.sub(" ", cleaned).strip()


def extract_title(text: str) -> str | None:
    """Derive a statute title from the heading block of the document text.

    Walks the first lines, skips page markers, and collects the heading block
    until a structural stop line (CONTENTS, SECTIONS, ...) or until a line that
    contains a statute-type keyword (ACT, ORDINANCE, ...) closes the title.
    Best-effort: the corpus is OCR-extracted, so a minority of titles keep noise.
    """
    collected: list[str] = []
    for raw in text.split("\n")[:60]:
        line = clean_line(raw)
        if not line:
            if collected:
                break
            continue
        if _PAGE_LINE_RE.match(line):
            continue
        if _STOP_LINE_RE.match(line):
            break
        collected.append(line)
        if _TITLE_KEYWORD_RE.search(line.upper()):
            break
        if len(collected) >= 4:
            break
    if not collected:
        return None
    title = _WS_RE.sub(" ", " ".join(collected)).strip(" ,;:-")
    return title or None


def extract_year(title: str | None, text: str) -> int | None:
    """Enactment year: last 4-digit year in the title, else first one in the text head."""
    if title:
        years = _YEAR_RE.findall(title)
        if years:
            return int(years[-1])
    match = _YEAR_RE.search(text[:500])
    return int(match.group(1)) if match else None


def law_source_url(file_name: str) -> str:
    """Canonical origin PDF at the Ministry of Law and Justice (pakistancode.gov.pk)."""
    return f"{PAKISTANCODE_PDF_BASE}{file_name}"


def parse_case_id(raw: str | None) -> str | None:
    """Extract the registry file id from the dataset's ``citation_number`` field.

    The field is a Python-repr dict string like
    ``"{'id': 'Crl.A.93_2013.pdf', 'url': ''}"``; fall back to treating the raw
    value as the id itself.
    """
    if not raw:
        return None
    value = raw.strip()
    if value.startswith("{"):
        try:
            parsed = ast.literal_eval(value)
        except (ValueError, SyntaxError):
            parsed = None
        if isinstance(parsed, dict):
            candidate = parsed.get("id")
            if isinstance(candidate, str) and candidate.strip():
                value = candidate.strip()
            else:
                return None
    return value or None


def case_citation(case_id: str | None) -> str | None:
    """Human-readable Supreme Court citation from a registry file id.

    ``Crl.A.93_2013.pdf`` -> ``Supreme Court of Pakistan, Crl.A. 93/2013``.
    """
    if not case_id:
        return None
    stem = case_id[:-4] if case_id.lower().endswith(".pdf") else case_id
    head, sep, year = stem.rpartition("_")
    pretty = f"{head} {year}".strip() if not sep else f"{head}/{year}"
    pretty = pretty.replace("_", " ").strip()
    return f"Supreme Court of Pakistan, {pretty}" if pretty else None
