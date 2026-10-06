"""Citation parsing and content matching for ``pk_verify_citations``.

Pure functions only - no network, no file IO. The tool in ``server.py`` does the
lookups; this module extracts Pakistani legal citations from free text, indexes
the statute corpus for existence checks, and scores a claimed description
against the real provision text.

Pattern credit: the parse-verify-report loop (act resolution from the
surrounding context, existence check with a range hint, optional content
matching, hard ``[HALLUCINATION_DETECTED]`` semantics, explicit
``[NO_CITATIONS_FOUND]`` marker) is adapted from chrisryugj/korean-law-mcp
(MIT). The Korean original uses character bigrams because Korean is
agglutinative; for English legal text character trigrams discriminate better,
so the content matcher here is trigram-based (same choice as it-eli-mcp
v0.6.0, the first connector to carry this pattern). See THIRD_PARTY.md.

Supported citation shapes (validated against the corpus itself):

- ``section 302 of the Pakistan Penal Code`` / ``section 10A of the Pakistan
  Study Centres Act, 1976`` / ``s. 2 of the ...``
- ``sections 6 and 7 of the Privatisation Commission Ordinance, 2000``
- ``sub-section (1) of section 6 of the ...`` / ``section 302(b) of the ...``
- ``Act No. XLV of 1860`` / ``Ordinance No. LII of 2000`` /
  ``Order No. 1 of 1999`` (Roman or Arabic numerals, act-level existence)
- ``Article 184(3) of the Constitution`` (the tail "of the Constitution" is
  required; a bare "Article 5" is too ambiguous and is deliberately ignored)
- ``Crl.A. 93/2013`` / ``Crl.A. No. 93 of 2013`` / ``Criminal Appeal No. 93
  of 2013`` (Supreme Court registry citations)
- ``PLD 2019 SC 1`` / ``2019 SCMR 1421`` (reporter citations - parsed, but
  reported as out_of_corpus: the judgment corpus is keyed by registry id)

The act reference is searched AFTER the section number first (the usual
English order: "section 5 of the X Act"), then BEFORE it ("The X Act, 1930,
in section 5, provides ...").

Corpus caveat, stated rather than hidden: statute texts are OCR extractions
from official PDFs. Section detection reads the ``N. Heading`` markers that
survived OCR, so both the existence check and the range hint describe the
machine-detectable text, not a certified table of sections.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Text normalization and trigram matching
# ---------------------------------------------------------------------------

# Warning threshold for the trigram scores. Below it the claimed description is
# flagged as a content mismatch - a signal for review, never a hard block
# (measured for Latin-script legal text; the Korean original uses bigrams at
# 0.25 for an agglutinative script).
CONTENT_WARN_THRESHOLD = 0.2


def normalize_text(s: str) -> str:
    """Lowercase, strip accents, drop punctuation, collapse whitespace."""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"[^0-9a-z]+", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def compact_text(s: str) -> str:
    """Normalized text with all spaces removed.

    OCR-safe: the corpus contains letter-spaced titles like
    ``T H E O A T H A C T , 1 8 7 3`` - compacting collapses them to
    ``theoathact1873``, so containment matching survives that noise.
    """
    return normalize_text(s).replace(" ", "")


def _trigrams(s: str) -> set[str]:
    compact = compact_text(s)
    if len(compact) < 3:
        return {compact} if compact else set()
    return {compact[i:i + 3] for i in range(len(compact) - 2)}


def trigram_jaccard(a: str, b: str) -> float:
    """Symmetric trigram Jaccard - for claim vs heading (comparable lengths)."""
    ta, tb = _trigrams(a), _trigrams(b)
    if not ta or not tb:
        return 0.0
    union = len(ta | tb)
    return len(ta & tb) / union if union else 0.0


def trigram_overlap(claim: str, body: str) -> float:
    """Asymmetric overlap - share of claim trigrams found in the (longer) body."""
    tc, tb = _trigrams(claim), _trigrams(body)
    if not tc or not tb:
        return 0.0
    return len(tc & tb) / len(tc)


def match_claim(claim: str, heading: str | None, body: str) -> tuple[bool, str, float]:
    """Score a claimed description against the real provision.

    Returns ``(matched, method, score)``. Layers:
    L1 exact - the normalized claim is a substring of heading+body;
    L2 trigram Jaccard vs the heading (symmetric, comparable lengths);
    L3 trigram overlap vs the body (asymmetric - claim is much shorter).
    """
    reference = f"{heading or ''} {body}".strip()
    c = compact_text(claim)
    if c and c in compact_text(reference):
        return True, "exact", 1.0

    score_heading = trigram_jaccard(claim, heading) if heading else 0.0
    score_body = trigram_overlap(claim, body[:1500]) if body else 0.0
    if score_heading >= score_body:
        method, score = "trigram-jaccard", score_heading
    else:
        method, score = "trigram-overlap", score_body
    return score >= CONTENT_WARN_THRESHOLD, method, round(score, 3)


# ---------------------------------------------------------------------------
# Roman numerals ("Act No. XLV of 1860")
# ---------------------------------------------------------------------------

_ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}


def roman_to_int(s: str) -> int | None:
    """Parse a Roman numeral, or None. Liberal - OCR output is not always classical."""
    s = s.strip().upper()
    if not s or any(ch not in _ROMAN_VALUES for ch in s):
        return None
    total = 0
    for i, ch in enumerate(s):
        value = _ROMAN_VALUES[ch]
        if i + 1 < len(s) and _ROMAN_VALUES[s[i + 1]] > value:
            total -= value
        else:
            total += value
    return total if total > 0 else None


def act_number_to_int(s: str) -> int | None:
    """Act number token (Roman or Arabic) to int."""
    s = s.strip()
    if s.isdigit():
        return int(s)
    return roman_to_int(s)


# ---------------------------------------------------------------------------
# Citation parsing
# ---------------------------------------------------------------------------

# Act coordinates: "Act No. XLV of 1860", "ORDINANCE No. LII OF 2000",
# "Order No. 1 of 1999", "P.O. No. 1 of 1970". "No." is optional because
# cross-references in the corpus drop it: "(Act LXXI of 1973)" appears
# verbatim. The lookbehind excludes letters and dots but NOT digits: OCR glues
# footnote markers to the type word ("1ACT No. XXVII OF 1976" appears
# verbatim in the corpus).
_COORD_RE = re.compile(
    r"(?<![A-Za-z.])"
    r"(?P<type>Act|Ordinance|Order|P\.\s*O\.?)"
    r"\s*,?\s*(?:No\.?\s*)?"
    r"(?P<num>[IVXLCDMivxlcdm]+|\d+)"
    r"\s+of\s+(?P<year>\d{4})",
    re.IGNORECASE,
)

# Header-only variant for INDEXING the corpus: "No." is REQUIRED. Statute
# bodies are full of no-"No." cross-references ("(Act XLV of 1860)" appears in
# dozens of preambles), and indexing those would key amendment acts under the
# parent act's coordinates. Measured on the 2025-01-30 snapshot: 746 of 967
# docs carry a strict header in their first 8,000 characters.
_COORD_HEADER_RE = re.compile(
    r"(?<![A-Za-z.])"
    r"(?P<type>Act|Ordinance|Order|P\.\s*O\.?)"
    r"\s*,?\s*No\.?\s*"
    r"(?P<num>[IVXLCDMivxlcdm]+|\d+)"
    r"\s+of\s+(?P<year>\d{4})",
    re.IGNORECASE,
)

_TYPE_MAP = {"act": "act", "ordinance": "ordinance", "order": "order", "po": "order"}


def _coord_type(token: str) -> str:
    return _TYPE_MAP[re.sub(r"[^a-z]", "", token.lower())]


# Short title: "the Pakistan Penal Code", "the Privatisation Commission
# Ordinance, 2000", "the Code of Criminal Procedure, 1898". Title words are
# capitalized (plus connectives), which keeps "the said Act" and other prose
# out. A match consisting only of the type keyword is rejected downstream.
# The word class carries both the straight and the curly apostrophe (U+2019) -
# OCR output mixes them in possessives like "People's".
_TITLE_WORD = r"(?:[A-Z][\w()'’.-]*|of|and|the|for|to|in|e)"  # noqa: RUF001
# "[Tt]he": sentence-initial references ("The Sale of Goods Act, 1930
# defines ...") are as real as mid-sentence ones. The Code/Constitution branch
# is GREEDY: non-greedy stopped at "Code of Criminal" instead of "Code of
# Criminal Procedure"; greedy over-extension across prose is stopped by the
# capitalization requirement, and a merged title merely fails to resolve
# (reported as a gap, never a wrong verdict).
_SHORT_TITLE_RE = re.compile(
    rf"\b[Tt]he\s+(?P<title>"
    rf"(?:Code|Constitution)\s+of\s+(?:{_TITLE_WORD}\s+)*{_TITLE_WORD}"
    rf"|(?:{_TITLE_WORD}\s+)+?(?:Act|Ordinance|Code|Order|Rules|Regulations)"
    rf")(?:\s*,?\s*(?P<year>1[6-9]\d\d|20\d\d))?"
)

# Section references: "section 302", "sections 6 and 7", "s. 2",
# "sub-section (1) of section 6", "section 302(b)".
_SEC_RE = re.compile(
    r"\b(?:sub-?section\s*\(\s*(?P<sub_pre>\d{1,2}[A-Za-z]?)\s*\)\s*of\s+)?"
    r"(?:(?:S|s)ections?|(?<![\w.])s\.)\s*"
    r"(?P<num>\d{1,4}[A-Z]{0,2})"
    r"(?:\s?\(\s*(?P<sub_post>\d{1,2}|[a-z])\s*\))?",
)

# Enumeration continuation: "sections 6 and 7", "sections 2, 3 and 4".
_SEC_CONT_RE = re.compile(r"\s*(?:,|and)\s+(\d{1,4}[A-Z]{0,2})\b(?!\s*\()")

# Constitution: "Article 184(3) of the Constitution [of the Islamic Republic
# of Pakistan]". The tail is REQUIRED - a bare "Article 5" could be any treaty.
_CONST_RE = re.compile(
    r"\bArticles?\s+(?P<num>\d{1,3}[A-Z]?)\s*(?:\(\s*(?P<clause>\d{1,2})\s*\))?"
    r"(?:\s*,)?\s+of\s+the\s+Constitution"
    r"(?:\s+of\s+the\s+Islamic\s+Republic\s+of\s+Pakistan)?",
    re.IGNORECASE,
)

# Supreme Court registry citations: "Crl.A. 93/2013", "Const.P. 15 of 2012",
# "S.M.C. No. 3/2009". The prefix is dotted abbreviation groups, as in the
# registry file ids of the judgment corpus ("Crl.A.93_2013.pdf").
_CASE_RE = re.compile(
    r"\b(?P<prefix>(?:[A-Z][A-Za-z]{0,5}\.\s?){1,4})"
    r"\s*(?:No\.?\s*)?"
    r"(?P<num>\d{1,5})"
    r"\s*(?:[/_]\s*|\s+of\s+)"
    r"(?P<year>\d{4})\b"
)

# Longhand case citations: "Criminal Appeal No. 93 of 2013".
_CASE_LONG_RE = re.compile(
    r"\b(?P<kind>Civil|Criminal|Constitution|Human\s+Rights|Suo\s+Motu?)\s+"
    r"(?P<form>Appeal|Petition|Review\s+Petition|Case|Miscellaneous\s+Application)s?\s+"
    r"No\.?\s*(?P<num>\d{1,5})\s+of\s+(?P<year>\d{4})\b",
    re.IGNORECASE,
)

# Longhand (kind, form) -> registry prefix, matching the corpus file ids.
_CASE_PREFIXES = {
    ("criminal", "appeal"): "Crl.A.",
    ("civil", "appeal"): "C.A.",
    ("civil", "petition"): "C.P.",
    ("criminal", "petition"): "Crl.P.",
    ("constitution", "petition"): "Const.P.",
    ("civil", "review petition"): "C.R.P.",
    ("criminal", "review petition"): "Crl.R.P.",
    ("human rights", "case"): "H.R.C.",
    ("suo motu", "case"): "S.M.C.",
    ("suo moto", "case"): "S.M.C.",
    ("civil", "miscellaneous application"): "C.M.A.",
    ("criminal", "miscellaneous application"): "Crl.M.A.",
}

# Reporter citations - parsed so they can be DISCLOSED as out-of-corpus, never
# silently dropped. "PLD 2019 SC 1", "2019 SCMR 1421".
_REPORTER_RE = re.compile(r"\b(?:PLD\s+(\d{4})\s+SC\s+\d{1,5}|(\d{4})\s+SCMR\s+\d{1,5})\b")

# How far around the section number we look for the act reference.
_AFTER_WINDOW = 120
_AFTER_MAX_START = 40   # the act ref must begin close to the section number
_BEFORE_WINDOW = 130
_BEFORE_MIN_END = 60    # ... or end close before it

# Claimed description: "(punishment of qatl-i-amd)" right after the citation.
_CLAIM_RE = re.compile(r"^\s*[(«]([^)»]{3,120})[)»]")
_CLAIM_REJECT = re.compile(
    r"^\s*(?:\d|as\s+amended|amended|substitut|insert|omitt|repeal|added|"
    r"act\b|ordinance\b|order\b|no\.|section\b|article\b)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ParsedCitation:
    """One citation extracted from free text, before verification."""

    raw: str                          # the citation as it appears in the text
    kind: str                         # "statute" | "constitution" | "case" | "reporter"
    section: str | None = None        # section label ("302", "10A") or Article number
    subsection: str | None = None     # sub-section / clause token ("1", "b", "3")
    act_title: str | None = None      # short title from context, e.g. "Pakistan Penal Code"
    act_title_year: int | None = None
    act_type: str | None = None       # "act" | "ordinance" | "order"
    act_number: int | None = None
    act_year: int | None = None
    case_id: str | None = None        # registry id, e.g. "Crl.A.93_2013"
    case_display: str | None = None   # e.g. "Crl.A. 93/2013"
    reporter: str | None = None       # e.g. "PLD 2019 SC 1"
    claim: str | None = None          # claimed description for the content check


def _overlaps(span: tuple[int, int], used: list[tuple[int, int]]) -> bool:
    return any(span[0] < e and span[1] > s for s, e in used)


def _extract_claim(after: str) -> str | None:
    m = _CLAIM_RE.match(after)
    if not m:
        return None
    claim = m.group(1).strip()
    if not re.search(r"[a-z]", claim, re.IGNORECASE):
        return None
    if _CLAIM_REJECT.match(claim):
        return None
    return claim


def _title_fields(m: re.Match[str]) -> dict[str, object] | None:
    title = re.sub(r"\s+", " ", m.group("title")).strip(" ,")
    # A match that is only the type keyword ("the Act") is prose, not a title.
    if re.fullmatch(r"(?:Act|Ordinance|Code|Order|Rules|Regulations)", title):
        return None
    fields: dict[str, object] = {"act_title": title}
    if m.group("year"):
        fields["act_title_year"] = int(m.group("year"))
    return fields


def _coord_fields(m: re.Match[str]) -> dict[str, object] | None:
    number = act_number_to_int(m.group("num"))
    if number is None:
        return None
    return {
        "act_type": _coord_type(m.group("type")),
        "act_number": number,
        "act_year": int(m.group("year")),
    }


def _act_from_after(window: str) -> tuple[dict[str, object], int] | None:
    """Act reference AFTER the section number ("section 5 of the X Act")."""
    coord = _COORD_RE.search(window)
    title = _SHORT_TITLE_RE.search(window)
    title_fields = _title_fields(title) if title else None
    coord_fields = _coord_fields(coord) if coord else None
    coord_start = coord.start() if coord and coord_fields else None
    title_start = title.start() if title and title_fields else None
    if (coord_start is not None
            and (title_start is None or coord_start <= title_start)
            and coord_start <= _AFTER_MAX_START):
        assert coord is not None and coord_fields is not None
        return coord_fields, coord.end()
    if title_start is not None and title_start <= _AFTER_MAX_START:
        assert title is not None and title_fields is not None
        # A coordinate in parentheses right after the title refines the reference:
        # "the Civil Servants Act, 1973 (Act LXXI of 1973)".
        end = title.end()
        trail = _COORD_RE.match(window[end:end + 40].lstrip(" ("))
        if trail:
            extra = _coord_fields(trail)
            if extra:
                title_fields.update(extra)
        return title_fields, end
    return None


def _act_from_before(window: str) -> dict[str, object] | None:
    """Act reference BEFORE the section number ("The X Act, 1930 ... section 5")."""
    best: tuple[dict[str, object], int] | None = None
    for m in _COORD_RE.finditer(window):
        fields = _coord_fields(m)
        if fields is not None and (best is None or m.end() > best[1]):
            best = (fields, m.end())
    for m in _SHORT_TITLE_RE.finditer(window):
        fields = _title_fields(m)
        if fields is not None and (best is None or m.end() > best[1]):
            best = (fields, m.end())
    if best is not None and best[1] >= len(window) - _BEFORE_MIN_END:
        return best[0]
    return None


def _registry_case_id(prefix: str, num: str, year: str) -> str:
    """Normalize to the corpus registry id shape: 'Crl.A. 93/2013' -> 'Crl.A.93_2013'."""
    compact_prefix = re.sub(r"\s+", "", prefix)
    # "Const.P. No. 15 of 2012": the dotted-groups prefix regex swallows the
    # "No." token; no registry prefix ends in it, so strip it here.
    compact_prefix = re.sub(r"[Nn][Oo]\.?$", "", compact_prefix)
    if not compact_prefix.endswith("."):
        compact_prefix += "."
    return f"{compact_prefix}{num}_{year}"


def parse_citations(text: str, max_citations: int = 15) -> list[ParsedCitation]:
    """Extract Pakistani legal citations from ``text``, deduplicated, in order."""
    text = text.replace("\xa0", " ")
    found: list[tuple[int, ParsedCitation]] = []
    used: list[tuple[int, int]] = []
    seen: set[tuple[object, ...]] = set()

    def _add(pos: int, span: tuple[int, int], key: tuple[object, ...],
             cite: ParsedCitation) -> None:
        if key in seen:
            return
        seen.add(key)
        used.append(span)
        found.append((pos, cite))

    # 1. Constitution articles (before sections: "Article" never collides, but
    #    the span mask keeps later passes away from the tail words).
    for m in _CONST_RE.finditer(text):
        clause = m.group("clause")
        claim = _extract_claim(text[m.end():m.end() + 140]) if not clause else None
        _add(m.start(), m.span(), ("const", m.group("num").upper(), clause),
             ParsedCitation(
                 raw=re.sub(r"\s+", " ", m.group(0)).strip(),
                 kind="constitution",
                 section=m.group("num").upper(),
                 subsection=clause,
                 claim=claim,
             ))

    # 2. Statute sections, with the act resolved from context.
    for m in _SEC_RE.finditer(text):
        if _overlaps(m.span(), used):
            continue
        sections = [m.group("num").upper()]
        end = m.end()
        if not m.group("sub_post"):
            while True:
                cont = _SEC_CONT_RE.match(text, end)
                if cont is None:
                    break
                sections.append(cont.group(1).upper())
                end = cont.end()
        subsection = m.group("sub_pre") or m.group("sub_post")

        after = text[end:end + _AFTER_WINDOW]
        act: dict[str, object] = {}
        claim_start = end
        found_after = _act_from_after(after)
        if found_after is not None:
            act, ref_end = found_after
            claim_start = end + ref_end
        else:
            before = text[max(0, m.start() - _BEFORE_WINDOW):m.start()]
            found_before = _act_from_before(before)
            if found_before is not None:
                act = found_before
        claim = _extract_claim(text[claim_start:claim_start + 140])

        raw_end = claim_start if found_after is not None else end
        raw = re.sub(r"\s+", " ", text[m.start():raw_end]).strip().rstrip(",;")
        for i, section in enumerate(sections):
            key = (
                "sec", act.get("act_title"), act.get("act_type"), act.get("act_number"),
                act.get("act_year"), act.get("act_title_year"), section,
                subsection if i == 0 else None,
            )
            _add(m.start(), (m.start(), raw_end), key, ParsedCitation(
                raw=raw if len(sections) == 1 else f"section {section} ({raw})",
                kind="statute",
                section=section,
                subsection=subsection if i == 0 else None,
                claim=claim if i == 0 else None,
                act_title=act.get("act_title"),           # type: ignore[arg-type]
                act_title_year=act.get("act_title_year"),  # type: ignore[arg-type]
                act_type=act.get("act_type"),             # type: ignore[arg-type]
                act_number=act.get("act_number"),         # type: ignore[arg-type]
                act_year=act.get("act_year"),             # type: ignore[arg-type]
            ))

    # 3. Bare act coordinates not already consumed by a section binding.
    for m in _COORD_RE.finditer(text):
        if _overlaps(m.span(), used):
            continue
        fields = _coord_fields(m)
        if fields is None:
            continue
        claim = _extract_claim(text[m.end():m.end() + 140])
        key = ("act", fields["act_type"], fields["act_number"], fields["act_year"])
        _add(m.start(), m.span(), key, ParsedCitation(
            raw=re.sub(r"\s+", " ", m.group(0)).strip(),
            kind="statute",
            claim=claim,
            act_type=fields["act_type"],      # type: ignore[arg-type]
            act_number=fields["act_number"],  # type: ignore[arg-type]
            act_year=fields["act_year"],      # type: ignore[arg-type]
        ))

    # 4. Supreme Court registry citations (abbreviated, then longhand).
    for m in _CASE_RE.finditer(text):
        if _overlaps(m.span(), used):
            continue
        prefix_compact = re.sub(r"\s+", "", m.group("prefix")).upper()
        if prefix_compact in {"P.O.", "P.O"}:  # President's Order = statute, not case
            continue
        if prefix_compact in {"NO.", "NO"}:
            # Bare "No. 93 of 2013" is the tail of a longhand citation
            # ("Criminal Appeal No. 93 of 2013") - leave it to _CASE_LONG_RE.
            continue
        case_id = _registry_case_id(m.group("prefix"), m.group("num"), m.group("year"))
        _add(m.start(), m.span(), ("case", case_id.upper()), ParsedCitation(
            raw=re.sub(r"\s+", " ", m.group(0)).strip(),
            kind="case",
            case_id=case_id,
            case_display=f"{case_id.rsplit('_', 1)[0]} {m.group('num')}/{m.group('year')}"
            .replace(f".{m.group('num')} ", ". "),
        ))
    for m in _CASE_LONG_RE.finditer(text):
        if _overlaps(m.span(), used):
            continue
        kind_key = re.sub(r"\s+", " ", m.group("kind")).lower()
        form_key = re.sub(r"\s+", " ", m.group("form")).lower()
        prefix = _CASE_PREFIXES.get((kind_key, form_key))
        if prefix is None:
            continue
        case_id = f"{prefix}{m.group('num')}_{m.group('year')}"
        _add(m.start(), m.span(), ("case", case_id.upper()), ParsedCitation(
            raw=re.sub(r"\s+", " ", m.group(0)).strip(),
            kind="case",
            case_id=case_id,
            case_display=f"{prefix} {m.group('num')}/{m.group('year')}",
        ))

    # 5. Reporter citations - disclosed, never resolvable against this corpus.
    for m in _REPORTER_RE.finditer(text):
        if _overlaps(m.span(), used):
            continue
        reporter = re.sub(r"\s+", " ", m.group(0)).strip()
        _add(m.start(), m.span(), ("rep", reporter.upper()), ParsedCitation(
            raw=reporter, kind="reporter", reporter=reporter,
        ))

    found.sort(key=lambda item: item[0])
    return [cite for _, cite in found[:max_citations]]


# ---------------------------------------------------------------------------
# Section detection in OCR statute text
# ---------------------------------------------------------------------------

# "302. Punishment of qatl-i-amd" at line start - the marker that survives OCR
# in both the CONTENTS block and the body.
_SECTION_MARK_RE = re.compile(r"(?m)^\s*(\d{1,4}[A-Z]{0,2})\s*\.\s+[A-Z(\"'“]")

# Numbers whose gap to the previous detected section exceeds this are treated
# as OCR noise (page numbers, years); real section sequences are dense.
_MAX_SECTION_GAP = 50


@dataclass(frozen=True)
class SectionMap:
    """Machine-detectable section labels of one statute text."""

    labels: frozenset[str]      # exact labels, e.g. {"1", "10", "10A", "302"}
    numbers: tuple[int, ...]    # pruned numeric sequence, sorted
    plausible: bool             # dense enough to support a MISSING verdict

    def has(self, wanted: str) -> bool:
        return wanted.upper() in self.labels


def section_map(text: str) -> SectionMap:
    """Detect section labels in OCR statute text, pruning trailing outliers."""
    labels = {m.group(1).upper() for m in _SECTION_MARK_RE.finditer(text)}
    numbers = sorted({int(re.match(r"\d+", label).group()) for label in labels})  # type: ignore[union-attr]
    pruned: list[int] = []
    for n in numbers:
        if not pruned or n - pruned[-1] <= _MAX_SECTION_GAP:
            pruned.append(n)
        else:
            break
    pruned_set = set(pruned)
    kept = frozenset(
        label for label in labels
        if int(re.match(r"\d+", label).group()) in pruned_set  # type: ignore[union-attr]
    )
    plausible = len(pruned) >= 5 and 1 in pruned_set
    return SectionMap(labels=kept, numbers=tuple(pruned), plausible=plausible)


# Where the last detected marker sits, as a fraction of the text. When markers stop
# early (Contract Act, 1872: 9 of 238 sections, last marker at 13% of the text), the
# rest of the act was simply not machine-readable - a section number beyond the last
# marker is then unchecked, not missing. Measured on the 2025-01-30 corpus: 594 of 799
# mapped statutes have their last marker at >= 80% of the text.
MARKERS_COVER_TEXT = 0.8


def markers_end_fraction(text: str, sections: SectionMap) -> float:
    """Position of the last detected section marker as a fraction of ``text`` (0.0-1.0)."""
    if not sections.numbers or not text:
        return 0.0
    last = str(sections.numbers[-1])
    ends = [m.start() for m in _SECTION_MARK_RE.finditer(text)
            if re.match(r"\d+", m.group(1)).group() == last]  # type: ignore[union-attr]
    return max(ends) / len(text) if ends else 0.0


def range_hint(sections: SectionMap, wanted: str, unit: str = "section",
               scope: str = "this act") -> str:
    """'section 999 does not exist in this act; 48 sections are detectable ...'."""
    if not sections.numbers:
        return (f"{unit} {wanted} not found; no numbered {unit}s are "
                f"machine-detectable in {scope}.")
    return (
        f"{unit} {wanted} does not exist in {scope}; {len(sections.labels)} {unit}s "
        f"are machine-detectable in its text, numbered {unit} "
        f"{sections.numbers[0]}-{sections.numbers[-1]}."
    )


def section_excerpt(text: str, label: str, max_chunk: int = 1200,
                    max_occurrences: int = 3) -> tuple[str | None, str]:
    """Heading and body excerpt for one section label.

    The label usually occurs twice (CONTENTS line + body); both are collected,
    bounded by the next section marker. The heading is the marker-line remainder
    of the first occurrence (the CONTENTS line is the cleanest).
    """
    marker = re.compile(rf"(?m)^\s*{re.escape(label)}\s*\.\s+")
    chunks: list[str] = []
    heading: str | None = None
    for m in list(marker.finditer(text))[:max_occurrences]:
        nxt = _SECTION_MARK_RE.search(text, m.end())
        end = min(nxt.start() if nxt else len(text), m.end() + max_chunk)
        chunk = text[m.end():end]
        chunks.append(chunk)
        if heading is None:
            head = re.split(r"[.—_]|--", chunk, maxsplit=1)[0]
            head = re.sub(r"\s+", " ", head).strip()
            if 3 <= len(head) <= 120:
                heading = head
    return heading, "\n".join(chunks)


def detect_subsections(chunk: str) -> tuple[list[int], list[str]]:
    """Sub-section and clause markers detectable in a flattened section excerpt.

    Returns ``(numeric_markers, letter_markers)``. The OCR flattening is lossy,
    so callers must treat a miss as a warning, never as proof of absence. An
    implausible numeric sequence returns ``[]`` (not checkable).
    """
    nums = sorted({
        int(m.group(1)) for m in re.finditer(r"\(\s*(\d{1,2})\s*\)", chunk)
        if 1 <= int(m.group(1)) <= 50
    })
    letters = sorted({m.group(1) for m in re.finditer(r"\(\s*([a-z])\s*\)", chunk)})
    if len(nums) < 2 or 1 not in nums:
        nums = []
    return nums, letters


# ---------------------------------------------------------------------------
# Act index over the in-memory corpus
# ---------------------------------------------------------------------------

_HEADER_SCAN_CHARS = 8000
_CONSTITUTION_COMPACT = "constitutionoftheislamicrepublicofpakistan"
_MIN_TITLE_COMPACT = 8  # shorter compacted titles are too generic to match


@dataclass
class IndexedLaw:
    """The slice of one corpus law the verifier needs."""

    law_id: str
    title: str | None
    year: int | None
    text: str
    compact_title: str = ""

    def __post_init__(self) -> None:
        if not self.compact_title:
            self.compact_title = compact_text(self.title or "")


@dataclass
class ActIndex:
    """Existence index over the statute corpus: coordinates + compacted titles.

    Coordinate keys come from the "ACT No. XLV OF 1860" header when it survived
    OCR near the top of the document (roughly half the corpus carries one), so a
    coordinate miss means UNKNOWN, not disproven - the caller reports it as an
    out-of-corpus gap.
    """

    laws: list[IndexedLaw] = field(default_factory=list)
    by_coord: dict[tuple[str, int, int], IndexedLaw] = field(default_factory=dict)
    constitution_law: IndexedLaw | None = None
    _sections: dict[str, SectionMap] = field(default_factory=dict)
    _coord_offset: dict[tuple[str, int, int], int] = field(default_factory=dict)

    @classmethod
    def build(cls, laws: list) -> ActIndex:  # laws: corpus.Law-compatible objects
        index = cls()
        for law in laws:
            indexed = IndexedLaw(
                law_id=law.law_id, title=law.title, year=law.year, text=law.text,
            )
            index.laws.append(indexed)
            head = law.text[:_HEADER_SCAN_CHARS].replace("\xa0", " ")
            m = _COORD_HEADER_RE.search(head)
            if m:
                fields = _coord_fields(m)
                if fields:
                    key = (
                        str(fields["act_type"]),
                        int(fields["act_number"]),   # type: ignore[arg-type]
                        int(fields["act_year"]),     # type: ignore[arg-type]
                    )
                    # On a key conflict the doc whose match sits closest to the
                    # top wins: a real header follows the title; a preamble
                    # cross-reference sits deeper in the text.
                    if (key not in index.by_coord
                            or m.start() < index._coord_offset[key]):
                        index.by_coord[key] = indexed
                        index._coord_offset[key] = m.start()
            if (index.constitution_law is None
                    and _CONSTITUTION_COMPACT in compact_text(law.text[:300])):
                index.constitution_law = indexed
        return index

    def resolve_coord(self, act_type: str, number: int, year: int) -> IndexedLaw | None:
        return self.by_coord.get((act_type, number, year))

    def resolve_title(self, title: str, year: int | None) -> IndexedLaw | None:
        """Best law whose compacted title contains the compacted citation title."""
        wanted = compact_text(title)
        if len(wanted) < _MIN_TITLE_COMPACT:
            return None
        candidates = [law for law in self.laws if wanted in law.compact_title]
        if year is not None:
            year_matched = [
                law for law in candidates
                if law.year == year or str(year) in law.compact_title
            ]
            if year_matched:
                candidates = year_matched
        if not candidates:
            return None
        return min(candidates, key=lambda law: len(law.compact_title))

    def sections(self, law: IndexedLaw) -> SectionMap:
        cached = self._sections.get(law.law_id)
        if cached is None:
            cached = section_map(law.text)
            self._sections[law.law_id] = cached
        return cached
