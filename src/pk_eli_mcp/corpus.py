"""Federal-statutes corpus: one-time download of the Pakistan Laws Dataset.

The Pakistan Code portal (pakistancode.gov.pk) has no machine API, so this
connector serves the AyeshaJadoon/Pakistan_Laws_Dataset mirror on HuggingFace:
967 federal laws as ``{file_name, text}`` with full statute text, collected from
the Ministry of Law and Justice PDFs (license ODC-BY 1.0).

The corpus is provisioned lazily on first use, along the fleet ladder:

1. **cache** - a previously downloaded corpus under the cache dir is reused;
   every later call is a pure in-memory query.
2. **release asset** - a pre-built ``pk-laws-corpus.json.gz`` from the GitHub
   release (``releases/latest/download/``), verified against its ``.sha256``
   sidecar (or the ``PK_ELI_CORPUS_SHA256`` pin) before install. Fast path.
3. **HuggingFace origin** - the pinned dataset revision, exactly as before.
4. **clear error** - :class:`CorpusUnavailableError` with both failure reasons.

Governance: the data is always the verbatim pinned snapshot - an asset is only
installed after its sha256 verifies; otherwise we fetch from the origin. How the
corpus was provisioned is stamped into a ``.provenance.json`` sidecar (source +
fetch time), never hidden. The dataset is a static snapshot (last updated
2025-01-30) - documented, not hidden.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import anyio
import httpx

from .citations import clean_line, extract_title, extract_year, law_source_url

LAWS_DATASET = "AyeshaJadoon/Pakistan_Laws_Dataset"
# Pinned revision = reproducible corpus (the dataset is static; 2025-01-30 snapshot).
LAWS_REVISION = "81bd25d52ad21f311af47d5ab4604daea9d83aa2"
LAWS_DATA_URL = (
    f"https://huggingface.co/datasets/{LAWS_DATASET}/resolve/{LAWS_REVISION}/pdf_data.json"
)
LAWS_SNAPSHOT_DATE = "2025-01-30"

_CORPUS_FILENAME = f"pdf_data.{LAWS_REVISION[:12]}.json"
_TOKEN_RE = re.compile(r"[a-z0-9]{2,}")
_DOWNLOAD_TIMEOUT = httpx.Timeout(300.0, connect=15.0)

# Pre-built corpus mirrored as a GitHub release asset (the same pinned HF file,
# gzipped). The "latest" download URL self-activates the moment a release carrying
# pk-laws-corpus.json.gz + its .sha256 sidecar is cut; until then it 404s and we
# fall through to the HuggingFace origin. A plain (non-.gz) URL also works - the
# .gz suffix drives decompression. Override with PK_ELI_CORPUS_URL ('' disables).
DEFAULT_CORPUS_ASSET_URL = (
    "https://github.com/matematicsolutions/pk-eli-mcp/releases/latest/download/"
    "pk-laws-corpus.json.gz"
)
USER_AGENT = "pk-eli-mcp (+https://github.com/matematicsolutions/pk-eli-mcp)"
_SHA256_RE = re.compile(r"[0-9a-fA-F]{64}")


class CorpusUnavailableError(RuntimeError):
    """Every provisioning path failed (release asset and HuggingFace origin)."""


def _resolve_cache_dir() -> Path:
    env = os.environ.get("PK_ELI_CACHE_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".matematic" / "cache" / "pk-eli"


@dataclass
class Law:
    """One federal statute from the corpus."""

    law_id: str  # the dataset file_name, e.g. "administrator...50b.pdf"
    title: str | None
    year: int | None
    text: str
    source_url: str = ""

    def __post_init__(self) -> None:
        if not self.source_url:
            self.source_url = law_source_url(self.law_id)


@dataclass
class SearchHit:
    law: Law
    score: int
    snippet: str


@dataclass
class LawsCorpus:
    """In-memory index over the statute corpus."""

    laws: list[Law] = field(default_factory=list)
    _by_id: dict[str, Law] = field(default_factory=dict)

    @classmethod
    def from_records(cls, records: list[dict[str, str]]) -> LawsCorpus:
        laws = []
        for record in records:
            file_name = (record.get("file_name") or "").strip()
            text = record.get("text") or ""
            if not file_name or not text:
                continue
            title = extract_title(text)
            laws.append(
                Law(
                    law_id=file_name,
                    title=title,
                    year=extract_year(title, text),
                    text=text,
                )
            )
        corpus = cls(laws=laws)
        corpus._by_id = {law.law_id: law for law in laws}
        return corpus

    def get(self, law_id: str) -> Law | None:
        """Lookup by file_name; tolerate a missing/extra ``.pdf`` suffix."""
        key = law_id.strip()
        for candidate in (key, f"{key}.pdf", key.removesuffix(".pdf")):
            law = self._by_id.get(candidate)
            if law is not None:
                return law
        return None

    def search(self, query: str, limit: int) -> list[SearchHit]:
        """Rank statutes by query tokens: title matches weigh 6x a body occurrence."""
        tokens = _TOKEN_RE.findall(query.lower())
        if not tokens:
            return []
        hits: list[SearchHit] = []
        for law in self.laws:
            title_lower = (law.title or "").lower()
            text_lower = law.text.lower()
            score = 0
            first_pos = -1
            for token in tokens:
                if token in title_lower:
                    score += 6
                occurrences = min(text_lower.count(token), 5)
                score += occurrences
                if occurrences and first_pos < 0:
                    first_pos = text_lower.find(token)
            if score > 0:
                hits.append(SearchHit(law=law, score=score, snippet=_snippet(law.text, first_pos)))
        hits.sort(key=lambda hit: hit.score, reverse=True)
        return hits[:limit]


def _snippet(text: str, pos: int, radius: int = 160) -> str:
    if pos < 0:
        pos = 0
    start = max(0, pos - radius)
    end = min(len(text), pos + radius)
    return clean_line(text[start:end])


_corpus: LawsCorpus | None = None
_corpus_lock = anyio.Lock()


async def get_corpus() -> LawsCorpus:
    """Singleton corpus: download once (about 47 MB), then serve from memory."""
    global _corpus
    if _corpus is not None:
        return _corpus
    async with _corpus_lock:
        if _corpus is not None:
            return _corpus
        path = await _ensure_corpus_file()
        records = await anyio.to_thread.run_sync(_load_records, path)
        _corpus = LawsCorpus.from_records(records)
        return _corpus


def _load_records(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        raise ValueError(f"Unexpected corpus shape in {path}: expected a JSON array")
    return data


def _asset_url() -> str:
    """Release-asset URL, overridable via ``PK_ELI_CORPUS_URL`` ('' disables)."""
    return os.environ.get("PK_ELI_CORPUS_URL", DEFAULT_CORPUS_ASSET_URL).strip()


async def _ensure_corpus_file() -> Path:
    """Return a ready corpus file, provisioning it once along the ladder.

    cache -> verified release asset -> HuggingFace origin -> clear error.
    """
    cache_dir = _resolve_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / _CORPUS_FILENAME
    if path.exists() and path.stat().st_size > 0:
        return path

    errors: list[str] = []
    url = _asset_url()
    if url:
        try:
            await _download_verified_asset(url, path)
            _write_provenance(path, f"release-asset ({url})")
            return path
        except Exception as exc:  # any failure (404, network, bad hash) -> origin
            errors.append(f"release-asset ({url}): {type(exc).__name__}: {exc}")

    try:
        await _download_from_origin(path)
        _write_provenance(path, f"huggingface ({LAWS_DATASET}@{LAWS_REVISION[:12]})")
        return path
    except Exception as exc:
        errors.append(f"huggingface ({LAWS_DATA_URL}): {type(exc).__name__}: {exc}")

    raise CorpusUnavailableError(
        f"Could not provision the Pakistan Laws corpus at {path}. "
        f"All provisioning paths failed ({' | '.join(errors)}). "
        f"Check network access to github.com and huggingface.co, then retry."
    )


async def _download_from_origin(path: Path) -> None:
    """Stream the pinned dataset revision straight from HuggingFace (the origin)."""
    tmp_path = path.with_suffix(".part")
    async with (
        httpx.AsyncClient(
            timeout=_DOWNLOAD_TIMEOUT, follow_redirects=True, headers={"User-Agent": USER_AGENT}
        ) as client,
        client.stream("GET", LAWS_DATA_URL) as response,
    ):
        response.raise_for_status()
        with tmp_path.open("wb") as fh:
            async for chunk in response.aiter_bytes():
                fh.write(chunk)
    tmp_path.replace(path)


async def _download_verified_asset(url: str, target: Path) -> None:
    """Download the pre-built corpus and verify its sha256 before installing atomically.

    The checksum is verified against the bytes actually downloaded (the ``.gz`` when the
    URL is gzipped). We refuse to install a corpus we cannot verify: if no checksum is
    available (env ``PK_ELI_CORPUS_SHA256`` or a ``<url>.sha256`` sidecar), this raises
    and the caller falls back to the HuggingFace origin.
    """
    dl_path = target.with_suffix(target.suffix + ".download")
    async with httpx.AsyncClient(
        timeout=_DOWNLOAD_TIMEOUT, follow_redirects=True, headers={"User-Agent": USER_AGENT}
    ) as client:
        expected = await _expected_sha256(client, url)
        if not expected:
            raise RuntimeError(
                "no sha256 checksum available; refusing to install an unverified corpus"
            )
        digest = hashlib.sha256()
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            with dl_path.open("wb") as fh:
                async for chunk in resp.aiter_bytes():
                    fh.write(chunk)
                    digest.update(chunk)
        actual = digest.hexdigest()
        if actual.lower() != expected.lower():
            dl_path.unlink(missing_ok=True)
            raise RuntimeError(f"sha256 mismatch: expected {expected}, got {actual}")

    tmp_path = target.with_suffix(".part")
    if url.endswith(".gz"):
        await anyio.to_thread.run_sync(_gunzip, dl_path, tmp_path)
        dl_path.unlink(missing_ok=True)
    else:
        os.replace(dl_path, tmp_path)
    os.replace(tmp_path, target)


def _gunzip(src: Path, dst: Path) -> None:
    """Stream-decompress a gzip file (constant memory)."""
    with gzip.open(src, "rb") as fin, dst.open("wb") as fout:
        shutil.copyfileobj(fin, fout, length=1024 * 1024)


async def _expected_sha256(client: httpx.AsyncClient, url: str) -> str | None:
    """Resolve the expected checksum: pinned env var first, else the ``.sha256`` sidecar."""
    pinned = os.environ.get("PK_ELI_CORPUS_SHA256", "").strip()
    if pinned:
        match = _SHA256_RE.search(pinned)
        return match.group(0) if match else None
    resp = await client.get(f"{url}.sha256")
    if resp.status_code != 200:
        return None
    match = _SHA256_RE.search(resp.text)
    return match.group(0) if match else None


def _write_provenance(path: Path, provenance: str) -> None:
    """Stamp how the corpus was provisioned into a sidecar - never silent staleness."""
    sidecar = path.with_name(path.name + ".provenance.json")
    payload = {
        "provenance": provenance,
        "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "dataset": LAWS_DATASET,
        "revision": LAWS_REVISION,
        "snapshot_date": LAWS_SNAPSHOT_DATE,
    }
    sidecar.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def corpus_provenance() -> dict[str, str] | None:
    """Read the provenance sidecar of the cached corpus, if present."""
    sidecar = _resolve_cache_dir() / (_CORPUS_FILENAME + ".provenance.json")
    if not sidecar.exists():
        return None
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else None


def set_corpus_for_testing(corpus: LawsCorpus | None) -> None:
    """Inject a small corpus in offline tests (bypasses the 47 MB download)."""
    global _corpus
    _corpus = corpus
