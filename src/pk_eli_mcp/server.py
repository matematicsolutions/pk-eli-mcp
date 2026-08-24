"""FastMCP entry point - Pakistani federal legislation + Supreme Court judgments.

Run:

    python -m pk_eli_mcp.server

Configuration via env:

- ``PK_ELI_CACHE_DIR`` (default ``~/.matematic/cache/pk-eli``)
- ``PK_ELI_AUDIT_DIR`` (default ``~/.matematic/audit``)
"""

from __future__ import annotations

import httpx
from fastmcp import FastMCP
from fastmcp.tools import ToolResult
from mcp.types import ToolAnnotations

from .audit import AuditLogger, hash_input, timer
from .citations import case_citation, clean_line, parse_case_id
from .corpus import CorpusUnavailableError, LawsCorpus, get_corpus
from .models import (
    CaseSearchResult,
    CaseSummary,
    CitationCheck,
    CitationVerificationResult,
    ContentMatch,
    DecisionText,
    LawSearchResult,
    LawSummary,
    LawText,
    VerificationGap,
)
from .sc_client import SC_DATASET, ScClient
from .verify import (
    ActIndex,
    IndexedLaw,
    ParsedCitation,
    detect_subsections,
    match_claim,
    parse_citations,
    range_hint,
    section_excerpt,
)
from .coverage import Coverage, build_coverage

_MAX_FULL_TEXT_CHARS = 300_000
_SNIPPET_CHARS = 400

INSTRUCTIONS = """\
This MCP server exposes Pakistani federal legislation (967 laws, full text) and Supreme Court of Pakistan judgments (1,414, full text). It is CORPUS-BASED, not a live government API: Pakistan publishes no machine-readable legal API, so both collections are static HuggingFace snapshots of official sources (statutes: pakistancode.gov.pk PDFs, snapshot 2025-01-30, ODC-BY 1.0; judgments: Supreme Court PDFs, snapshot 2024-07-26, MIT). Pakistan has no ELI scheme; every response carries a stable `eli_uri` (the canonical pakistancode.gov.pk PDF URL for statutes, the dataset row URI for judgments), a `human_readable_citation` and a `source_url`. See `eli_note` and `dataset_note` on every response.

## Call order

1. `pk_search_laws` - keyword search over the 967 federal statutes (title-weighted). Returns `law_id` for each hit. The first call provisions the 47 MB corpus once and caches it (a pre-built GitHub release asset verified by sha256, with the pinned HuggingFace origin as fallback); expect roughly a minute of first-call latency on a slow link, a second or two on warm starts.
2. `pk_get_law` - full statute text by `law_id`. Large statutes are truncated at roughly 300,000 characters.
3. `pk_case_search` - server-side full-text search over the Supreme Court judgments. Returns `row_idx` and `case_id` for each hit.
4. `pk_get_decision` - one judgment's full text, by `row_idx` (preferred, from search results) or by `case_id`.
5. `pk_verify_citations` - anti-hallucination check. Extracts Pakistani legal citations from any text (a drafted answer, a memo, a pleading) and verifies each against the corpus: statute sections ("section 302 of the Pakistan Penal Code", "section 10A of the Pakistan Study Centres Act, 1976"), act coordinates ("Act No. XLV of 1860"), Constitution articles ("Article 184(3) of the Constitution"), and Supreme Court registry citations ("Crl.A. 93/2013", "Criminal Appeal No. 93 of 2013"). A parenthetical description right after a citation is content-checked against the real provision (trigram match; a mismatch is a review signal, not a block). Run it on every drafted answer that cites Pakistani law BEFORE presenting it to the user.

## Verification semantics (pk_verify_citations)

- Result status `HALLUCINATION_DETECTED` (isError=true): at least one cited section or Article does not exist in the resolved act's text. The response includes a range hint of what DOES exist ("the act has sections 1-48"). NEVER report "verification complete" or "citations verified" for this result - report the citation errors explicitly.
- Result status `NO_CITATIONS_FOUND`: the text contains no checkable citations. This is NOT a verification success - it is the absence of anything to verify.
- The judgment corpus is a 1,414-judgment SUBSET: a case citation that is not found there is a `gap` (`out_of_corpus`), never a hallucination verdict. Reporter citations (PLD, SCMR) cannot be resolved against the registry-keyed corpus and are also reported as gaps.
- Everything the tool could NOT verify is listed in the structured `gaps` field (`out_of_corpus` / `unparseable_citation` / `upstream_unavailable` / `sections_not_checkable` / `subsection_not_checkable`) - relay gaps to the user instead of ignoring them.

## Hard constraints

- **Do not answer past the edge of this corpus** - when a search comes back empty, or the question touches material this connector does not carry, call `pk_coverage` and relay what it says is missing. Absence here is not absence in the law.
- **Static snapshots, not current law** - statutes as of 2025-01-30, judgments as of 2024-07-26. Amendments and newer judgments are ABSENT. When advising on current law, say so and point to pakistancode.gov.pk / scp.gov.pk for verification.
- **The judgment corpus is a subset** - 1,414 judgments is not the Court's full output; absence here proves nothing.
- **No native ELI** - Pakistan has not deployed ELI; `eli_uri` is a stable canonical URL, never invented (see `eli_note`).
- **OCR artifacts** - statute texts were extracted from PDFs; occasional broken characters and layout noise are verbatim from the source, not transmission errors.
- **Every response has `human_readable_citation` + `source_url`** - cite both to the user.
- **Audit log JSONL** - every tool call appends to `~/.matematic/audit/pk-eli-mcp.jsonl`.

## Error iteration

Tools return a structured error with a `[code]` prefix:
- `invalid_arg` - a parameter is missing, empty, or out of range.
- `not_found` - no statute matches that `law_id`, or no judgment matches that `row_idx`/`case_id`.
- `upstream_error` - a HuggingFace or download error (HTTP, timeout). Retry once before surfacing.

## Response style

- Cite statutes as `human_readable_citation` plus the pakistancode.gov.pk URL: "The Privatisation Commission Ordinance, 2000 - https://pakistancode.gov.pk/pdffiles/...".
- Cite judgments by their registry citation: "Supreme Court of Pakistan, Crl.A. 93/2013".
- NEVER invent a `law_id`, `case_id`, `eli_uri` or citation - take each from the tool output.
- Flag the snapshot dates whenever the user's question depends on the law being current.
"""


class ToolError(Exception):
    """Structured error for pk-eli MCP tools - visible to the LLM with a [code] prefix."""

    VALID_CODES = frozenset({"invalid_arg", "not_found", "upstream_error"})

    def __init__(self, code: str, message: str):
        if code not in self.VALID_CODES:
            raise ValueError(f"Unknown ToolError code: {code}. Valid: {sorted(self.VALID_CODES)}")
        self.code = code
        super().__init__(f"[{code}] {message}")


READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    idempotentHint=True,
    destructiveHint=False,
    openWorldHint=True,
)

mcp: FastMCP = FastMCP(name="pk-eli-mcp", instructions=INSTRUCTIONS)


def _audit() -> AuditLogger:
    return AuditLogger()


def _map_upstream(exc: Exception) -> Exception:
    if isinstance(exc, CorpusUnavailableError):
        return ToolError("upstream_error", str(exc))
    if isinstance(exc, (httpx.HTTPStatusError, httpx.TransportError, httpx.TimeoutException)):
        return ToolError("upstream_error", f"Upstream error: {type(exc).__name__}: {exc}")
    return exc


def _law_contract(law_id: str, title: str | None, year: int | None) -> dict[str, str | None]:
    from .citations import law_source_url

    citation = title if title else law_id
    if title and year and str(year) not in title:
        citation = f"{title} ({year})"
    return {
        "eli_uri": law_source_url(law_id),
        "human_readable_citation": citation,
        "source_url": law_source_url(law_id),
    }


def _case_contract(row_idx: int, case_id: str | None) -> dict[str, str | None]:
    row_url = (
        f"https://huggingface.co/datasets/{SC_DATASET}/viewer/default/train?row={row_idx}"
    )
    return {
        "eli_uri": row_url,
        "human_readable_citation": case_citation(case_id),
        "source_url": row_url,
    }


# ---------------------------------------------------------------------------
# pk_search_laws
# ---------------------------------------------------------------------------


@mcp.tool(annotations=READ_ONLY)
async def pk_search_laws(query: str, limit: int = 10) -> LawSearchResult:
    """Keyword search over Pakistan's 967 federal statutes (title-weighted ranking).

    Args:
        query: free-text keywords, e.g. ``"penal code"`` or ``"electronic crimes"``.
        limit: max results, 1-50 (default 10).

    Returns:
        ``LawSearchResult`` with ``items: list[LawSummary]``, each carrying ``law_id``
        and the citation contract. First call downloads the 47 MB corpus once.
    """
    audit = _audit()
    if not query.strip():
        raise ToolError("invalid_arg", "query must not be empty.")
    if not 1 <= limit <= 50:
        raise ToolError("invalid_arg", f"limit={limit} must be in 1..50.")
    input_hash = hash_input({"query": query, "limit": limit})

    with timer() as t:
        try:
            corpus = await get_corpus()
        except Exception as exc:
            audit.log(tool="pk_search_laws", input_hash=input_hash, output_count_or_size=0,
                      duration_ms=t.duration_ms if t.duration_ms else 0, status="error",
                      error=f"{type(exc).__name__}: {exc}")
            raise _map_upstream(exc) from exc
        hits = corpus.search(query, limit)

    items = [
        LawSummary(
            law_id=hit.law.law_id,
            title=hit.law.title,
            year=hit.law.year,
            snippet=hit.snippet,
            **_law_contract(hit.law.law_id, hit.law.title, hit.law.year),
        )
        for hit in hits
    ]
    result = LawSearchResult(query=query, total_matches=len(items), items=items)
    audit.log(tool="pk_search_laws", input_hash=input_hash, output_count_or_size=len(items),
              duration_ms=t.duration_ms, status="ok")
    return result


# ---------------------------------------------------------------------------
# pk_get_law
# ---------------------------------------------------------------------------


@mcp.tool(annotations=READ_ONLY)
async def pk_get_law(law_id: str) -> LawText:
    """Fetch the full text of one federal statute. Large statutes are truncated.

    Args:
        law_id: the corpus file name from ``pk_search_laws``, e.g.
            ``"administrator00532129aba2e10fe634ab8fbd94c50b.pdf"``.

    Returns:
        ``LawText`` with the citation contract, ``content`` and ``truncated``
        (True if cut at ~300,000 characters).
    """
    audit = _audit()
    if not law_id.strip():
        raise ToolError("invalid_arg", "law_id must not be empty.")
    input_hash = hash_input({"law_id": law_id})

    with timer() as t:
        try:
            corpus = await get_corpus()
        except Exception as exc:
            audit.log(tool="pk_get_law", input_hash=input_hash, output_count_or_size=0,
                      duration_ms=t.duration_ms if t.duration_ms else 0, status="error",
                      error=f"{type(exc).__name__}: {exc}")
            raise _map_upstream(exc) from exc
        law = corpus.get(law_id)

    if law is None:
        raise ToolError("not_found", f"No statute in the corpus for law_id={law_id!r}.")
    truncated = len(law.text) > _MAX_FULL_TEXT_CHARS
    content = law.text[:_MAX_FULL_TEXT_CHARS] if truncated else law.text

    result = LawText(
        law_id=law.law_id,
        title=law.title,
        year=law.year,
        content=content,
        byte_size=len(content.encode("utf-8")),
        truncated=truncated,
        **_law_contract(law.law_id, law.title, law.year),
    )
    audit.log(tool="pk_get_law", input_hash=input_hash,
              output_count_or_size=result.byte_size or 0,
              duration_ms=t.duration_ms, status="ok")
    return result


# ---------------------------------------------------------------------------
# pk_case_search
# ---------------------------------------------------------------------------


@mcp.tool(annotations=READ_ONLY)
async def pk_case_search(query: str, limit: int = 10) -> CaseSearchResult:
    """Full-text search over 1,414 Supreme Court of Pakistan judgments (server-side).

    Args:
        query: free-text keywords, e.g. ``"qatl-i-amd"`` or ``"fundamental rights"``.
        limit: max results, 1-20 (default 10).

    Returns:
        ``CaseSearchResult`` with ``items: list[CaseSummary]``, each carrying
        ``row_idx`` (use it with ``pk_get_decision``) and the citation contract.
    """
    audit = _audit()
    if not query.strip():
        raise ToolError("invalid_arg", "query must not be empty.")
    if not 1 <= limit <= 20:
        raise ToolError("invalid_arg", f"limit={limit} must be in 1..20.")
    input_hash = hash_input({"query": query, "limit": limit})

    with timer() as t:
        try:
            async with ScClient() as client:
                payload = await client.search(query, offset=0, length=limit)
        except Exception as exc:
            audit.log(tool="pk_case_search", input_hash=input_hash, output_count_or_size=0,
                      duration_ms=t.duration_ms if t.duration_ms else 0, status="error",
                      error=f"{type(exc).__name__}: {exc}")
            raise _map_upstream(exc) from exc

    items = []
    for wrapper in payload.get("rows", []):
        row = wrapper.get("row") or {}
        row_idx = wrapper.get("row_idx", -1)
        case_id = parse_case_id(row.get("citation_number"))
        items.append(
            CaseSummary(
                row_idx=row_idx,
                case_id=case_id,
                snippet=clean_line((row.get("text") or "")[:_SNIPPET_CHARS]),
                **_case_contract(row_idx, case_id),
            )
        )
    result = CaseSearchResult(
        query=query,
        total_matches=payload.get("num_rows_total", len(items)),
        items=items,
    )
    audit.log(tool="pk_case_search", input_hash=input_hash, output_count_or_size=len(items),
              duration_ms=t.duration_ms, status="ok")
    return result


# ---------------------------------------------------------------------------
# pk_get_decision
# ---------------------------------------------------------------------------


@mcp.tool(annotations=READ_ONLY)
async def pk_get_decision(row_idx: int | None = None, case_id: str | None = None) -> DecisionText:
    """Fetch one Supreme Court judgment's full text, by row index or registry citation.

    Args:
        row_idx: 0-based dataset row index from ``pk_case_search`` (preferred).
        case_id: registry file id, e.g. ``"Crl.A.93_2013.pdf"`` (used when
            ``row_idx`` is not given).

    Returns:
        ``DecisionText`` with the citation contract and the judgment ``content``.
    """
    audit = _audit()
    if row_idx is None and (case_id is None or not case_id.strip()):
        raise ToolError("invalid_arg", "Provide row_idx or case_id.")
    if row_idx is not None and row_idx < 0:
        raise ToolError("invalid_arg", f"row_idx={row_idx} must be >= 0.")
    input_hash = hash_input({"row_idx": row_idx, "case_id": case_id})

    with timer() as t:
        try:
            async with ScClient() as client:
                if row_idx is not None:
                    payload = await client.row(row_idx)
                else:
                    assert case_id is not None
                    payload = await client.filter_by_case_id(case_id.strip())
        except Exception as exc:
            audit.log(tool="pk_get_decision", input_hash=input_hash, output_count_or_size=0,
                      duration_ms=t.duration_ms if t.duration_ms else 0, status="error",
                      error=f"{type(exc).__name__}: {exc}")
            raise _map_upstream(exc) from exc

    rows = payload.get("rows", [])
    if not rows:
        wanted = f"row_idx={row_idx}" if row_idx is not None else f"case_id={case_id!r}"
        raise ToolError("not_found", f"No judgment in the corpus for {wanted}.")
    wrapper = rows[0]
    row = wrapper.get("row") or {}
    found_idx = wrapper.get("row_idx", row_idx if row_idx is not None else -1)
    found_case_id = parse_case_id(row.get("citation_number"))
    text = row.get("text") or ""
    truncated = len(text) > _MAX_FULL_TEXT_CHARS
    content = text[:_MAX_FULL_TEXT_CHARS] if truncated else text

    result = DecisionText(
        row_idx=found_idx,
        case_id=found_case_id,
        content=content,
        byte_size=len(content.encode("utf-8")),
        truncated=truncated,
        **_case_contract(found_idx, found_case_id),
    )
    audit.log(tool="pk_get_decision", input_hash=input_hash,
              output_count_or_size=result.byte_size or 0,
              duration_ms=t.duration_ms, status="ok")
    return result


# ---------------------------------------------------------------------------
# pk_verify_citations - anti-hallucination citation check
# (parse-verify-report loop adapted from chrisryugj/korean-law-mcp, MIT;
#  see THIRD_PARTY.md)
@mcp.tool(annotations=READ_ONLY)
async def pk_coverage() -> Coverage:
    """Declare what this connector covers, how it is sourced, and what it does NOT cover.

    Call this before telling a user that the law "does not contain" something, and whenever
    a search comes back empty: the absence may be a gap in this connector rather than in the
    law. Every gap carries a fallback saying where to look instead.

    Returns:
        ``Coverage`` with families, an as-of note, and a non-empty list of known gaps.
    """
    return build_coverage()


# ---------------------------------------------------------------------------

_STATUS_MARK = {
    "verified": "[OK]",
    "not_found": "[MISSING]",
    "content_mismatch": "[CHECK]",
    "unverified": "[CHECK]",
}

_verify_index: ActIndex | None = None
_verify_index_src: LawsCorpus | None = None


def _get_verify_index(corpus: LawsCorpus) -> ActIndex:
    """Build the act index once per corpus object; rebuild when the corpus changes."""
    global _verify_index, _verify_index_src
    if _verify_index is None or _verify_index_src is not corpus:
        _verify_index = ActIndex.build(corpus.laws)
        _verify_index_src = corpus
    return _verify_index


def _law_human(law: IndexedLaw) -> str:
    title = clean_line(law.title or law.law_id)
    if law.year and str(law.year) not in title:
        title = f"{title} ({law.year})"
    return title


def _resolve_statute(index: ActIndex, cite: ParsedCitation) -> IndexedLaw | None:
    law: IndexedLaw | None = None
    if cite.act_type and cite.act_number and cite.act_year:
        law = index.resolve_coord(cite.act_type, cite.act_number, cite.act_year)
    if law is None and cite.act_title:
        law = index.resolve_title(cite.act_title, cite.act_title_year)
    return law


def _check_section(
    index: ActIndex,
    cite: ParsedCitation,
    law: IndexedLaw,
    unit: str,
    scope: str,
    gaps: list[VerificationGap],
) -> CitationCheck:
    """Section/Article existence + optional claim and sub-section checks."""
    from .citations import law_source_url

    kind = "constitution" if cite.kind == "constitution" else "statute"
    human_act = _law_human(law)
    human = f"{human_act}, {unit} {cite.section}"
    source_url = law_source_url(law.law_id)
    sections = index.sections(law)

    if not sections.plausible:
        gaps.append(VerificationGap(
            gap_type="sections_not_checkable", citation=cite.raw,
            note=f"{human_act}: too few '{unit} N.' markers survived OCR to check "
                 f"{unit} numbers; {unit} {cite.section} was NOT checked.",
        ))
        return CitationCheck(
            raw=cite.raw, kind=kind, act_reference=human_act,
            section=cite.section, subsection=cite.subsection, status="unverified",
            detail=f"[CHECK] {human} - the act exists but its {unit}s are not "
                   f"machine-detectable; not verified.",
            human_readable_citation=human, source_url=source_url,
        )

    if not sections.has(cite.section or ""):
        hint = range_hint(sections, cite.section or "?", unit=unit, scope=scope)
        return CitationCheck(
            raw=cite.raw, kind=kind, act_reference=human_act,
            section=cite.section, subsection=cite.subsection, status="not_found",
            detail=f"[MISSING] {human} - {hint}",
            range_hint=hint,
            human_readable_citation=human, source_url=source_url,
        )

    status = "verified"
    notes: list[str] = []
    content_match: ContentMatch | None = None
    heading, body = section_excerpt(law.text, cite.section or "")

    if cite.claim:
        matched, method, score = match_claim(cite.claim, heading, body)
        content_match = ContentMatch(matched=matched, method=method, score=score)
        if not matched:
            status = "content_mismatch"
            actual = heading or clean_line(body[:80]) + "..."
            notes.append(
                f"claimed description {cite.claim!r} does not match the real provision "
                f"({actual!r}; {method} score {score}). Review signal, not a block."
            )

    if cite.subsection:
        nums, letters = detect_subsections(body)
        token = cite.subsection.lower()
        if token.isdigit():
            if not nums:
                gaps.append(VerificationGap(
                    gap_type="subsection_not_checkable", citation=cite.raw,
                    note=f"{human}: the {unit} exists but its sub-{unit} markers are not "
                         f"machine-detectable in the OCR text; ({cite.subsection}) was "
                         f"NOT checked.",
                ))
                notes.append(f"sub-{unit} ({cite.subsection}) not machine-checkable")
            elif int(token) not in nums:
                if status == "verified":
                    status = "unverified"
                notes.append(
                    f"sub-{unit} ({cite.subsection}) not found among detected markers "
                    f"(1)-({max(nums)}) (OCR text; review signal)"
                )
        elif letters and token not in letters:
            if status == "verified":
                status = "unverified"
            notes.append(f"clause ({cite.subsection}) not found among detected clauses "
                         f"(OCR text; review signal)")

    mark = _STATUS_MARK[status]
    heading_note = f" ({heading})" if heading else ""
    suffix = f" - {'; '.join(notes)}" if notes else ""
    return CitationCheck(
        raw=cite.raw, kind=kind, act_reference=human_act,
        section=cite.section, subsection=cite.subsection, status=status,
        detail=f"{mark} {human}{heading_note} - {unit} exists{suffix}",
        claim=cite.claim, content_match=content_match,
        human_readable_citation=human, source_url=source_url,
    )


def _verify_statute(
    index: ActIndex, cite: ParsedCitation, gaps: list[VerificationGap]
) -> CitationCheck:
    from .citations import law_source_url

    if not (cite.act_title or (cite.act_type and cite.act_number and cite.act_year)):
        gaps.append(VerificationGap(
            gap_type="unparseable_citation", citation=cite.raw,
            note="No act identifiable next to the section number; name the act "
                 "explicitly (e.g. 'section 5 of the Sale of Goods Act, 1930').",
        ))
        return CitationCheck(
            raw=cite.raw, kind="statute", section=cite.section, subsection=cite.subsection,
            status="unverified",
            detail=f"[CHECK] {cite.raw} - no act identifiable in context; not verified.",
        )

    law = _resolve_statute(index, cite)
    if law is None:
        named = cite.act_title or (
            f"{cite.act_type} No. {cite.act_number} of {cite.act_year}"
        )
        gaps.append(VerificationGap(
            gap_type="out_of_corpus", citation=cite.raw,
            note=f"{named!r} is not resolvable in the 967-statute snapshot (coordinate "
                 f"headers survive OCR in roughly half the corpus, and provincial or "
                 f"repealed law is absent). Existence is UNKNOWN, not disproven - "
                 f"verify at pakistancode.gov.pk.",
        ))
        return CitationCheck(
            raw=cite.raw, kind="statute", section=cite.section, subsection=cite.subsection,
            status="unverified",
            detail=f"[CHECK] {cite.raw} - act not resolvable in the corpus; not verified.",
        )

    if cite.section is None:
        human = _law_human(law)
        detail = f"[OK] {human} - act exists in the corpus"
        content_match: ContentMatch | None = None
        status = "verified"
        if cite.claim:
            heading = clean_line((law.title or "")[:120])
            matched, method, score = match_claim(cite.claim, heading, law.text[:2000])
            content_match = ContentMatch(matched=matched, method=method, score=score)
            if not matched:
                status = "content_mismatch"
                detail = (f"[CHECK] {human} - act exists, but the claimed description "
                          f"{cite.claim!r} does not match its opening text "
                          f"({method} score {score}). Review signal, not a block.")
        return CitationCheck(
            raw=cite.raw, kind="statute", act_reference=_law_human(law), status=status,
            detail=detail, claim=cite.claim, content_match=content_match,
            human_readable_citation=_law_human(law), source_url=law_source_url(law.law_id),
        )

    return _check_section(index, cite, law, unit="section", scope="this act", gaps=gaps)


def _verify_constitution(
    index: ActIndex, cite: ParsedCitation, gaps: list[VerificationGap]
) -> CitationCheck:
    law = index.constitution_law
    if law is None:
        gaps.append(VerificationGap(
            gap_type="out_of_corpus", citation=cite.raw,
            note="The Constitution text is not present in the loaded corpus; "
                 "Article numbers were NOT checked.",
        ))
        return CitationCheck(
            raw=cite.raw, kind="constitution", section=cite.section,
            subsection=cite.subsection, status="unverified",
            detail=f"[CHECK] {cite.raw} - Constitution not in the corpus; not verified.",
        )
    return _check_section(
        index, cite, law, unit="Article", scope="the Constitution", gaps=gaps,
    )


async def _verify_case(cite: ParsedCitation, gaps: list[VerificationGap]) -> CitationCheck:
    display = cite.case_display or cite.case_id or cite.raw
    try:
        async with ScClient() as client:
            payload = await client.filter_by_case_id(cite.case_id or "")
    except (httpx.HTTPStatusError, httpx.TransportError, httpx.TimeoutException) as exc:
        gaps.append(VerificationGap(
            gap_type="upstream_unavailable", citation=cite.raw,
            note=f"Judgment lookup failed ({type(exc).__name__}); retry later.",
        ))
        return CitationCheck(
            raw=cite.raw, kind="supreme_court_case", status="unverified",
            detail=f"[CHECK] {display} - judgment lookup unavailable; not verified.",
        )
    rows = payload.get("rows", [])
    if not rows:
        gaps.append(VerificationGap(
            gap_type="out_of_corpus", citation=cite.raw,
            note=f"{display}: not in the 1,414-judgment snapshot, which is a SUBSET of "
                 f"the Court's output - absence proves nothing. Verify at scp.gov.pk.",
        ))
        return CitationCheck(
            raw=cite.raw, kind="supreme_court_case", status="unverified",
            detail=f"[CHECK] {display} - not in the judgment corpus (a subset); "
                   f"existence UNKNOWN, not disproven.",
        )
    wrapper = rows[0]
    row = wrapper.get("row") or {}
    row_idx = wrapper.get("row_idx", -1)
    found_case_id = parse_case_id(row.get("citation_number"))
    contract = _case_contract(row_idx, found_case_id)
    return CitationCheck(
        raw=cite.raw, kind="supreme_court_case", status="verified",
        detail=f"[OK] {display} - judgment exists in the corpus "
               f"({contract['human_readable_citation'] or 'decision found'}).",
        human_readable_citation=contract["human_readable_citation"],
        source_url=contract["source_url"],
    )


def _verify_reporter(cite: ParsedCitation, gaps: list[VerificationGap]) -> CitationCheck:
    gaps.append(VerificationGap(
        gap_type="out_of_corpus", citation=cite.raw,
        note=f"{cite.reporter}: reporter citations (PLD/SCMR) cannot be resolved against "
             f"the registry-keyed judgment corpus. Verify in the reporter itself.",
    ))
    return CitationCheck(
        raw=cite.raw, kind="reporter_citation", status="unverified",
        detail=f"[CHECK] {cite.reporter} - reporter citation outside the corpus keying; "
               f"not verified here.",
    )


def _verification_report(result: CitationVerificationResult) -> str:
    lines = [
        f"[{result.status}] == Citation verification ==",
        f"Total {result.total} | OK {result.verified_count} | "
        f"MISSING {result.failed_count} | CHECK {result.warning_count}",
        "",
    ]
    lines += [c.detail for c in result.citations]
    if result.gaps:
        lines.append("")
        lines.append("Gaps (not covered by this verification):")
        lines += [f"- [{g.gap_type}] {g.note}" for g in result.gaps]
    if result.status == "HALLUCINATION_DETECTED":
        lines += [
            "",
            f"[HALLUCINATION_DETECTED] {result.failed_count} citation(s) do not exist in "
            f"the cited source. They are likely invented. Report the citation errors to "
            f"the user explicitly and correct the text.",
            "NEVER report 'verification complete' or 'citations verified' for this result.",
        ]
    if result.status == "NO_CITATIONS_FOUND":
        lines += [
            "",
            "[NO_CITATIONS_FOUND] This is NOT a verification success - there were no "
            "citations to verify. Supported patterns: 'section 302 of the Pakistan Penal "
            "Code', 'Act No. XLV of 1860', 'Article 184(3) of the Constitution', "
            "'Crl.A. 93/2013'. Re-run with text that contains citations.",
        ]
    return "\n".join(lines)


@mcp.tool(annotations=READ_ONLY, output_schema=CitationVerificationResult.model_json_schema())
async def pk_verify_citations(text: str, max_citations: int = 15) -> ToolResult:
    """Verify Pakistani legal citations found in a text against the corpus.

    Extracts statute sections ("section 302 of the Pakistan Penal Code",
    "section 10A of the Pakistan Study Centres Act, 1976"), act coordinates
    ("Act No. XLV of 1860"), Constitution articles ("Article 184(3) of the
    Constitution") and Supreme Court registry citations ("Crl.A. 93/2013"),
    then checks each against the local corpus. A parenthetical description
    right after a citation is content-checked with a trigram match (mismatch =
    review signal). Non-existent sections come back with a range hint of what
    DOES exist.

    Args:
        text: the text to verify (an LLM answer, a memo, a pleading).
        max_citations: cap on citations to verify (1..30, default 15).

    Returns:
        ``CitationVerificationResult`` (structured): per-citation checks plus a
        ``gaps`` list of everything the tool could NOT verify. When any cited
        section or Article does not exist the result is HALLUCINATION_DETECTED
        and the response carries isError=true.
    """
    audit = _audit()
    input_hash = hash_input({"text": text, "max_citations": max_citations})
    if not 1 <= max_citations <= 30:
        raise ToolError("invalid_arg", f"max_citations={max_citations} out of range 1..30.")

    with timer() as t:
        cites = parse_citations(text, max_citations=max_citations)
        checks: list[CitationCheck] = []
        gaps: list[VerificationGap] = []
        if cites:
            needs_corpus = any(c.kind in ("statute", "constitution") for c in cites)
            index: ActIndex | None = None
            if needs_corpus:
                try:
                    corpus = await get_corpus()
                    index = _get_verify_index(corpus)
                except (CorpusUnavailableError, httpx.HTTPError) as exc:
                    index = None
                    gaps.append(VerificationGap(
                        gap_type="upstream_unavailable",
                        note=f"Statute corpus unavailable ({type(exc).__name__}); "
                             f"statute citations were NOT checked.",
                    ))
            for cite in cites:
                if cite.kind == "statute":
                    if index is None:
                        checks.append(CitationCheck(
                            raw=cite.raw, kind="statute", section=cite.section,
                            status="unverified",
                            detail=f"[CHECK] {cite.raw} - corpus unavailable; not verified.",
                        ))
                    else:
                        checks.append(_verify_statute(index, cite, gaps))
                elif cite.kind == "constitution":
                    if index is None:
                        checks.append(CitationCheck(
                            raw=cite.raw, kind="constitution", section=cite.section,
                            status="unverified",
                            detail=f"[CHECK] {cite.raw} - corpus unavailable; not verified.",
                        ))
                    else:
                        checks.append(_verify_constitution(index, cite, gaps))
                elif cite.kind == "case":
                    checks.append(await _verify_case(cite, gaps))
                else:
                    checks.append(_verify_reporter(cite, gaps))

    verified = sum(1 for c in checks if c.status == "verified")
    failed = sum(1 for c in checks if c.status == "not_found")
    warnings = sum(1 for c in checks if c.status in ("content_mismatch", "unverified"))

    if not checks:
        status = "NO_CITATIONS_FOUND"
        summary = ("No checkable citations found in the input text. "
                   "This is NOT a verification success.")
    elif failed > 0:
        status = "HALLUCINATION_DETECTED"
        summary = (f"{failed} of {len(checks)} citation(s) do not exist in the cited "
                   f"source. NEVER report 'verification complete' for this result.")
    elif warnings > 0:
        status = "PARTIAL_VERIFIED"
        summary = (f"{verified} of {len(checks)} citation(s) verified; {warnings} need "
                   f"review (see details and gaps).")
    else:
        status = "VERIFIED"
        summary = f"All {verified} citation(s) exist in their cited sources."

    result = CitationVerificationResult(
        status=status, summary=summary,
        total=len(checks), verified_count=verified,
        failed_count=failed, warning_count=warnings,
        citations=checks, gaps=gaps,
    )
    # The tool call itself succeeded; a detected hallucination is a RESULT, not a
    # tool error. It is still recorded (greppable) via the error field.
    audit.log(
        tool="pk_verify_citations", input_hash=input_hash,
        output_count_or_size=len(checks), duration_ms=t.duration_ms,
        status="ok",
        error=None if failed == 0 else f"hallucination_detected: {failed} citation(s)",
    )
    return ToolResult(
        content=_verification_report(result),
        structured_content=result,
        is_error=(status == "HALLUCINATION_DETECTED"),
    )


def main() -> None:
    """Run the MCP server over stdio (default for Claude Code)."""
    mcp.run()


if __name__ == "__main__":
    main()
