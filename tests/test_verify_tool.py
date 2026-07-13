"""Offline tests for the pk_verify_citations tool - fixture corpus, no network.

The corpus seam (``server.get_corpus``) is monkeypatched with schema-compatible
law stubs (the same pattern as it-eli-mcp's test_verify_tool: the real corpus is
too large to download in CI). The judgment lookup seam (``server.ScClient``) is
replaced by a fake that knows exactly one registry id.
"""

from __future__ import annotations

import pytest

import pk_eli_mcp.server as server

# fastmcp returns a FunctionTool (call via .fn) or the bare function, by version.
_VERIFY = getattr(server.pk_verify_citations, "fn", server.pk_verify_citations)


class _LawStub:
    def __init__(self, law_id: str, title: str, year: int, text: str) -> None:
        self.law_id, self.title, self.year, self.text = law_id, title, year, text


def _sections(n: int, special: dict[int, str] | None = None) -> str:
    special = special or {}
    lines = []
    for i in range(1, n + 1):
        heading = special.get(i, f"Provision heading number {i}")
        lines.append(f"{i}. {heading}.-- (1) First rule. (2) Second rule. (3) Third rule.")
    return "\n".join(lines)


SGA = _LawStub(
    "sale-of-goods-act-1930.pdf",
    "THE SALE OF GOODS ACT, 1930",
    1930,
    "THE SALE OF GOODS ACT, 1930\nACT No. III OF 1930\n"
    + _sections(48, {4: "Sale and agreement to sell"}),
)

CONSTITUTION = _LawStub(
    "constitution.pdf",
    "THE CONSTITUTION OF THE ISLAMIC REPUBLIC OF PAKISTAN",
    1973,
    "THE CONSTITUTION OF THE ISLAMIC REPUBLIC OF PAKISTAN\n"
    + _sections(200, {184: "Original jurisdiction of Supreme Court"}),
)


class _CorpusStub:
    def __init__(self) -> None:
        self.laws = [SGA, CONSTITUTION]


class _FakeScClient:
    """Knows exactly one judgment: Crl.A.93_2013."""

    async def __aenter__(self) -> _FakeScClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def filter_by_case_id(self, case_id: str) -> dict:
        if case_id == "Crl.A.93_2013":
            return {"rows": [{
                "row_idx": 7,
                "row": {"citation_number": "{'id': 'Crl.A.93_2013.pdf', 'url': ''}"},
            }]}
        return {"rows": []}


@pytest.fixture(autouse=True)
def _patched(monkeypatch):
    corpus = _CorpusStub()

    async def fake_get_corpus():
        return corpus

    monkeypatch.setattr(server, "get_corpus", fake_get_corpus)
    monkeypatch.setattr(server, "ScClient", _FakeScClient)
    monkeypatch.setattr(server, "_verify_index", None)
    monkeypatch.setattr(server, "_verify_index_src", None)
    yield


@pytest.mark.asyncio
async def test_all_verified():
    res = await _VERIFY(
        "Under section 4 of the Sale of Goods Act, 1930 and Article 184(3) of the "
        "Constitution the petition is maintainable."
    )
    sc = res.structured_content
    assert res.is_error is False
    assert sc["status"] == "VERIFIED"
    assert sc["total"] == 2
    assert sc["verified_count"] == 2
    assert sc["gaps"] == []
    assert all(c["status"] == "verified" for c in sc["citations"])


@pytest.mark.asyncio
async def test_hallucination_detected_with_range_hint():
    res = await _VERIFY("As held, section 999 of the Sale of Goods Act, 1930 applies.")
    sc = res.structured_content
    assert res.is_error is True
    assert sc["status"] == "HALLUCINATION_DETECTED"
    check = sc["citations"][0]
    assert check["status"] == "not_found"
    assert "section 999 does not exist" in check["range_hint"]
    assert "section 1-48" in check["range_hint"]
    text = res.content[0].text
    assert "[HALLUCINATION_DETECTED]" in text
    assert "NEVER report 'verification complete'" in text


@pytest.mark.asyncio
async def test_no_citations_is_not_success():
    res = await _VERIFY("A paragraph without any legal citation at all.")
    sc = res.structured_content
    assert res.is_error is False
    assert sc["status"] == "NO_CITATIONS_FOUND"
    assert "NOT a verification success" in res.content[0].text


@pytest.mark.asyncio
async def test_content_match_ok_and_mismatch():
    ok = await _VERIFY(
        "See section 4 of the Sale of Goods Act, 1930 (sale and agreement to sell)."
    )
    assert ok.structured_content["status"] == "VERIFIED"
    cm = ok.structured_content["citations"][0]["content_match"]
    assert cm["matched"] is True

    bad = await _VERIFY(
        "See section 4 of the Sale of Goods Act, 1930 (registration of trade unions)."
    )
    sc = bad.structured_content
    assert bad.is_error is False  # review signal, not a block
    assert sc["status"] == "PARTIAL_VERIFIED"
    assert sc["citations"][0]["status"] == "content_mismatch"


@pytest.mark.asyncio
async def test_unparseable_citation_goes_to_gaps():
    res = await _VERIFY("As stated in section 12, transparency is mandatory.")
    sc = res.structured_content
    assert sc["status"] == "PARTIAL_VERIFIED"
    assert sc["citations"][0]["status"] == "unverified"
    assert any(g["gap_type"] == "unparseable_citation" for g in sc["gaps"])


@pytest.mark.asyncio
async def test_unresolvable_act_is_gap_not_hallucination():
    res = await _VERIFY("Under section 2 of the Balochistan Finance Act, 2019 a levy applies.")
    sc = res.structured_content
    assert res.is_error is False
    assert sc["status"] == "PARTIAL_VERIFIED"
    assert sc["citations"][0]["status"] == "unverified"
    gap = next(g for g in sc["gaps"] if g["gap_type"] == "out_of_corpus")
    assert "UNKNOWN, not disproven" in gap["note"]


@pytest.mark.asyncio
async def test_case_verified_and_out_of_corpus():
    ok = await _VERIFY("As held in Crl.A. 93/2013 the conviction was set aside.")
    sc = ok.structured_content
    assert sc["status"] == "VERIFIED"
    assert sc["citations"][0]["kind"] == "supreme_court_case"

    missing = await _VERIFY("As held in Crl.A. 555/1999 the conviction was set aside.")
    sc = missing.structured_content
    assert missing.is_error is False  # subset corpus: absence proves nothing
    assert sc["status"] == "PARTIAL_VERIFIED"
    assert any(g["gap_type"] == "out_of_corpus" for g in sc["gaps"])


@pytest.mark.asyncio
async def test_reporter_citation_is_disclosed_gap():
    res = await _VERIFY("The principle was reported as PLD 2019 SC 1.")
    sc = res.structured_content
    assert sc["citations"][0]["kind"] == "reporter_citation"
    assert sc["citations"][0]["status"] == "unverified"
    assert any(g["gap_type"] == "out_of_corpus" for g in sc["gaps"])


@pytest.mark.asyncio
async def test_max_citations_out_of_range():
    with pytest.raises(server.ToolError):
        await _VERIFY("section 302 of the Pakistan Penal Code", max_citations=0)
