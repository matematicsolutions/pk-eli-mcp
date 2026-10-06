# pk-eli-mcp - Claude plugin

Pakistani law with verifiable citations, as a Claude plugin. It runs the
[pk-eli-mcp](https://github.com/matematicsolutions/pk-eli-mcp) MCP server, version 0.4.4
from PyPI. `server/uv.lock` pins that package and every dependency with hashes, and the
plugin starts it with `uv run --frozen`, so it runs exactly what was reviewed. Every
answer carries the official source, so a citation can be checked instead of trusted.

What it covers: 967 federal statutes in full text (search and fetch, from a corpus collected from pakistancode.gov.pk PDFs, snapshot of 2025-01-30, ODC-BY 1.0), 1,414 Supreme Court of Pakistan judgments (search and full decision, MIT), and a tool that checks the Pakistani citations in a text. The full tool list is in the
[main README](https://github.com/matematicsolutions/pk-eli-mcp#readme).

## Requirements

Claude Code or the Claude desktop app, and [uv](https://docs.astral.sh/uv/) on your
machine (it installs the locked packages on first start and runs the server).

## Install

```
/plugin marketplace add matematicsolutions/pk-eli-mcp
/plugin install pk-eli-mcp@pk-eli-mcp
```

## Data

The server runs on your machine. Each tool call sends your query to Hugging Face's public datasets service (datasets-server.huggingface.co) for judgment searches; statute searches run on a local copy of the corpus
and to nothing else; nothing goes to MateMatic. Your query and the results also pass
through whatever model you use, the same way as any other message.

On the first statute call the server downloads the statute corpus once from Hugging Face (dataset `AyeshaJadoon/Pakistan_Laws_Dataset`, pinned to revision `81bd25d`, so the content cannot change underneath) and keeps it in the cache folder below. The plugin sets `PK_ELI_CORPUS_URL` to empty in `plugin.json`, so this download comes straight from that pinned revision and not from a MateMatic mirror. Judgments come from the dataset `Ibtehaj10/supreme-court-of-pak-judgments`, queried live. Both are datasets compiled by third parties from official publications, not the official portals themselves: verify a statute's currency at pakistancode.gov.pk.

Two things are written locally, in your home directory:

- a response cache (`~/.matematic/cache/pk-eli`), so a repeated lookup does not hit
  the source again. Court decisions are public records and can name the parties.
- an audit log (`~/.matematic/audit/pk-eli-mcp.jsonl`), one line per tool call: the
  tool name, a SHA-256 hash of the input (not the input itself), result size, time
  and status.

Delete either folder at any time; `PK_ELI_CACHE_DIR` and `PK_ELI_AUDIT_DIR` move them.

## Licence

Apache-2.0, see the repository's [LICENSE](https://github.com/matematicsolutions/pk-eli-mcp/blob/main/LICENSE).
