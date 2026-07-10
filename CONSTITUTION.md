# CONSTITUTION - pk-eli-mcp

The non-negotiable rules this connector is built and maintained under.
Derived from the matematicsolutions connector fleet constitution.

## Article I - Scope

One repository, one jurisdiction: Pakistan. Federal statutes and Supreme Court
judgments, read-only. No other country's data, no write operations, ever.

## Article II - Audit

Every tool call writes one JSONL line (timestamp, tool, SHA-256 input hash,
output size, duration, status) to `~/.matematic/audit/pk-eli-mcp.jsonl`.
If the audit write fails, the tool fails - it never silently continues.

## Article III - Honesty about sources

This connector is corpus-based because Pakistan has no machine-readable legal
API. That fact, the snapshot dates (statutes 2025-01-30, judgments 2024-07-26)
and the subset nature of the judgment corpus are stated in the server
INSTRUCTIONS, in every response's `dataset_note`, and in the README. The
connector never presents snapshot data as current law.

## Article IV - Citation contract

Every response carries `eli_uri`, `human_readable_citation` and `source_url`.
Pakistan has not deployed ELI, so `eli_uri` is a stable canonical URL
(pakistancode.gov.pk PDF for statutes, dataset row URI for judgments) -
documented in `eli_note`, never invented.

## Article V - Text fidelity

Official text is returned verbatim from the corpus, including OCR artifacts.
The connector never rewrites, summarizes or "cleans up" legal text. Derived
metadata (titles, snippets) is the one exception: there, whitespace is
collapsed and OCR artifact characters (soft hyphens, replacement chars) are
swapped for plain hyphens - the `content` field stays verbatim.
