"""Pydantic v2 models for pk-eli-mcp (Pakistani statutes + Supreme Court judgments)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DATASET_NOTE_LAWS = (
    "Corpus-based connector, not a live government API: Pakistan's official portal "
    "(pakistancode.gov.pk) exposes no machine interface, so statutes are served from the "
    "AyeshaJadoon/Pakistan_Laws_Dataset mirror on HuggingFace (967 federal laws, full text, "
    "ODC-BY 1.0), a static snapshot last updated 2025-01-30. Amendments after that date are "
    "NOT reflected - verify currency against pakistancode.gov.pk before relying on a provision."
)

DATASET_NOTE_CASES = (
    "Corpus-based connector: judgments come from the Ibtehaj10/supreme-court-of-pak-judgments "
    "dataset on HuggingFace (1,414 Supreme Court of Pakistan judgments, full text, MIT), a "
    "static snapshot from 2024-07-26 covering a SUBSET of the Court's output - absence of a "
    "judgment here does not mean it does not exist."
)

ELI_NOTE = (
    "Pakistan has not deployed ELI. For statutes, eli_uri is the canonical Ministry of Law "
    "and Justice PDF URL (https://pakistancode.gov.pk/pdffiles/{law_id}); for judgments it is "
    "the HuggingFace dataset row URI. Stable and resolvable, never invented."
)


class _Tolerant(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class LawSummary(_Tolerant):
    """One statute as returned by ``pk_search_laws``."""

    law_id: str
    title: str | None = None
    year: int | None = None
    snippet: str | None = None

    # Citation contract (Art. IV CONSTITUTION).
    eli_uri: str | None = None
    eli_note: str = ELI_NOTE
    human_readable_citation: str | None = None
    source_url: str | None = None


class LawSearchResult(_Tolerant):
    """Result of ``pk_search_laws``."""

    query: str
    total_matches: int
    items: list[LawSummary] = Field(default_factory=list)
    dataset_note: str = DATASET_NOTE_LAWS


class LawText(_Tolerant):
    """Result of ``pk_get_law`` - the full text of a statute, possibly truncated."""

    law_id: str
    title: str | None = None
    year: int | None = None
    eli_uri: str | None = None
    eli_note: str = ELI_NOTE
    human_readable_citation: str | None = None
    source_url: str | None = None
    content: str | None = None
    byte_size: int | None = None
    truncated: bool = False
    dataset_note: str = DATASET_NOTE_LAWS


class CaseSummary(_Tolerant):
    """One judgment as returned by ``pk_case_search``."""

    row_idx: int
    case_id: str | None = None
    snippet: str | None = None

    eli_uri: str | None = None
    eli_note: str = ELI_NOTE
    human_readable_citation: str | None = None
    source_url: str | None = None


class CaseSearchResult(_Tolerant):
    """Result of ``pk_case_search``."""

    query: str
    total_matches: int
    items: list[CaseSummary] = Field(default_factory=list)
    dataset_note: str = DATASET_NOTE_CASES


class DecisionText(_Tolerant):
    """Result of ``pk_get_decision`` - the full text of one judgment."""

    row_idx: int
    case_id: str | None = None
    eli_uri: str | None = None
    eli_note: str = ELI_NOTE
    human_readable_citation: str | None = None
    source_url: str | None = None
    content: str | None = None
    byte_size: int | None = None
    truncated: bool = False
    dataset_note: str = DATASET_NOTE_CASES


# ---------------------------------------------------------------------------
# pk_verify_citations
# ---------------------------------------------------------------------------

DATASET_NOTE_VERIFY = (
    "Verification runs against the local corpus snapshots (statutes 2025-01-30, judgments "
    "2024-07-26). Statute section detection reads the 'N. Heading' markers that survived OCR, "
    "so [MISSING] describes the machine-detectable text of the snapshot - treat it as a strong "
    "signal and confirm at source_url before accusing anyone. The judgment corpus is a 1,414-"
    "judgment SUBSET, so a case citation that is not found is reported as a gap, never as a "
    "hallucination. Everything the tool could not check lands in `gaps`, not in silence."
)

CitationStatus = Literal["verified", "not_found", "content_mismatch", "unverified"]
VerificationStatus = Literal[
    "VERIFIED", "PARTIAL_VERIFIED", "HALLUCINATION_DETECTED", "NO_CITATIONS_FOUND"
]
GapType = Literal[
    "out_of_corpus",
    "unparseable_citation",
    "upstream_unavailable",
    "sections_not_checkable",
    "subsection_not_checkable",
]


class ContentMatch(_Tolerant):
    """Trigram comparison of a claimed description against the real provision."""

    matched: bool
    method: Literal["exact", "trigram-jaccard", "trigram-overlap"]
    score: float


class CitationCheck(_Tolerant):
    """Verification outcome for one citation found in the input text."""

    raw: str
    kind: Literal["statute", "constitution", "supreme_court_case", "reporter_citation"]
    act_reference: str | None = None
    section: str | None = None
    subsection: str | None = None
    status: CitationStatus
    detail: str
    range_hint: str | None = None
    claim: str | None = None
    content_match: ContentMatch | None = None
    human_readable_citation: str | None = None
    source_url: str | None = None


class VerificationGap(_Tolerant):
    """An explicit incompleteness of the verification - never hidden in prose."""

    gap_type: GapType
    citation: str | None = None
    note: str


class CitationVerificationResult(_Tolerant):
    """Result of ``pk_verify_citations``."""

    status: VerificationStatus
    summary: str
    total: int
    verified_count: int
    failed_count: int
    warning_count: int
    citations: list[CitationCheck] = Field(default_factory=list)
    gaps: list[VerificationGap] = Field(default_factory=list)
    dataset_note: str = DATASET_NOTE_VERIFY
