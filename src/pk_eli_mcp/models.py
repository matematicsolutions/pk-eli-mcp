"""Pydantic v2 models for pk-eli-mcp (Pakistani statutes + Supreme Court judgments)."""

from __future__ import annotations

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
