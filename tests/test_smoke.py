"""Smoke tests - require internet; the laws tests download the 47 MB corpus once.

Run manually:

    pytest tests/test_smoke.py -v
"""

from __future__ import annotations

import pytest

from pk_eli_mcp.server import pk_case_search, pk_get_decision, pk_get_law, pk_search_laws


@pytest.mark.asyncio
async def test_smoke_search_laws() -> None:
    result = await pk_search_laws("penal code", limit=5)
    assert result.items, "expected at least one statute for 'penal code'"
    for item in result.items:
        assert item.law_id
        assert item.eli_uri and "pakistancode.gov.pk/pdffiles/" in item.eli_uri
        assert item.human_readable_citation


@pytest.mark.asyncio
async def test_smoke_get_law() -> None:
    found = await pk_search_laws("privatisation commission ordinance", limit=1)
    assert found.items
    law = await pk_get_law(found.items[0].law_id)
    assert law.content and len(law.content) > 1000
    assert law.human_readable_citation
    assert law.byte_size and law.byte_size > 0


@pytest.mark.asyncio
async def test_smoke_case_search() -> None:
    result = await pk_case_search("murder", limit=3)
    assert result.items, "expected at least one judgment for 'murder'"
    for item in result.items:
        assert item.row_idx >= 0
        assert item.eli_uri and "huggingface.co/datasets" in item.eli_uri


@pytest.mark.asyncio
async def test_smoke_get_decision_by_row_idx() -> None:
    found = await pk_case_search("appeal", limit=1)
    assert found.items
    decision = await pk_get_decision(row_idx=found.items[0].row_idx)
    assert decision.content and len(decision.content) > 500
    assert decision.human_readable_citation
