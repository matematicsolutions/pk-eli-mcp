"""Offline tests for the lazy corpus-provisioning ladder (``corpus._ensure_corpus_file``).

Covers: cache short-circuit, release-asset download with a REAL sha256 verify over a
localhost HTTP server (gzipped asset), checksum-mismatch fallback to the HuggingFace
origin, and the final clear error - without touching the network or the 47 MB corpus.
"""

from __future__ import annotations

import functools
import gzip
import hashlib
import http.server
import socketserver
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from pk_eli_mcp import corpus

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE = (FIXTURES / "laws_sample.json").read_bytes()


@pytest.fixture
def cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Fresh cache dir + clean singleton + tuning env cleared."""
    dest = tmp_path / "cache"
    monkeypatch.setenv("PK_ELI_CACHE_DIR", str(dest))
    monkeypatch.delenv("PK_ELI_CORPUS_URL", raising=False)
    monkeypatch.delenv("PK_ELI_CORPUS_SHA256", raising=False)
    corpus.set_corpus_for_testing(None)
    yield dest
    corpus.set_corpus_for_testing(None)


@pytest.fixture
def served(tmp_path: Path) -> Iterator[tuple[Path, str]]:
    """A localhost HTTP server over a scratch dir; yields (dir, base_url)."""
    root = tmp_path / "release"
    root.mkdir()
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield root, f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        thread.join(timeout=5)


def _cached_path(cache_dir: Path) -> Path:
    return cache_dir / corpus._CORPUS_FILENAME


async def test_cached_corpus_short_circuits(cache_dir: Path, monkeypatch: pytest.MonkeyPatch):
    """An existing non-empty corpus file is used without any network attempt."""
    cache_dir.mkdir(parents=True)
    _cached_path(cache_dir).write_bytes(SAMPLE)
    # An unreachable asset URL would explode if the ladder went past the cache.
    monkeypatch.setenv("PK_ELI_CORPUS_URL", "http://127.0.0.1:1/nope.json.gz")

    loaded = await corpus.get_corpus()
    assert loaded.laws, "expected laws parsed from the cached corpus"


async def test_release_asset_gz_with_sha256(cache_dir, served, monkeypatch):
    """The gzipped asset is downloaded, verified via .sha256 sidecar, and installed."""
    root, base = served
    gz = root / "pk-laws-corpus.json.gz"
    gz.write_bytes(gzip.compress(SAMPLE))
    sha = hashlib.sha256(gz.read_bytes()).hexdigest()
    (root / "pk-laws-corpus.json.gz.sha256").write_text(
        f"{sha}  pk-laws-corpus.json.gz\n", encoding="utf-8"
    )
    monkeypatch.setenv("PK_ELI_CORPUS_URL", f"{base}/pk-laws-corpus.json.gz")
    # Origin must not be needed: point it at a guaranteed-dead URL.
    monkeypatch.setattr(corpus, "LAWS_DATA_URL", "http://127.0.0.1:1/dead.json")

    loaded = await corpus.get_corpus()
    assert loaded.laws
    prov = corpus.corpus_provenance()
    assert prov is not None and prov["provenance"].startswith("release-asset")
    assert prov["revision"] == corpus.LAWS_REVISION


async def test_missing_checksum_refuses_asset_and_uses_origin(cache_dir, served, monkeypatch):
    """No .sha256 sidecar -> the unverified asset is refused; origin serves the corpus."""
    root, base = served
    (root / "pk-laws-corpus.json.gz").write_bytes(gzip.compress(SAMPLE))  # no sidecar
    (root / "origin.json").write_bytes(SAMPLE)
    monkeypatch.setenv("PK_ELI_CORPUS_URL", f"{base}/pk-laws-corpus.json.gz")
    monkeypatch.setattr(corpus, "LAWS_DATA_URL", f"{base}/origin.json")

    loaded = await corpus.get_corpus()
    assert loaded.laws
    prov = corpus.corpus_provenance()
    assert prov is not None and prov["provenance"].startswith("huggingface")


async def test_sha256_mismatch_falls_back_to_origin(cache_dir, served, monkeypatch):
    """A bad checksum rejects the download; the HuggingFace origin is used instead."""
    root, base = served
    (root / "pk-laws-corpus.json.gz").write_bytes(gzip.compress(SAMPLE))
    (root / "pk-laws-corpus.json.gz.sha256").write_text(
        f"{'0' * 64}  pk-laws-corpus.json.gz\n", encoding="utf-8"
    )
    (root / "origin.json").write_bytes(SAMPLE)
    monkeypatch.setenv("PK_ELI_CORPUS_URL", f"{base}/pk-laws-corpus.json.gz")
    monkeypatch.setattr(corpus, "LAWS_DATA_URL", f"{base}/origin.json")

    loaded = await corpus.get_corpus()
    assert loaded.laws
    prov = corpus.corpus_provenance()
    assert prov is not None and prov["provenance"].startswith("huggingface")
    # The rejected download must not linger half-installed.
    leftovers = [p for p in _cached_path(cache_dir).parent.glob("*.download")]
    assert not leftovers


async def test_all_paths_fail_raises_clear_error(cache_dir, monkeypatch):
    """Dead asset + dead origin surface CorpusUnavailableError with both reasons."""
    monkeypatch.setenv("PK_ELI_CORPUS_URL", "http://127.0.0.1:1/nope.json.gz")
    monkeypatch.setattr(corpus, "LAWS_DATA_URL", "http://127.0.0.1:1/dead.json")

    with pytest.raises(corpus.CorpusUnavailableError) as exc:
        await corpus.get_corpus()
    msg = str(exc.value)
    assert "release-asset" in msg and "huggingface" in msg


async def test_asset_url_env_empty_disables_fast_path(cache_dir, served, monkeypatch):
    """PK_ELI_CORPUS_URL='' skips the asset entirely; origin provisions the corpus."""
    root, base = served
    (root / "origin.json").write_bytes(SAMPLE)
    monkeypatch.setenv("PK_ELI_CORPUS_URL", "")
    monkeypatch.setattr(corpus, "LAWS_DATA_URL", f"{base}/origin.json")

    loaded = await corpus.get_corpus()
    assert loaded.laws
    prov = corpus.corpus_provenance()
    assert prov is not None and prov["provenance"].startswith("huggingface")
