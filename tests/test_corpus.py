"""Offline tests - title/year extraction, corpus search, and citation parsing.

Run against committed fixtures (public legal data); no network needed.
"""

from __future__ import annotations

import json
from pathlib import Path

from pk_eli_mcp.citations import case_citation, extract_title, extract_year, parse_case_id
from pk_eli_mcp.corpus import LawsCorpus

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture_corpus() -> LawsCorpus:
    records = json.loads((FIXTURES / "laws_sample.json").read_text(encoding="utf-8"))
    return LawsCorpus.from_records(records)


def test_extract_title_and_year():
    corpus = _fixture_corpus()
    titles = {law.title for law in corpus.laws}
    assert any("PRIVATISATION COMMISSION ORDINANCE" in (t or "") for t in titles), titles
    assert any("ISLAMABAD HIGH COURT ACT" in (t or "") for t in titles), titles
    years = {law.year for law in corpus.laws}
    assert 2000 in years and 2010 in years, years


def test_search_ranks_title_match_first():
    corpus = _fixture_corpus()
    hits = corpus.search("privatisation commission", limit=3)
    assert hits, "expected at least one hit"
    assert "PRIVATISATION" in (hits[0].law.title or "")
    assert hits[0].snippet


def test_search_empty_query_returns_nothing():
    corpus = _fixture_corpus()
    assert corpus.search("", limit=5) == []
    assert corpus.search("!!!", limit=5) == []


def test_get_tolerates_pdf_suffix():
    corpus = _fixture_corpus()
    law = corpus.laws[0]
    assert corpus.get(law.law_id) is law
    assert corpus.get(law.law_id.removesuffix(".pdf")) is law


def test_parse_case_id_from_fixture_row():
    payload = json.loads((FIXTURES / "sc_row.json").read_text(encoding="utf-8"))
    row = payload["rows"][0]["row"]
    case_id = parse_case_id(row["citation_number"])
    assert case_id and case_id.endswith(".pdf")
    citation = case_citation(case_id)
    assert citation and citation.startswith("Supreme Court of Pakistan, ")
    assert "/" in citation  # year separator, e.g. Crl.A.93/2013


def test_parse_case_id_plain_string():
    assert parse_case_id("C.A.1_2020.pdf") == "C.A.1_2020.pdf"
    assert parse_case_id("") is None
    assert parse_case_id(None) is None
    assert parse_case_id("{'no_id': 'x'}") is None


def test_extract_title_falls_back_without_keyword():
    text = " \nPage 1 of 65 \n \nTHE QANUN-E-SHAHADAT , 1984  \n \nCONTENTS  \n"
    title = extract_title(text)
    assert title and "QANUN" in title
    assert extract_year(title, text) == 1984
