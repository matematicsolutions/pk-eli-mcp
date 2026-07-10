# DISCOVERY - how the Pakistan sources were chosen (2026-07-10)

## What was probed

| Candidate | Result |
|---|---|
| pakistancode.gov.pk (Ministry of Law and Justice) | Official portal, no API. HTML with obfuscated URLs; unreliable from datacenter IPs (Legal Data Hunter marks it `site_unreachable`). Scraping is off-principle for this fleet - rejected as a direct source, kept as the canonical origin for citations. |
| pakistan.gov.pk/open-data | No legal datasets exposed. |
| scp.gov.pk (Supreme Court) | Judgments as PDFs behind a web UI, no API. |
| Ansvar-Systems/Pakistani-law-mcp | Indexed by search engines but the repository no longer exists (the org keeps ~40 EU/UK law MCPs, none for Pakistan). No living prior art. |
| AyeshaJadoon/Pakistan_Laws_Dataset (HuggingFace) | 967 federal laws, full text, collected from pakistancode.gov.pk PDFs. ODC-BY 1.0. Static (2025-01-30). Verified live: the 47 MB `pdf_data.json` downloads and parses. **Chosen.** |
| Ibtehaj10/supreme-court-of-pak-judgments (HuggingFace) | 1,414 Supreme Court judgments, full text + registry citation. MIT. Static (2024-07-26). Verified live: datasets-server `/search`, `/rows` and `/filter` all answer. **Chosen.** |

## Design consequences

- **Corpus-based, not live-query.** Both sources are static snapshots; the
  server INSTRUCTIONS and every `dataset_note` say so, with dates.
- **Pinned revision.** The statutes corpus downloads a pinned dataset revision
  (`81bd25d5...`), so the corpus is reproducible and citations are stable.
- **No ELI.** Pakistan has not deployed ELI; `eli_uri` carries the canonical
  pakistancode.gov.pk PDF URL (statutes) or the dataset row URI (judgments).
- **Titles are derived.** The statute PDFs have opaque hash file names, so the
  title and year are extracted from the leading lines of each document. The
  extraction is original code for this connector - the Legal Data Hunter
  harvester that catalogues the same datasets is AGPL-3.0, so no code was
  reused from it.
- **Embeddings stripped.** The judgments dataset carries a 1024-float
  embeddings column; the client removes it before anything reaches the LLM.

## Later candidates (not in this MVP)

Legal Data Hunter also catalogues, with working configs: SECP (securities
regulation), FBR tax doctrine, the Islamabad and Peshawar High Courts, and the
Balochistan provincial code. Any of these can become a feature-002 widening
round if there is demand.
