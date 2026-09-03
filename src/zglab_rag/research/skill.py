"""WebResearchSkill & runtime factory (Phase 12B research, 12C answering).

The skill is a Capability-shaped adapter over ResearchService:

    execute(): CapabilityRequest -> ResearchService.research() -> ResearchResult

Phase 12C adds an answering path that keeps the layering strict:

    answer(): research -> adapt ExternalEvidence -> build_web_context
              -> generate_from_context (shared Phase 8 pipeline)
              -> CapabilityResult

ResearchService itself never calls an LLM. The skill still does NOT fall
back to PersonalKnowledgeSkill, route or plan anything.

Phase 12B deliberately does NOT register ``web_research`` in the runtime
CapabilityRegistry and does NOT expose any HTTP endpoint: registry
presence is not API reachability, and product integration belongs to 12D.
The skill exists so the pipeline is callable, testable and kill-switched.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum

from zglab_rag.capabilities.contracts import (
    CapabilityContext,
    CapabilityMetadata,
    CapabilityRequest,
    CapabilityResult,
    CapabilityStatus,
    EvidenceOrigin,
)
from zglab_rag.config import Settings
from zglab_rag.conversation.models import SessionResourceType
from zglab_rag.conversation.resources import WEB_RESOURCE_VERSION, resource_key, web_resource_key
from zglab_rag.generation.context import ContextBudget, build_web_context
from zglab_rag.generation.contracts import (
    GenerationDiagnostics,
    GenerationResult,
    GenerationStatus,
    GroundedAnswer,
    ProgressStage,
)
from zglab_rag.generation.persona import WEB_INSUFFICIENT_EVIDENCE_ANSWER
from zglab_rag.generation.service import GroundedGenerationConfig, generate_from_context
from zglab_rag.research.contracts import (
    WEB_RESEARCH_CAPABILITY_ID,
    ExternalEvidence,
    ResearchBudget,
    ResearchPolicyError,
    ResearchResult,
    ResearchStatus,
)
from zglab_rag.research.search import TavilySearchProvider
from zglab_rag.research.service import ResearchService
from zglab_rag.research.web_adapter import adapt_external_evidence

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


class ResearchProgressStage(StrEnum):
    """Internal request-scoped progress for the web answering path.

    Never mapped to the public SSE contract: 12D freezes any public
    "researching" stages. Best-effort like the generation observer.
    """

    SEARCHING = "searching"
    FETCHING = "fetching"
    EXTRACTING = "extracting"
    GENERATING = "generating"
    VALIDATING = "validating"


ResearchProgressCallback = Callable[[ResearchProgressStage], None]


def _safe_research_progress(
    progress: ResearchProgressCallback | None,
) -> Callable[[ResearchProgressStage], None]:
    if progress is None:
        return lambda _stage: None

    def notify(stage: ResearchProgressStage) -> None:
        try:
            progress(stage)
        except Exception:
            pass

    return notify


class WebResearchSkill:
    """Capability adapter for the research pipeline (not registered in 12B)."""

    metadata: CapabilityMetadata = WEB_RESEARCH_METADATA

    def __init__(
        self,
        service: ResearchService,
        *,
        provider=None,
        generation_config: GroundedGenerationConfig | None = None,
        reuse_provider: str = "",
        reuse_config_fingerprint: str = "",
        reuse_ttl_seconds: int = 300,
    ) -> None:
        self._service = service
        self._provider = provider
        self._generation_config = generation_config or GroundedGenerationConfig()
        self._reuse_provider = reuse_provider
        self._reuse_config_fingerprint = reuse_config_fingerprint
        self._reuse_ttl_seconds = reuse_ttl_seconds

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

    def answer(
        self,
        request: CapabilityRequest,
        context: CapabilityContext,
        *,
        progress: ResearchProgressCallback | None = None,
    ) -> CapabilityResult:
        """Research then ground an answer over fetched web evidence only.

        Layering rules enforced here:
        - kill switch fails closed before any work;
        - zero usable evidence never triggers an LLM call (business
          outcome, not a guess);
        - research infrastructure failures stay FAILED with their reason;
        - generation reuses the shared Phase 8 pipeline, so citation
          validation and claims rendering are identical to personal.
        """
        notify = _safe_research_progress(progress)
        question = request.question.strip()
        research_query = (
            context.conversation_context.retrieval_query(
                question,
                max_chars=self._generation_config.retrieval_query_max_chars,
                max_bytes=self._generation_config.retrieval_query_max_bytes,
            )
            if context.conversation_context is not None
            else question
        )

        # Policy is checked before lookup: a previously stored web resource
        # cannot bypass the web kill switch or the API's auth/quota gates.
        if not self._service.enabled:
            research = self._service.research(research_query, request_id=context.request_id)
        else:
            cache_key = web_resource_key(
                query=research_query,
                provider=self._reuse_provider,
                config=self._reuse_config_fingerprint,
            )
            cached_evidence = None
            if context.session_workspace is not None:
                cached = context.session_workspace.get(
                    SessionResourceType.WEB_EVIDENCE,
                    cache_key,
                    producer_fingerprint=self._reuse_config_fingerprint,
                )
                if cached is not None:
                    try:
                        cached_evidence = [
                            ExternalEvidence.model_validate(value).model_copy(
                                update={"evidence_id": f"W{index}"}
                            )
                            for index, value in enumerate(cached["evidence"], start=1)
                        ]
                        if not cached_evidence:
                            raise ValueError("empty evidence")
                    except (KeyError, TypeError, ValueError):
                        cached_evidence = None
            if cached_evidence is None:
                notify(ResearchProgressStage.SEARCHING)
                notify(ResearchProgressStage.FETCHING)
                notify(ResearchProgressStage.EXTRACTING)
                research = self._service.research(research_query, request_id=context.request_id)
                if (
                    context.session_workspace is not None
                    and research.status == ResearchStatus.SUCCESS
                    and research.evidence
                ):
                    context.session_workspace.put(
                        SessionResourceType.WEB_EVIDENCE,
                        cache_key,
                        payload={
                            "version": WEB_RESOURCE_VERSION,
                            "evidence": [
                                item.model_dump(mode="json") for item in research.evidence
                            ],
                        },
                        provenance={
                            "query_fingerprint": resource_key({"query": research_query}),
                            "evidence_provenance": [
                                {
                                    "url": item.url,
                                    "canonical_url": item.canonical_url,
                                    "domain": item.domain,
                                    "retrieved_at": item.retrieved_at.isoformat(),
                                    "search_result_url": item.search_result_url,
                                    "redirect_chain": list(item.redirect_chain),
                                }
                                for item in research.evidence
                            ],
                        },
                        producer_fingerprint=self._reuse_config_fingerprint,
                        source_request_id=context.request_id,
                        ttl_seconds=self._reuse_ttl_seconds,
                    )
            else:
                research = ResearchResult(status=ResearchStatus.SUCCESS, evidence=cached_evidence)

        if research.status == ResearchStatus.POLICY_DISABLED:
            raise ResearchPolicyError("web research is disabled by policy")

        if research.status != ResearchStatus.SUCCESS or not research.evidence:
            if research.status in (
                ResearchStatus.NO_RESULTS,
                ResearchStatus.NO_USABLE_EVIDENCE,
            ):
                return self._insufficient_result(question, research.status)
            return CapabilityResult(
                capability_id=WEB_RESEARCH_CAPABILITY_ID,
                status=CapabilityStatus.FAILED,
                origin=EvidenceOrigin.WEB,
                generation=None,
                failure_reason=f"research_{research.status.value}",
            )

        if self._provider is None:
            raise RuntimeError(
                "WebResearchSkill.answer requires a generation provider; "
                "construct it via build_web_research_skill"
            )

        evidence_items = adapt_external_evidence(
            research.evidence,
            max_items=self._generation_config.budget.max_evidence_items,
        )
        built = build_web_context(
            question,
            evidence_items,
            self._generation_config.budget,
            conversation_context=context.conversation_context,
        )

        def generation_progress(stage: ProgressStage) -> None:
            if stage == ProgressStage.GENERATING:
                notify(ResearchProgressStage.GENERATING)
            elif stage == ProgressStage.VALIDATING:
                notify(ResearchProgressStage.VALIDATING)

        generation = generate_from_context(
            self._provider,
            self._generation_config,
            question,
            built,
            progress=generation_progress,
            retrieval_mode="web_research",
            retrieval_top_k=len(evidence_items),
            insufficient_answer=WEB_INSUFFICIENT_EVIDENCE_ANSWER,
            empty_reason="no_usable_evidence",
        )
        return CapabilityResult(
            capability_id=WEB_RESEARCH_CAPABILITY_ID,
            status=CapabilityStatus.from_generation_status(generation.status),
            origin=EvidenceOrigin.WEB,
            generation=generation,
        )

    def _insufficient_result(
        self, question: str, research_status: ResearchStatus
    ) -> CapabilityResult:
        """Business outcome when research found nothing usable: no LLM call."""
        generation = GenerationResult(
            status=GenerationStatus.INSUFFICIENT_EVIDENCE,
            question=question,
            answer=GroundedAnswer(
                answer=WEB_INSUFFICIENT_EVIDENCE_ANSWER, insufficient_evidence=True
            ),
            diagnostics=GenerationDiagnostics(
                retrieval_mode="web_research",
                retrieval_top_k=0,
                evidence_count=0,
                retrieval_latency_ms=0.0,
                provider=None,
                model=None,
                generation_latency_ms=0.0,
                total_latency_ms=0.0,
                repair_attempts=0,
                input_tokens=None,
                output_tokens=None,
            ),
            failure_reason=research_status.value,
        )
        return CapabilityResult(
            capability_id=WEB_RESEARCH_CAPABILITY_ID,
            status=CapabilityStatus.INSUFFICIENT_EVIDENCE,
            origin=EvidenceOrigin.WEB,
            generation=generation,
            failure_reason=research_status.value,
        )


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


def build_web_research_skill(settings: Settings, llm_provider) -> WebResearchSkill:
    """Construct the answering-capable web skill from configuration.

    Only call this when web research is actually enabled: startup must not
    depend on SEARCH_API_KEY while the kill switch is off, so ProductionRuntime
    builds this lazily.
    """
    reuse_config = resource_key(
        {
            "provider": settings.search_provider.lower(),
            "budget": {
                "max_search_results": settings.research_max_search_results,
                "max_fetch_candidates": settings.research_max_fetch_candidates,
                "max_redirects": settings.research_max_redirects,
                "fetch_timeout_seconds": settings.research_fetch_timeout_seconds,
                "overall_timeout_seconds": settings.research_overall_timeout_seconds,
                "max_response_bytes": settings.research_max_response_bytes,
                "max_extracted_chars": settings.research_max_extracted_chars,
            },
            "generation": {
                "top_k": settings.generation_retrieval_top_k,
                "context_chars": settings.generation_max_context_chars,
            },
        }
    )
    return WebResearchSkill(
        build_research_service(settings),
        provider=llm_provider,
        generation_config=GroundedGenerationConfig(
            retrieval_top_k=settings.generation_retrieval_top_k,
            budget=ContextBudget(
                max_evidence_items=settings.generation_max_evidence_items,
                max_context_chars=settings.generation_max_context_chars,
            ),
            retrieval_query_max_chars=settings.conversation_context_retrieval_query_max_chars,
            retrieval_query_max_bytes=settings.conversation_context_retrieval_query_max_bytes,
        ),
        reuse_provider=settings.search_provider.lower(),
        reuse_config_fingerprint=reuse_config,
        reuse_ttl_seconds=settings.session_web_ttl_seconds,
    )
