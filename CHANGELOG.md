# Changelog

All notable changes to `pk-eli-mcp` are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/) and the project uses [SemVer](https://semver.org/).

## [0.3.0] - 2026-07-13

### Added
- **`pk_verify_citations` - anti-hallucination citation verification.** Extracts Pakistani
  legal citations from any text (a drafted answer, a memo, a pleading) and verifies each
  against the corpus: statute sections (`section 302 of the Pakistan Penal Code`,
  `section 10A of the Pakistan Study Centres Act, 1976`, `sections 6 and 7 of ...`,
  `sub-section (1) of section 6`), act coordinates (`Act No. XLV of 1860`, Roman or Arabic),
  Constitution articles (`Article 184(3) of the Constitution`), and Supreme Court registry
  citations (`Crl.A. 93/2013`, `Criminal Appeal No. 93 of 2013`). A missing section returns
  a range hint of what does exist. A parenthetical description after a citation is
  content-checked with a character-trigram match (mismatch = review signal, not a block).
  Hard semantics: any non-existent section or Article makes the result
  `HALLUCINATION_DETECTED` with `isError=true`; a text without citations returns
  `NO_CITATIONS_FOUND`, explicitly not a success. Everything unverifiable lands in a
  structured `gaps` field (`out_of_corpus` / `unparseable_citation` / `upstream_unavailable`
  / `sections_not_checkable` / `subsection_not_checkable`) instead of being hidden in prose.
  Because the judgment corpus is a 1,414-judgment subset and roughly half the statute PDFs
  carry no machine-readable coordinate header, an unresolvable citation is reported as a
  gap ("existence UNKNOWN, not disproven"), never as a hallucination verdict. Pattern
  adapted from chrisryugj/korean-law-mcp (MIT) - see `THIRD_PARTY.md`; second carrier of
  the pattern after it-eli-mcp v0.6.0 and the first with a fully local existence backend.
- `THIRD_PARTY.md` with the korean-law-mcp attribution.
- `tests/test_verify.py` (citation parser, trigram matcher, section map - offline) and
  `tests/test_verify_tool.py` (tool-level, fixture corpus, no network).
- This changelog.

### Changed
- `fastmcp` dependency floor raised to `>=3.4` (`ToolResult.is_error` is needed to deliver
  the hallucination verdict as a tool-level error without losing the structured result).
- The instructions drift test regex now tolerates nested parentheses in tool decorators
  (`output_schema=Model.model_json_schema()`), using DOTALL with a non-greedy group.

## [0.2.0] - 2026-07-12

### Added
- **Lazy corpus ladder.** First call provisions the 967-statute corpus automatically:
  env override / existing cache, then a gzipped release asset (11.5 MB) verified against
  its `.sha256` sidecar, then the HuggingFace dataset as fallback. `release.yml` builds
  and attaches the asset with a provenance sidecar on every tag.

## [0.1.0] - 2026-07-10

### Added
- Initial release: `pk_search_laws`, `pk_get_law`, `pk_case_search`, `pk_get_decision`
  over 967 Pakistani federal statutes and 1,414 Supreme Court judgments, with the fleet
  citation contract (`eli_uri`, `human_readable_citation`, `source_url`), read-only
  annotations, JSONL audit log, and an instructions drift test.
