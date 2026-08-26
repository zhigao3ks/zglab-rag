"""WebResearchSkill & runtime factory (Phase 12B).

The skill is a thin Capability-shaped adapter over ResearchService:

    CapabilityRequest -> ResearchService.research() -> ResearchResult

It does NOT generate answers, render citations, fall back to
PersonalKnowledgeSkill or plan anything. Its output is the research-
specific ResearchResult (ExternalEvidence[]); unifying capability output
is a 12C decision.

Phase 12B deliberately does NOT register ``web_research`` in the runtime
CapabilityRegistry and does NOT expose any HTTP endpoint: registry
presence is not API reachability, and product integration belongs to 12D.
The skill exists so the pipeline is callable, testable and kill-switched.
"""

from __future__ import annotations

from zglab_rag.capabilities.contracts import (
    CapabilityContext,
    CapabilityMetadata,
    CapabilityRequest,
)
from zglab_rag.config import Settings
from zglab_rag.research.contracts import (
    WEB_RESEARCH_CAPABILITY_ID,
    ResearchBudget,
    ResearchPolicyError,
    ResearchResult,
)
from zglab_rag.research.search import TavilySearchProvider
from zglab_rag.research.service import ResearchService

WEB_RESEARCH_METADATA = CapabilityMetadata(
    id=WEB_RESEARCH_CAPABILITY_ID,
    name="Web Research",
    description=(
        "Bounded public-web research: one search call, safe fetch, "
        "deterministic extraction into untrusted external evidence."
    ),
    requires_auth=True,
    network_access=True,
)


class WebResearchSkill:
    """Capability adapter for the research pipeline (not registered in 12B)."""

    metadata: CapabilityMetadata = WEB_RESEARCH_METADATA

    def __init__(self, service: ResearchService) -> None:
        self._service = service

    def execute(
        self,
        request: CapabilityRequest,
        context: CapabilityContext,
        *,
        progress=None,
    ) -> ResearchResult:
        """Run one bounded research request; kill switch fails closed."""
        result = self._service.research(request.question, request_id=context.request_id)
        if result.status.value == "policy_disabled":
            raise ResearchPolicyError("web research is disabled by policy")
        return result


def research_budget_from_settings(settings: Settings) -> ResearchBudget:
    return ResearchBudget(
        max_search_results=settings.research_max_search_results,
        max_fetch_candidates=settings.research_max_fetch_candidates,
        max_redirects=settings.research_max_redirects,
        fetch_timeout_seconds=settings.research_fetch_timeout_seconds,
        overall_timeout_seconds=settings.research_overall_timeout_seconds,
        max_response_bytes=settings.research_max_response_bytes,
        max_extracted_chars=settings.research_max_extracted_chars,
    )


def build_research_service(settings: Settings) -> ResearchService:
    """Construct the research stack from configuration.

    The search key only ever comes from environment/config; constructing
    without it is a configuration error, not a runtime fallback.
    """
    provider_name = settings.search_provider.lower()
    if provider_name == "tavily":
        provider = TavilySearchProvider(
            settings.search_api_key or "", timeout_seconds=settings.search_timeout_seconds
        )
    else:
        raise ValueError(f"Unknown search provider: {provider_name!r}")
    return ResearchService(
        provider,
        research_budget_from_settings(settings),
        enabled=settings.web_research_enabled,
    )
