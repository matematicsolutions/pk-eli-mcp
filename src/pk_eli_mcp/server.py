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
from mcp.types import ToolAnnotations

from .audit import AuditLogger, hash_input, timer
from .citations import case_citation, clean_line, parse_case_id
from .corpus import CorpusUnavailableError, get_corpus
from .models import (
    CaseSearchResult,
    CaseSummary,
    DecisionText,
    LawSearchResult,
    LawSummary,
    LawText,
)
from .sc_client import SC_DATASET, ScClient

_MAX_FULL_TEXT_CHARS = 300_000
_SNIPPET_CHARS = 400

INSTRUCTIONS = """\
This MCP server exposes Pakistani federal legislation (967 laws, full text) and Supreme Court of Pakistan judgments (1,414, full text). It is CORPUS-BASED, not a live government API: Pakistan publishes no machine-readable legal API, so both collections are static HuggingFace snapshots of official sources (statutes: pakistancode.gov.pk PDFs, snapshot 2025-01-30, ODC-BY 1.0; judgments: Supreme Court PDFs, snapshot 2024-07-26, MIT). Pakistan has no ELI scheme; every response carries a stable `eli_uri` (the canonical pakistancode.gov.pk PDF URL for statutes, the dataset row URI for judgments), a `human_readable_citation` and a `source_url`. See `eli_note` and `dataset_note` on every response.

## Call order

1. `pk_search_laws` - keyword search over the 967 federal statutes (title-weighted). Returns `law_id` for each hit. The first call provisions the 47 MB corpus once and caches it (a pre-built GitHub release asset verified by sha256, with the pinned HuggingFace origin as fallback); expect roughly a minute of first-call latency on a slow link, a second or two on warm starts.
2. `pk_get_law` - full statute text by `law_id`. Large statutes are truncated at roughly 300,000 characters.
3. `pk_case_search` - server-side full-text search over the Supreme Court judgments. Returns `row_idx` and `case_id` for each hit.
4. `pk_get_decision` - one judgment's full text, by `row_idx` (preferred, from search results) or by `case_id`.

## Hard constraints

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


def main() -> None:
    """Run the MCP server over stdio (default for Claude Code)."""
    mcp.run()


if __name__ == "__main__":
    main()
