# verify_citations - fleet rollout plan

Status: recipe, written 2026-07-13. Two carriers shipped: it-eli-mcp v0.6.0
(live existence backend, Normattiva) and pk-eli-mcp v0.3.0 (local corpus
backend). This document is the working plan for porting the tool to the rest
of the MateMatic fleet. Treat it as the session brief for each rollout session:
pick the next connector from the ROI table, follow the per-connector checklist,
ship one tagged release.

## What the tool is

`<prefix>_verify_citations(text, max_citations)` extracts the jurisdiction's
legal citations from free text and checks each one against the connector's own
source of truth. Hard semantics, identical in every port:

- a cited provision that does not exist => result `HALLUCINATION_DETECTED`,
  `isError=true`, plus a range hint of what does exist;
- `NO_CITATIONS_FOUND` is explicitly not a success;
- a parenthetical claim after a citation is content-checked (trigram match,
  mismatch = review signal);
- everything unverifiable lands in a structured `gaps` list (`out_of_corpus`,
  `unparseable_citation`, `upstream_unavailable`, plus per-connector types),
  never in silence.

Pattern origin: chrisryugj/korean-law-mcp (MIT), attribution in THIRD_PARTY.md
of each carrier.

## The four components (what is shared, what is swapped)

| # | Component | Shared or per-connector | Notes |
|---|---|---|---|
| 1 | Citation grammar (regexes + parser) | per-connector | The only genuinely national part. Validate every pattern against real identifiers from the connector's own data, not from memory (pk lesson: cross-references drop the "No." token; sentence-initial "The ... Act" is as common as mid-sentence "the ... Act"). |
| 2 | Reference resolution (citation -> document) | per-connector | Corpus connectors: coordinate/title index built once per corpus (pk `ActIndex`). Live connectors: the connector's existing search/get tool becomes the resolver (it-eli reuses its Normattiva fetch). |
| 3 | Existence backend (does the provision exist) | per-connector | Corpus: section-map over the local text. Live: fetch the act and look for the article. API-only registries: a GET that 404s is itself the check. |
| 4 | Verdict semantics, models, report, gaps, isError | shared | Copy from pk (`models.py` verification block, `_verification_report`, the tool body) or it-eli. Do not re-derive; the wording of `[HALLUCINATION_DETECTED]` / `[NO_CITATIONS_FOUND]` and the "NEVER report verification complete" instruction are part of the contract. |

Hard prerequisites per repo:

- `fastmcp>=3.4` in pyproject (PyPI `fastmcp` below 3.4 lacks
  `ToolResult.is_error`; the floor also dodges the fastmcp-slim stub problem).
- Drift-test regex that survives nested parentheses in decorators:
  `@mcp\.tool\(.*?\)\s*\nasync def (\w+)` with `re.DOTALL`.
- `THIRD_PARTY.md` with the korean-law-mcp attribution.
- Offline tool-level tests with a schema-compatible fixture corpus
  (pk `tests/test_verify_tool.py` is the template; never depend on the
  network in CI).
- Instructions block: add the tool line plus the "Verification semantics"
  section (copy from pk server.py, adjust names).

## Fleet inventory and per-connector work

Legend: backend = what component 3 uses. Effort: S = grammar close to an
existing carrier, mostly transliteration (about half a day); M = new grammar
or new backend seam (about a day); L = multiple citation systems or a
TypeScript port of the shared semantics (1-2 days).

### Wave 1 - largest corpora, highest hallucination stakes

| Connector | Lang | Backend for verify | What to swap | Effort |
|---|---|---|---|---|
| us-eli-mcp | Python | live (5 US sources already wired) | Grammar: `17 U.S.C. § 106`, `Pub. L. 94-553`, CFR cites, federal reporters (`410 U.S. 113`). Resolution via the existing lookup tools; section existence from the fetched act text. | L (two citation systems: code + reporter) |
| sejm-eli-mcp (PL) | Python | live (api.sejm.gov.pl/eli) | Grammar: `art. 415 k.c.`, `art. 23 ust. 1 pkt 2 ustawy z dnia [DD.MM.YYYY] o [...]`, Dz.U. coordinates. ELI GET that resolves = existence proof. Watch the short-abbreviation word-boundary trap (`\bKP\b`, not `.includes`). | M |
| eur/mcp-eu-sparql | Node | live (Cellar SPARQL) | Grammar: CELEX (`32016R0679`), ELI URIs, `Article 6(1)(a) GDPR` style. Existence: SPARQL ASK on the CELEX id; article-level via the act's XHTML. First TypeScript port of component 4. | L |
| br-eli-mcp | Python | live (8 open-data APIs) | Grammar: `art. 5º, LXXVIII, CF/88`, `Lei nº 8.078/1990`, `Súmula 331 TST`. Resolution via LexML/normas API; ordinal and superscript characters need the same NFKD normalization pk uses. | M |

### Wave 2 - the remaining single-country connectors (grammar clones)

All Python, all live ELI/national-API backends. The work per connector is
component 1 (grammar) plus pointing resolution at the connector's existing
get-act tool; the first connector of each grammar family is an M, every
sibling after it is an S. Order inside the wave by adoption data from the
PATRON dashboard (installs per connector), not alphabetically.

| Grammar family | Connectors | Grammar seed | Effort |
|---|---|---|---|
| German-style | de-eli, at-eli, ch-eli | `§ 823 Abs. 1 BGB`, `Art. 41 OR` | M, then S each |
| French-style | fr-eli, be-eli, lu-eli | `article 1240 du Code civil`, `L. 121-1` | M, then S each |
| Spanish-style | es-eli, cl-eli, co-eli | `articulo 1902 del Codigo Civil`, `Ley 29/1994` | M, then S each |
| English-style (closest to pk, cheapest) | gb-eli, ie-eli, au-eli, ca-eli, my-eli, sg-eli | `section 6 of the Human Rights Act 1998` | S each |
| One-off grammars | nl, dk, fi, se, cz, sk, hr, hu, lt, mt, ro, tr, jp, il | national grammar per connector, harvested from that connector's own data | M each |

### Wave 3 - Polish case-law and registry connectors (Node + Python)

| Connector | Lang | Note |
|---|---|---|
| mcp-saos | Node | Already carries `saos_cite_check` (the "is this judgment alive" citator, v1.2.0). Add existence verification of signatures cited in a draft: same grammar, different question. Reuse the citator's signature regexes; remember SAOS phrase search stems - confirm matches literally. | 
| mcp-nsa | Node | Signature grammar `II FSK 1234/20`; existence = search hit on the signature. |
| kio-orzeczenia-mcp / uodo-orzeczenia-mcp | Python | Signature/decision-number grammar; existence = registry search. S each once one Node port exists to copy wording from. |
| mcp-eu-compliance | Node | Blocked on the schema remap (separate session); fold verify in afterwards. |

## ROI order (the queue for rollout sessions)

1. **us-eli-mcp** - 9.7M documents, English grammar, flagship for the US
   audience; hallucinated US citations are the best-documented failure mode in
   the wild (Damien Charlotin's AI Hallucination Cases tracker), which makes
   this the port with the clearest demand signal.
2. **sejm-eli-mcp** - home market, PATRON integration, pairs with the existing
   saos citator to cover both statutes and case law in Polish.
3. **mcp-eu-sparql** - 4.07M documents, and it produces the TypeScript port of
   the shared semantics that every later Node connector copies.
4. **br-eli-mcp** - 8.5M documents, second-largest corpus, active BR audience.
5. **de-eli-mcp then fr-eli-mcp then es-eli-mcp** - each one is the M-effort
   opener of a grammar family; its siblings drop to S (wave 2 table).
6. **mcp-saos + mcp-nsa** - once the TS semantics exist (step 3), these are
   short sessions.
7. Remaining wave-2 connectors in dashboard-adoption order; wave 3 registries
   last.

Rationale: order = (documents served x hallucination stakes) / effort, with a
bonus for family openers (first TS port, first grammar of a family).

## Per-session checklist (copy into each rollout session)

1. Read this file and the pk carrier (`src/pk_eli_mcp/verify.py`,
   `server.py` verify block, both test files).
2. Harvest 10-20 REAL citations from the connector's own data before writing
   any regex; keep them as test fixtures.
3. Port component 4 verbatim; write components 1-3.
4. `fastmcp>=3.4` (or TS equivalent), drift-test fix, THIRD_PARTY.md.
5. Offline tests green, ruff/eslint green, leak scan.
6. CHANGELOG entry, README section (copy pk's structure).
7. Bump version in lockstep (pyproject + server.json + `__init__`), tag,
   verify the published package resolves (`uv run --with <pkg>==<version>`).
8. Flip the Boutique cards in all three languages (PL/EN/PT) and recheck
   `numberOfItems` in JSON-LD.
