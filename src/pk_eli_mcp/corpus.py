"""Federal-statutes corpus: one-time download of the Pakistan Laws Dataset.

The Pakistan Code portal (pakistancode.gov.pk) has no machine API, so this
connector serves the AyeshaJadoon/Pakistan_Laws_Dataset mirror on HuggingFace:
967 federal laws as ``{file_name, text}`` with full statute text, collected from
the Ministry of Law and Justice PDFs (license ODC-BY 1.0).

The download is pinned to a dataset revision, cached as a plain file under the
cache dir, and indexed in memory on first use. The dataset is a static snapshot
(last updated 2025-01-30) - this is documented, not hidden.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
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


async def _ensure_corpus_file() -> Path:
    cache_dir = _resolve_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / _CORPUS_FILENAME
    if path.exists() and path.stat().st_size > 0:
        return path
    tmp_path = path.with_suffix(".part")
    async with (
        httpx.AsyncClient(timeout=_DOWNLOAD_TIMEOUT, follow_redirects=True) as client,
        client.stream("GET", LAWS_DATA_URL) as response,
    ):
        response.raise_for_status()
        with tmp_path.open("wb") as fh:
            async for chunk in response.aiter_bytes():
                fh.write(chunk)
    tmp_path.replace(path)
    return path


def set_corpus_for_testing(corpus: LawsCorpus | None) -> None:
    """Inject a small corpus in offline tests (bypasses the 47 MB download)."""
    global _corpus
    _corpus = corpus
