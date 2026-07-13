"""Offline tests for the citation parser and content matcher (verify.py).

Every citation string below mirrors a shape found verbatim in the corpus
(headers like "ORDINANCE No. LII OF 2000", cross-references like
"the Civil Servants Act, 1973 (Act LXXI of 1973)", prose like
"sub-section (1) of section 6") or in the judgment registry
("Crl.A.93_2013.pdf").
"""

from __future__ import annotations

from pk_eli_mcp.verify import (
    CONTENT_WARN_THRESHOLD,
    ActIndex,
    detect_subsections,
    match_claim,
    normalize_text,
    parse_citations,
    range_hint,
    roman_to_int,
    section_map,
    trigram_jaccard,
    trigram_overlap,
)

# ---------------------------------------------------------------------------
# Parser - statute sections
# ---------------------------------------------------------------------------


def test_parse_section_with_short_title_after():
    cites = parse_citations("Murder is punishable under section 302 of the Pakistan Penal Code.")
    assert len(cites) == 1
    c = cites[0]
    assert c.kind == "statute"
    assert c.section == "302"
    assert c.act_title == "Pakistan Penal Code"


def test_parse_section_with_title_and_year():
    cites = parse_citations(
        "as provided in section 10A of the Pakistan Study Centres Act, 1976"
    )
    c = cites[0]
    assert c.section == "10A"
    assert c.act_title == "Pakistan Study Centres Act"
    assert c.act_title_year == 1976


def test_parse_title_with_trailing_coordinates():
    # Real cross-reference shape: "the Civil Servants Act, 1973 (Act LXXI of 1973)"
    cites = parse_citations(
        "governed by section 4 of the Civil Servants Act, 1973 (Act LXXI of 1973)."
    )
    c = cites[0]
    assert c.act_title == "Civil Servants Act"
    assert c.act_type == "act"
    assert c.act_number == 71
    assert c.act_year == 1973


def test_parse_section_with_coordinates_after():
    cites = parse_citations("see section 3 of Ordinance No. LII of 2000 on establishment")
    c = cites[0]
    assert c.section == "3"
    assert c.act_type == "ordinance"
    assert c.act_number == 52
    assert c.act_year == 2000


def test_parse_act_before_section_lookback():
    cites = parse_citations(
        "The Sale of Goods Act, 1930 defines the contract of sale in section 4."
    )
    c = cites[0]
    assert c.section == "4"
    assert c.act_title == "Sale of Goods Act"
    assert c.act_title_year == 1930


def test_parse_enumeration_sections():
    cites = parse_citations(
        "sections 6 and 7 of the Privatisation Commission Ordinance, 2000 govern the Board"
    )
    assert [c.section for c in cites] == ["6", "7"]
    assert all(c.act_title == "Privatisation Commission Ordinance" for c in cites)


def test_parse_subsection_prefix_form():
    # Corpus prose: "a Board of Governors referred to in sub-section (1) of section 6"
    cites = parse_citations(
        "referred to in sub-section (1) of section 6 of the Pakistan Study Centres Act, 1976"
    )
    c = cites[0]
    assert c.section == "6"
    assert c.subsection == "1"


def test_parse_subsection_suffix_form():
    cites = parse_citations("convicted under section 302(b) of the Pakistan Penal Code")
    c = cites[0]
    assert c.section == "302"
    assert c.subsection == "b"


def test_parse_no_act_in_context():
    cites = parse_citations("as stated in section 12 regarding transparency")
    assert len(cites) == 1
    assert cites[0].act_title is None
    assert cites[0].act_type is None


def test_parse_said_act_is_not_a_title():
    # "the said Act" must not resolve to a title consisting of the keyword alone.
    cites = parse_citations("in accordance with section 9 of the said Act")
    assert cites[0].act_title is None


def test_parse_code_of_criminal_procedure_keyword_first_title():
    cites = parse_citations("under section 154 of the Code of Criminal Procedure, 1898")
    c = cites[0]
    assert c.act_title == "Code of Criminal Procedure"
    assert c.act_title_year == 1898


# ---------------------------------------------------------------------------
# Parser - bare act coordinates
# ---------------------------------------------------------------------------


def test_parse_bare_coordinates_roman():
    cites = parse_citations("The Pakistan Penal Code was enacted as Act No. XLV of 1860.")
    coords = [c for c in cites if c.act_number is not None]
    assert coords[0].act_type == "act"
    assert coords[0].act_number == 45
    assert coords[0].act_year == 1860


def test_parse_bare_coordinates_arabic_order():
    cites = parse_citations("suspended in pursuance of Order No. 9 of 1999")
    c = cites[0]
    assert c.act_type == "order"
    assert c.act_number == 9
    assert c.act_year == 1999


def test_parse_po_is_order_not_case():
    cites = parse_citations("as amended by P.O. No. 1 of 1970")
    assert len(cites) == 1
    assert cites[0].kind == "statute"
    assert cites[0].act_type == "order"


# ---------------------------------------------------------------------------
# Parser - Constitution
# ---------------------------------------------------------------------------


def test_parse_constitution_article_with_clause():
    cites = parse_citations(
        "invoked Article 184(3) of the Constitution for enforcement of fundamental rights"
    )
    c = cites[0]
    assert c.kind == "constitution"
    assert c.section == "184"
    assert c.subsection == "3"


def test_parse_constitution_long_tail():
    cites = parse_citations(
        "Article 199 of the Constitution of the Islamic Republic of Pakistan empowers..."
    )
    assert cites[0].section == "199"


def test_parse_bare_article_is_ignored():
    # Without "of the Constitution", "Article 5" could be any instrument.
    assert parse_citations("Article 5 of the Universal Declaration prohibits torture.") == []


# ---------------------------------------------------------------------------
# Parser - Supreme Court cases and reporters
# ---------------------------------------------------------------------------


def test_parse_case_registry_slash():
    cites = parse_citations("as held in Crl.A. 93/2013 by the Supreme Court")
    c = cites[0]
    assert c.kind == "case"
    assert c.case_id == "Crl.A.93_2013"


def test_parse_case_registry_of_year():
    cites = parse_citations("see Const.P. No. 15 of 2012 on judicial independence")
    assert cites[0].case_id == "Const.P.15_2012"


def test_parse_case_longhand():
    cites = parse_citations("In Criminal Appeal No. 93 of 2013 the Court held...")
    c = cites[0]
    assert c.kind == "case"
    assert c.case_id == "Crl.A.93_2013"


def test_parse_reporter_citations_disclosed():
    cites = parse_citations("reported as PLD 2019 SC 1 and 2019 SCMR 1421")
    kinds = [c.kind for c in cites]
    assert kinds == ["reporter", "reporter"]
    assert cites[0].reporter == "PLD 2019 SC 1"


def test_parse_claim_parenthetical():
    cites = parse_citations(
        "section 302 of the Pakistan Penal Code (punishment of qatl-i-amd) applies"
    )
    assert cites[0].claim == "punishment of qatl-i-amd"


def test_parse_claim_rejects_amendment_note():
    cites = parse_citations(
        "section 3 of the Islamabad High Court Act, 2010 (as amended in 2020) provides..."
    )
    assert cites[0].claim is None


def test_parse_dedupe_and_cap():
    text = "section 302 of the Pakistan Penal Code and again section 302 of the Pakistan Penal Code"
    assert len(parse_citations(text)) == 1
    many = " ".join(f"Act No. {n} of 1950;" for n in range(1, 40))
    assert len(parse_citations(many, max_citations=5)) == 5


def test_parse_no_citations():
    assert parse_citations("This text contains no legal citation at all.") == []


# ---------------------------------------------------------------------------
# Roman numerals
# ---------------------------------------------------------------------------


def test_roman_to_int():
    assert roman_to_int("XLV") == 45
    assert roman_to_int("LII") == 52
    assert roman_to_int("LXXI") == 71
    assert roman_to_int("XXVII") == 27
    assert roman_to_int("Q") is None


# ---------------------------------------------------------------------------
# Trigram content matcher
# ---------------------------------------------------------------------------


def test_normalize_strips_punct():
    assert normalize_text("Qatl-i-amd,  punishment!") == "qatl i amd punishment"


def test_trigram_jaccard_identical():
    assert trigram_jaccard("punishment of qatl-i-amd", "punishment of qatl-i-amd") == 1.0


def test_trigram_jaccard_unrelated_below_threshold():
    score = trigram_jaccard("punishment of qatl-i-amd", "registration of trade unions")
    assert score < CONTENT_WARN_THRESHOLD


def test_trigram_overlap_claim_in_long_body():
    body = ("Whoever commits qatl-e-amd shall, subject to the provisions of this "
            "Chapter be punished with death as qisas.") * 3
    assert trigram_overlap("commits qatl-e-amd shall be punished", body) > 0.8


def test_match_claim_exact_layer():
    matched, method, score = match_claim(
        "punishment of qatl-i-amd",
        "Punishment of qatl-i-amd",
        "Whoever commits qatl-e-amd shall be punished...",
    )
    assert matched and method == "exact" and score == 1.0


def test_match_claim_mismatch():
    matched, _, score = match_claim(
        "registration of trade unions",
        "Punishment of qatl-i-amd",
        "Whoever commits qatl-e-amd shall, subject to the provisions of this Chapter, "
        "be punished with death as qisas.",
    )
    assert not matched
    assert score < CONTENT_WARN_THRESHOLD


def test_match_claim_survives_ocr_letter_spacing():
    # Compacted trigrams make "S h o r t t i t l e" comparable with "Short title".
    matched, _, _ = match_claim(
        "short title and extent", "S h o r t t i t l e a n d e x t e n t", ""
    )
    assert matched


# ---------------------------------------------------------------------------
# Section map, range hint, sub-sections
# ---------------------------------------------------------------------------

_FAKE_ACT = "\n".join(
    [f"{n}. Provision heading number {n}. Some body text follows here." for n in range(1, 21)]
    + ["10A. Inserted provision heading.  Text.",
       "1971. This is a year caught at line start, not a section."]
)


def test_section_map_detects_labels_and_prunes_outliers():
    sections = section_map(_FAKE_ACT)
    assert sections.has("5")
    assert sections.has("10A")
    assert not sections.has("999")
    assert 1971 not in sections.numbers  # gap > 50 pruned as OCR noise
    assert sections.plausible


def test_section_map_implausible_when_sparse():
    sections = section_map("7. Lonely provision. Nothing else numbered.")
    assert not sections.plausible


def test_range_hint_format():
    sections = section_map(_FAKE_ACT)
    hint = range_hint(sections, "999")
    assert "section 999 does not exist" in hint
    assert "section 1-20" in hint


def test_range_hint_no_sections():
    sections = section_map("no numbered provisions at all")
    assert "machine-detectable" in range_hint(sections, "5")


def test_detect_subsections():
    chunk = "(1) First rule. (2) Second rule. (a) a clause; (b) another clause."
    nums, letters = detect_subsections(chunk)
    assert nums == [1, 2]
    assert letters == ["a", "b"]


def test_detect_subsections_not_plausible():
    nums, _ = detect_subsections("only a stray (4) marker here")
    assert nums == []


# ---------------------------------------------------------------------------
# ActIndex
# ---------------------------------------------------------------------------


class _LawStub:
    def __init__(self, law_id, title, year, text):
        self.law_id, self.title, self.year, self.text = law_id, title, year, text


def test_act_index_coordinates_and_titles():
    laws = [
        _LawStub("a.pdf", "THE PRIVATISATION COMMISSION ORDINANCE, 2000",
                 2000, "THE PRIVATISATION COMMISSION ORDINANCE, 2000\n"
                       "ORDINANCE  No. LII OF 2000\n1. Short title."),
        _LawStub("b.pdf", "THE PAKISTAN STUDY CENTRES, ACT 1976",
                 1976, "THE PAKISTAN STUDY CENTRES, ACT 1976\n1ACT  No. XXVII OF 1976\n"
                       "1. Short title."),
    ]
    index = ActIndex.build(laws)
    assert index.resolve_coord("ordinance", 52, 2000).law_id == "a.pdf"
    # OCR footnote marker glued to the type word ("1ACT") must still index.
    assert index.resolve_coord("act", 27, 1976).law_id == "b.pdf"
    # Compacted title matching tolerates the comma placement noise.
    assert index.resolve_title("Pakistan Study Centres Act", 1976).law_id == "b.pdf"
    assert index.resolve_title("Privatisation Commission Ordinance", None).law_id == "a.pdf"
    assert index.resolve_title("Nonexistent Councils Act", None) is None


def test_act_index_finds_constitution():
    laws = [_LawStub("c.pdf", "THE CONSTITUTION",
                     1973, "THE CONSTITUTION OF THE ISLAMIC REPUBLIC OF PAKISTAN\n"
                           "1. The State.")]
    index = ActIndex.build(laws)
    assert index.constitution_law is not None
    assert index.constitution_law.law_id == "c.pdf"
