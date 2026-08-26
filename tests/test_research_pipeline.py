"""Phase 12B tests: candidate selection, research pipeline, skill & provider.

Offline end-to-end: FakeSearchProvider + httpx.MockTransport + fake DNS.
Covers partial success, dedupe, budget bounds, provenance, failure-model
separation, the kill switch and the Tavily adapter mapping.
"""

from __future__ import annotations

import httpx
import pytest

from tests.test_research_safety import PUBLIC, FakeResolver
from zglab_rag.capabilities.contracts import CapabilityContext, CapabilityRequest
from zglab_rag.config import Settings
from zglab_rag.research.contracts import (
    FetchFailureReason,
    ResearchBudget,
    ResearchPolicyError,
    ResearchStatus,
    SearchProviderError,
    SearchProviderUnavailableError,
    SearchResult,
)
from zglab_rag.research.fetch import SafeFetcher
from zglab_rag.research.search import (
    FakeSearchProvider,
    TavilySearchProvider,
    select_candidates,
)
from zglab_rag.research.service import ResearchService
from zglab_rag.research.skill import WebResearchSkill, build_research_service

LONG_PARAGRAPH = "这是一段足够长的正文内容，用来通过最小质量检查。" * 10


def _article(body: str = LONG_PARAGRAPH, title: str = "文章") -> str:
    return (
        f"<html><head><title>{title}</title></head>"
        f"<body><article><p>{body}</p></article></body></html>"
    )


def _hit(url: str, rank: int) -> SearchResult:
    return SearchResult(title=f"t{rank}", url=url, snippet="s", rank=rank, provider="fake")


def _service(
    results: list[SearchResult],
    handler,
    *,
    budget: ResearchBudget | None = None,
    enabled: bool = True,
    resolver: FakeResolver | None = None,
    clock=None,
) -> tuple[ResearchService, FakeSearchProvider]:
    provider = FakeSearchProvider(results)
    budget = budget or ResearchBudget()
    fetcher = SafeFetcher(
        budget, resolver=resolver or FakeResolver({"example.com": [PUBLIC], "other.org": [PUBLIC]}),
        transport=httpx.MockTransport(handler),
    )
    kwargs = {"clock": clock} if clock else {}
    service = ResearchService(provider, budget, enabled=enabled, fetcher=fetcher, **kwargs)
    return service, provider


def _ok_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=_article(), headers={"content-type": "text/html"})


# ---------------------------------------------------------------------------
# Candidate selection
# ---------------------------------------------------------------------------


def test_selection_ordered_deduped_bounded_and_domain_capped() -> None:
    results = [
        _hit("https://example.com/a", 1),
        _hit("https://EXAMPLE.com/a#frag", 2),  # canonical duplicate
        _hit("https://example.com/b", 3),
        _hit("https://example.com/c", 4),  # domain cap (2 per domain)
        _hit("https://other.org/x", 5),
        _hit("file:///etc/passwd", 6),  # unsafe scheme dropped
        _hit("https://other.org/y", 7),
    ]
    selected, rejected = select_candidates(results, ResearchBudget())
    urls = [candidate.result.url for candidate in selected]
    assert urls == [
        "https://example.com/a",
        "https://example.com/b",
        "https://other.org/x",
        "https://other.org/y",
    ]
    assert len(rejected) == 3


def test_selection_is_deterministic() -> None:
    results = [_hit(f"https://example.com/{i}", i + 1) for i in range(6)]
    first, _ = select_candidates(results, ResearchBudget())
    second, _ = select_candidates(results, ResearchBudget())
    assert [c.canonical_url for c in first] == [c.canonical_url for c in second]


# ---------------------------------------------------------------------------
# Research pipeline
# ---------------------------------------------------------------------------


def test_pipeline_full_success_with_provenance() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/go":
            return httpx.Response(302, headers={"location": "https://example.com/final"})
        return httpx.Response(200, content=_article(), headers={"content-type": "text/html"})

    service, provider = _service([_hit("https://example.com/go", 1)], handler)
    result = service.research("量子计算 进展", request_id="r1")
    assert result.status == ResearchStatus.SUCCESS
    assert len(result.evidence) == 1
    evidence = result.evidence[0]
    assert evidence.evidence_id == "W1"
    assert evidence.origin.value == "web"
    assert evidence.trust == "untrusted"
    # Provenance: the URL came from the search hit + validated redirect.
    assert evidence.search_result_url == "https://example.com/go"
    assert evidence.url == "https://example.com/final"
    assert evidence.redirect_chain == ("https://example.com/go",)
    assert evidence.domain == "example.com"
    assert len(provider.calls) == 1  # exactly one search call


def test_pipeline_partial_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/bad":
            return httpx.Response(404, content="")
        if request.url.path == "/error":
            return httpx.Response(500, content="")
        return httpx.Response(200, content=_article(), headers={"content-type": "text/html"})

    service, _provider = _service(
        [
            _hit("https://example.com/bad", 1),
            _hit("https://example.com/good", 2),
            _hit("https://other.org/error", 3),
        ],
        handler,
    )
    result = service.research("问题")
    assert result.status == ResearchStatus.SUCCESS
    assert len(result.evidence) == 1
    assert result.attempted_count == 3
    assert result.fetched_count == 1
    reasons = {failure.reason for failure in result.failures}
    assert reasons == {FetchFailureReason.HTTP_ERROR}


def test_pipeline_all_fetches_fail_is_no_usable_evidence_not_technical() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content="")

    service, _provider = _service([_hit("https://example.com/x", 1)], handler)
    result = service.research("问题")
    assert result.status == ResearchStatus.NO_USABLE_EVIDENCE
    assert result.evidence == []


def test_pipeline_no_search_results() -> None:
    service, _provider = _service([], _ok_handler)
    result = service.research("问题")
    assert result.status == ResearchStatus.NO_RESULTS


def test_pipeline_provider_unavailable_distinct_from_no_evidence() -> None:
    class BrokenProvider(FakeSearchProvider):
        def search(self, query, *, limit):
            raise SearchProviderUnavailableError("down")

    service = ResearchService(BrokenProvider([]), enabled=True)
    assert service.research("问题").status == ResearchStatus.PROVIDER_UNAVAILABLE


def test_pipeline_provider_error_is_technical_failure() -> None:
    class WeirdProvider(FakeSearchProvider):
        def search(self, query, *, limit):
            raise SearchProviderError("bad payload")

    service = ResearchService(WeirdProvider([]), enabled=True)
    assert service.research("问题").status == ResearchStatus.TECHNICAL_FAILURE


def test_pipeline_dedupe_by_content_hash() -> None:
    service, _provider = _service(
        [_hit("https://example.com/mirror1", 1), _hit("https://other.org/mirror2", 2)],
        _ok_handler,
    )
    result = service.research("问题")
    assert result.status == ResearchStatus.SUCCESS
    assert len(result.evidence) == 1  # identical extracted content deduped


def test_pipeline_ssrf_candidate_recorded_as_safe_failure() -> None:
    service, _provider = _service(
        [_hit("http://127.0.0.1/admin", 1), _hit("https://example.com/ok", 2)],
        _ok_handler,
    )
    result = service.research("问题")
    assert result.status == ResearchStatus.SUCCESS
    assert len(result.evidence) == 1
    ssrf = [
        failure
        for failure in result.failures
        if failure.reason == FetchFailureReason.SSRF_REJECTED
    ]
    assert len(ssrf) == 1
    # Failure summaries never contain private IPs beyond the input URL, keys
    # or stack traces; the input URL itself is the candidate identifier.
    assert "127.0.0.1" in ssrf[0].url


def test_pipeline_empty_page_not_evidence() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content="<html><body><nav>只有导航</nav></body></html>",
            headers={"content-type": "text/html"},
        )

    service, _provider = _service([_hit("https://example.com/empty", 1)], handler)
    result = service.research("问题")
    assert result.status == ResearchStatus.NO_USABLE_EVIDENCE
    assert result.failures[0].reason == FetchFailureReason.EMPTY_OR_LOW_QUALITY


def test_pipeline_budget_bounds_attempts() -> None:
    # Alternate domains so the per-domain cap does not bind before the
    # fetch-candidate cap does.
    results = [
        _hit(f"https://{'example.com' if i % 2 == 0 else 'other.org'}/p{i}", i + 1)
        for i in range(8)
    ]
    service, provider = _service(results, _ok_handler)
    result = service.research("问题")
    assert provider.calls == [("问题", 6)]  # one call, capped by budget
    assert result.search_result_count == 6
    assert result.attempted_count == 4  # max_fetch_candidates


def test_pipeline_overall_timeout_returns_timeout_without_evidence() -> None:
    ticks = iter([0.0, 100.0, 100.0])  # start, loop check, elapsed measurement
    service, _provider = _service(
        [_hit("https://example.com/a", 1)],
        _ok_handler,
        clock=lambda: next(ticks),
    )
    result = service.research("问题")
    assert result.status == ResearchStatus.TIMEOUT
    assert result.evidence == []


def test_pipeline_disabled_kill_switch_fails_closed() -> None:
    service, provider = _service([_hit("https://example.com/a", 1)], _ok_handler, enabled=False)
    result = service.research("问题")
    assert result.status == ResearchStatus.POLICY_DISABLED
    assert provider.calls == []  # zero cost spent


def test_pipeline_empty_query_no_search_call() -> None:
    service, provider = _service([], _ok_handler)
    assert service.research("   ").status == ResearchStatus.NO_RESULTS
    assert provider.calls == []


# ---------------------------------------------------------------------------
# WebResearchSkill
# ---------------------------------------------------------------------------


def test_skill_returns_research_result_and_kill_switch_raises() -> None:
    service, _provider = _service([_hit("https://example.com/a", 1)], _ok_handler)
    skill = WebResearchSkill(service)
    request = CapabilityRequest(question="问题")
    context = CapabilityContext(request_id="r1")
    result = skill.execute(request, context)
    assert result.status == ResearchStatus.SUCCESS

    disabled_service, _provider = _service([], _ok_handler, enabled=False)
    disabled_skill = WebResearchSkill(disabled_service)
    with pytest.raises(ResearchPolicyError):
        disabled_skill.execute(request, context)


def test_skill_metadata_flags_network_and_auth() -> None:
    metadata = WebResearchSkill.metadata
    assert metadata.id == "web_research"
    assert metadata.network_access is True
    assert metadata.requires_auth is True


# ---------------------------------------------------------------------------
# Tavily adapter (offline via MockTransport)
# ---------------------------------------------------------------------------


def _tavily(payload_status: int, body: bytes, content_type: str = "application/json"):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer test-key"
        return httpx.Response(payload_status, content=body, headers={"content-type": content_type})

    return TavilySearchProvider("test-key", transport=httpx.MockTransport(handler))


def test_tavily_maps_structured_results() -> None:
    import json

    body = json.dumps(
        {
            "results": [
                {"title": "T1", "url": "https://a.example/1", "content": "snippet one"},
                {"title": "T2", "url": "https://b.example/2", "content": "snippet two"},
            ]
        }
    ).encode()
    provider = _tavily(200, body)
    results = provider.search("query", limit=5)
    assert [result.url for result in results] == ["https://a.example/1", "https://b.example/2"]
    assert [result.rank for result in results] == [1, 2]
    assert all(result.provider == "tavily" for result in results)


@pytest.mark.parametrize("status", [500, 503])
def test_tavily_server_error_is_unavailable(status: int) -> None:
    provider = _tavily(status, b"oops", "text/plain")
    with pytest.raises(SearchProviderUnavailableError):
        provider.search("q", limit=3)


def test_tavily_bad_payload_is_unavailable() -> None:
    provider = _tavily(200, b"not json")
    with pytest.raises(SearchProviderUnavailableError):
        provider.search("q", limit=3)


def test_tavily_requires_key_from_configuration() -> None:
    with pytest.raises(ValueError):
        TavilySearchProvider("")


# ---------------------------------------------------------------------------
# Configuration / runtime factory
# ---------------------------------------------------------------------------


def test_settings_default_web_research_disabled() -> None:
    settings = Settings()
    assert settings.web_research_enabled is False
    assert settings.search_api_key is None


def test_build_research_service_requires_key_and_fails_closed() -> None:
    with pytest.raises(ValueError):
        build_research_service(Settings(search_provider="tavily"))
    service = build_research_service(
        Settings(search_provider="tavily", search_api_key="dummy")
    )
    # Enabled flag defaults to false -> kill switch fails closed.
    assert service.research("q").status == ResearchStatus.POLICY_DISABLED
