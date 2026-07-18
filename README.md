# pk-eli-mcp

<!-- mcp-name: io.github.matematicsolutions/pk-eli-mcp -->

MCP server for Pakistani law: 967 federal statutes and 1,414 Supreme Court of
Pakistan judgments, full text, with a strict citation contract on every response.

Pakistan publishes no machine-readable legal API, so this connector is
**corpus-based**: it serves two public HuggingFace mirrors of official sources
instead of querying a government endpoint live.

| Collection | Source | Snapshot | License |
|---|---|---|---|
| Federal statutes (967) | [Pakistan_Laws_Dataset](https://huggingface.co/datasets/AyeshaJadoon/Pakistan_Laws_Dataset), collected from [pakistancode.gov.pk](https://pakistancode.gov.pk/) PDFs (Ministry of Law and Justice) | 2025-01-30 | ODC-BY 1.0 |
| Supreme Court judgments (1,414) | [supreme-court-of-pak-judgments](https://huggingface.co/datasets/Ibtehaj10/supreme-court-of-pak-judgments) | 2024-07-26 | MIT |

**Honest limits, stated up front:** both collections are static snapshots.
Amendments after 2025-01-30 and judgments after 2024-07-26 are absent, and the
judgment corpus is a subset of the Court's output. Every response repeats this
in `dataset_note`. Verify currency against [pakistancode.gov.pk](https://pakistancode.gov.pk/)
and [scp.gov.pk](https://www.scp.gov.pk/) before relying on a provision.

## Tools

| Tool | What it does |
|---|---|
| `pk_search_laws` | Keyword search over the 967 federal statutes (title-weighted). First call downloads the 47 MB corpus once, then serves from cache. |
| `pk_get_law` | Full statute text by `law_id` (truncated at ~300,000 characters). |
| `pk_case_search` | Server-side full-text search over the Supreme Court judgments. |
| `pk_get_decision` | One judgment's full text, by `row_idx` or registry `case_id`. |
| `pk_verify_citations` | Anti-hallucination check: extracts Pakistani legal citations from any text and verifies each against the corpus. |

## Citation verification (`pk_verify_citations`)

Run it on a drafted answer, memo or pleading before it reaches a reader. The tool
extracts every checkable citation and verifies it against the corpus:

- statute sections: `section 302 of the Pakistan Penal Code`,
  `section 10A of the Pakistan Study Centres Act, 1976`, `sections 6 and 7 of ...`,
  `sub-section (1) of section 6 of ...`;
- act coordinates: `Act No. XLV of 1860`, `Ordinance No. LII of 2000` (Roman or Arabic);
- Constitution articles: `Article 184(3) of the Constitution` (the tail is required -
  a bare `Article 5` could be any instrument and is deliberately ignored);
- Supreme Court registry citations: `Crl.A. 93/2013`, `Criminal Appeal No. 93 of 2013`.

The semantics are deliberately hard:

- A cited section that does not exist makes the whole result `HALLUCINATION_DETECTED`
  with `isError=true`, plus a range hint of what does exist (`section 999 does not
  exist in this act; 59 sections are machine-detectable in its text, numbered
  section 1-66`).
- `NO_CITATIONS_FOUND` is not a success - it means there was nothing to check.
- A parenthetical description right after a citation
  (`section 302 of the Pakistan Penal Code (punishment of qatl-i-amd)`) is
  content-checked against the real provision text with a character-trigram match.
  A mismatch is a review signal, never a hard block.
- Everything the tool could NOT check is listed in a structured `gaps` field. The
  judgment corpus is a subset and roughly half the statute PDFs carry no
  machine-readable coordinate header, so an unresolvable citation is reported as
  `out_of_corpus` ("existence UNKNOWN, not disproven"), never as a hallucination.

The parse-verify-report pattern is adapted from
[korean-law-mcp](https://github.com/chrisryugj/korean-law-mcp) (MIT) - see
`THIRD_PARTY.md`. The same pattern shipped first in it-eli-mcp v0.6.0; the fleet
rollout plan lives in [`docs/VERIFY-ROLLOUT.md`](docs/VERIFY-ROLLOUT.md).

## Citation contract

Pakistan has not deployed ELI. Every response still carries the fleet contract:

- `eli_uri` - a stable, resolvable URL: the canonical pakistancode.gov.pk PDF for
  statutes, the dataset row URI for judgments. Never invented; see `eli_note`.
- `human_readable_citation` - e.g. `THE PRIVATISATION COMMISSION ORDINANCE, 2000`
  or `Supreme Court of Pakistan, Crl.A. 93/2013`.
- `source_url` - where a human can read the same document.

## Install

```bash
uvx pk-eli-mcp          # or: pip install pk-eli-mcp
```

Claude Code:

```bash
claude mcp add pk-eli-mcp -- uvx pk-eli-mcp
```

Or drop `.mcp.json.example` into your project as `.mcp.json`.


### Windows 11 ze Smart App Control

Smart App Control blokuje niepodpisane pliki wykonywalne, a `uvx.exe`, `pip.exe`
i generowany przy instalacji `pk-eli-mcp.exe` podpisane nie sa. `python.exe`
z python.org jest podpisany przez Python Software Foundation, wiec uruchomienie
przez modul omija blokade:

```bash
python -m pip install pk-eli-mcp
python -m pk_eli_mcp
```

```json
{ "mcpServers": { "pk-eli-mcp": { "command": "python", "args": ["-m", "pk_eli_mcp"] } } }
```

Nie wylaczaj Smart App Control, zeby to obejsc - wylaczenia nie da sie cofnac
bez ponownej instalacji systemu.

## Configuration

| Env | Default | Purpose |
|---|---|---|
| `PK_ELI_CACHE_DIR` | `~/.matematic/cache/pk-eli` | Corpus file + HTTP cache |
| `PK_ELI_AUDIT_DIR` | `~/.matematic/audit` | JSONL audit log (one line per tool call) |

## Development

```bash
python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"   # Linux/macOS: .venv/bin/pip
ruff check src tests
pytest tests/test_instructions_drift.py tests/test_corpus.py   # offline
pytest tests/test_smoke.py                                     # live (downloads the corpus)
```

## Governance

Read-only by design: the server can search and fetch, never write. Every tool
call appends an audit line (`~/.matematic/audit/pk-eli-mcp.jsonl`) with a SHA-256
input hash, duration and status. See `CONSTITUTION.md` for the rules this
connector is built under and `DISCOVERY.md` for how the sources were chosen.

## License

Apache-2.0 for this code. The datasets keep their own licenses (ODC-BY 1.0 and
MIT); the underlying texts are Government of Pakistan public legal documents.
