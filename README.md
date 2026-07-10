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
