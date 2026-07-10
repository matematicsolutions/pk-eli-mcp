"""Client for the Supreme Court judgments dataset via the HuggingFace datasets-server.

Dataset: Ibtehaj10/supreme-court-of-pak-judgments (MIT) - 1,414 Supreme Court of
Pakistan judgments with full text. The datasets-server exposes server-side
full-text search (``/search``), row fetch (``/rows``) and SQL-ish filtering
(``/filter``); all keyless. Rows carry a 1024-float embeddings column that is
useless to an LLM - it is stripped before anything is returned.
"""

from __future__ import annotations

import anyio
import httpx

from .cache import HttpCache

DATASETS_SERVER = "https://datasets-server.huggingface.co"
SC_DATASET = "Ibtehaj10/supreme-court-of-pak-judgments"
SC_DATASET_URL = f"https://huggingface.co/datasets/{SC_DATASET}"
SC_SNAPSHOT_DATE = "2024-07-26"

_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
_MAX_ATTEMPTS = 3
_TIMEOUT = httpx.Timeout(40.0, connect=10.0)


class ScClient:
    """Async client for the judgments dataset. Use as ``async with ScClient() as c:``."""

    def __init__(self, cache: HttpCache | None = None) -> None:
        self._cache = cache or HttpCache()
        self._http = httpx.AsyncClient(
            timeout=_TIMEOUT,
            headers={"Accept": "application/json"},
            follow_redirects=True,
        )

    async def __aenter__(self) -> ScClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()
        self._cache.close()

    async def _get_json(self, path: str, params: dict[str, str | int]) -> dict:
        url = f"{DATASETS_SERVER}{path}"
        cache_key = url + "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        cached = self._cache.get(cache_key)
        if isinstance(cached, dict):
            return cached
        last_exc: Exception | None = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                resp = await self._http.get(url, params=params)
                resp.raise_for_status()
                payload = resp.json()
                _strip_embeddings(payload)
                self._cache.set(cache_key, payload, ttl=HttpCache.ttl_for("act"))
                return payload
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                if exc.response.status_code not in _RETRY_STATUS or attempt == _MAX_ATTEMPTS - 1:
                    raise
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_exc = exc
                if attempt == _MAX_ATTEMPTS - 1:
                    raise
            await anyio.sleep(0.5 * (2**attempt))
        assert last_exc is not None
        raise last_exc

    async def search(self, query: str, offset: int, length: int) -> dict:
        """Server-side full-text search over the judgments."""
        return await self._get_json(
            "/search",
            {
                "dataset": SC_DATASET,
                "config": "default",
                "split": "train",
                "query": query,
                "offset": offset,
                "length": length,
            },
        )

    async def row(self, row_idx: int) -> dict:
        """Fetch one judgment row by index (0-based)."""
        return await self._get_json(
            "/rows",
            {
                "dataset": SC_DATASET,
                "config": "default",
                "split": "train",
                "offset": row_idx,
                "length": 1,
            },
        )

    async def filter_by_case_id(self, case_id: str) -> dict:
        """Find a judgment whose citation field contains ``case_id``."""
        safe = case_id.replace("'", "''")
        return await self._get_json(
            "/filter",
            {
                "dataset": SC_DATASET,
                "config": "default",
                "split": "train",
                "where": f"\"citation_number\" LIKE '%{safe}%'",
                "length": 1,
            },
        )


def _strip_embeddings(payload: dict) -> None:
    """Drop the 1024-float embeddings column in place - noise for an LLM."""
    for row_wrapper in payload.get("rows", []):
        row = row_wrapper.get("row")
        if isinstance(row, dict):
            row.pop("embeddings", None)
