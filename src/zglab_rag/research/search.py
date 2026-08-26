"""Search providers & deterministic candidate selection (Phase 12B).

One real provider (Tavily) plus a deterministic fake for offline tests.
Provider JSON never leaves the adapter: everything upstream sees the
provider-neutral SearchResult model.

Candidate selection is deterministic, testable and bounded: provider rank
order, canonical-URL dedupe, URL safety pre-filter and a per-domain cap.
No LLM ever decides which URLs get fetched.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from zglab_rag.research.contracts import (
    ResearchBudget,
    SearchProviderUnavailableError,
    SearchResult,
)
from zglab_rag.research.errors import UnsafeUrlError
from zglab_rag.research.url_safety import canonicalize_url


class FakeSearchProvider:
    """Deterministic provider double; results are returned exactly as given."""

    name = "fake"

    def __init__(self, results: list[SearchResult] | None = None) -> None:
        self.results = results or []
        self.calls: list[tuple[str, int]] = []

    def search(self, query: str, *, limit: int) -> list[SearchResult]:
        self.calls.append((query, limit))
        return list(self.results[:limit])


class TavilySearchProvider:
    """Adapter for the Tavily Search API (official, structured, key-based).

    POST https://api.tavily.com/search with a Bearer key; the response is
    mapped to SearchResult immediately so no vendor shape leaks upstream.
    ``transport`` exists purely for deterministic offline tests.
    """

    name = "tavily"
    _endpoint = "https://api.tavily.com/search"

    def __init__(
        self,
        api_key: str,
        *,
        timeout_seconds: float = 8.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("TavilySearchProvider requires an API key from configuration")
        self._api_key = api_key
        self._timeout = timeout_seconds
        self._transport = transport

    def search(self, query: str, *, limit: int) -> list[SearchResult]:
        payload = {"query": query, "max_results": limit, "search_depth": "basic"}
        try:
            with httpx.Client(
                timeout=self._timeout, transport=self._transport, follow_redirects=False
            ) as client:
                response = client.post(
                    self._endpoint,
                    json=payload,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
        except httpx.HTTPError as exc:
            raise SearchProviderUnavailableError("search request failed") from exc
        if response.status_code >= 500:
            raise SearchProviderUnavailableError("search provider server error")
        if response.status_code >= 400:
            # 4xx (bad key / quota) is a configuration problem, not "no
            # results"; surface it as provider failure, never as evidence.
            raise SearchProviderUnavailableError("search provider rejected request")
        try:
            body = response.json()
            items = body["results"]
        except (ValueError, KeyError, TypeError) as exc:
            raise SearchProviderUnavailableError("search provider payload invalid") from exc

        results: list[SearchResult] = []
        for rank, item in enumerate(items[:limit], start=1):
            url = str(item.get("url") or "")
            if not url:
                continue
            results.append(
                SearchResult(
                    title=str(item.get("title") or ""),
                    url=url,
                    snippet=str(item.get("content") or ""),
                    rank=rank,
                    provider=self.name,
                )
            )
        return results


@dataclass(frozen=True, slots=True)
class Candidate:
    """A fetch candidate with full provenance back to the search hit."""

    result: SearchResult
    canonical_url: str


def select_candidates(
    results: list[SearchResult], budget: ResearchBudget
) -> tuple[list[Candidate], list[SearchResult]]:
    """Deterministic, bounded candidate selection.

    Keeps provider rank order; drops unparseable/unsafe URLs, duplicate
    canonical URLs and more than ``max_candidates_per_domain`` hits per
    registrable-ish host (exact host, deterministic). Returns the selected
    candidates plus the rejected results (for safe diagnostics).
    """
    selected: list[Candidate] = []
    rejected: list[SearchResult] = []
    seen_canonical: set[str] = set()
    per_domain: dict[str, int] = {}
    for result in sorted(results, key=lambda item: item.rank):
        if len(selected) >= budget.max_fetch_candidates:
            break
        try:
            canonical = canonicalize_url(result.url)
        except UnsafeUrlError:
            rejected.append(result)
            continue
        if canonical in seen_canonical:
            rejected.append(result)
            continue
        host = canonical.split("/")[2].split(":")[0]
        if per_domain.get(host, 0) >= budget.max_candidates_per_domain:
            rejected.append(result)
            continue
        seen_canonical.add(canonical)
        per_domain[host] = per_domain.get(host, 0) + 1
        selected.append(Candidate(result=result, canonical_url=canonical))
    return selected, rejected
