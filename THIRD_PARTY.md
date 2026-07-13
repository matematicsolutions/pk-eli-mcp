# Third-party attributions

## chrisryugj/korean-law-mcp (MIT)

The `pk_verify_citations` tool adapts the anti-hallucination verification pattern of
[korean-law-mcp](https://github.com/chrisryugj/korean-law-mcp) by chrisryugj, published
under the MIT License:

- the parse-verify-report loop: extract citations from free text, resolve the act
  reference from the surrounding context, verify existence against the source;
- the range hint on a missing provision ("section 999 does not exist; the act has
  sections 1-48");
- the optional content check of a claimed description against the real provision text;
- the hard response semantics: `isError=true` plus a `[HALLUCINATION_DETECTED]` header
  when a cited provision does not exist, and an explicit `[NO_CITATIONS_FOUND]` marker
  (not a success) when the input contains nothing to verify.

No source code was copied. The implementation in `src/pk_eli_mcp/verify.py` and
`src/pk_eli_mcp/server.py` was written from scratch in Python for Pakistani citation
grammar (Act/Ordinance/Order coordinates, short titles, Constitution articles, Supreme
Court registry citations) and for this repository's citation contract. The content
matcher uses character trigrams instead of the original's character bigrams: bigrams
fit Korean, an agglutinative script; trigrams discriminate better for English legal
text. The same adaptation shipped first in it-eli-mcp v0.6.0; this connector is the
second carrier of the pattern and the first with a fully local (corpus-lookup)
existence backend.

MIT License text of the original project:
https://github.com/chrisryugj/korean-law-mcp/blob/main/LICENSE
