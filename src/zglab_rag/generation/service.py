from __future__ import annotations

from collections.abc import Callable, Sequence
from time import perf_counter
from typing import Literal

from pydantic import BaseModel, Field

from zglab_rag.conversation.context import ConversationContext
from zglab_rag.generation.citation import (
    CitationValidation,
    resolve_sources,
    validate_generated_answer,
)
from zglab_rag.generation.context import BuiltContext, ContextBudget, ContextBuilder
from zglab_rag.generation.contracts import (
    GeneratedAnswer,
    GeneratedClaim,
    GenerationDiagnostics,
    GenerationRequest,
    GenerationResult,
    GenerationStatus,
    GroundedAnswer,
    ProgressCallback,
    ProgressStage,
    ProviderResponse,
    Retriever,
)
from zglab_rag.generation.errors import (
    CitationValidationFailure,
    GenerationError,
    InvalidStructuredOutput,
    ProviderFailure,
    RetrievalFailure,
)
from zglab_rag.generation.persona import INSUFFICIENT_EVIDENCE_ANSWER
from zglab_rag.generation.structured import parse_structured_answer
from zglab_rag.retrieval.contracts import RetrievalQuery

RetrievalMode = Literal["vector", "reranked"]


class GroundedGenerationConfig(BaseModel):
    retrieval_mode: RetrievalMode = "vector"
    retrieval_top_k: int = Field(default=5, ge=1, le=8)
    max_repair_attempts: int = Field(default=1, ge=0, le=1)
    budget: ContextBudget = Field(default_factory=ContextBudget)


def render_claims_answer(claims: Sequence[GeneratedClaim]) -> str:
    """Deterministically render the public answer from validated claims only.

    The provider's free-form answer text never reaches the user; every visible
    factual sentence has passed claim-level citation validation.
    """
    return "\n".join(claim.text for claim in claims)


def _safe_progress(progress: ProgressCallback | None) -> Callable[[ProgressStage], None]:
    """Wrap the optional progress observer.

    The observer is best-effort: any exception it raises is swallowed so that
    progress reporting can never change or break the generation workflow.
    """
    if progress is None:
        return lambda _stage: None

    def notify(stage: ProgressStage) -> None:
        try:
            progress(stage)
        except Exception:
            pass

    return notify


def generate_from_context(
    provider,
    config: GroundedGenerationConfig,
    question: str,
    context: BuiltContext,
    *,
    progress: ProgressCallback | None = None,
    started: float | None = None,
    retrieval_mode: str = "vector",
    retrieval_top_k: int = 0,
    retrieval_ms: float = 0.0,
    insufficient_answer: str = INSUFFICIENT_EVIDENCE_ANSWER,
    empty_reason: str = "empty_evidence",
) -> GenerationResult:
    """Shared grounded generation over an already-built evidence context.

    Phase 12C extracted this verbatim from GroundedAnswerService.answer so
    personal retrieval evidence and adapted web evidence run through the
    exact same structured generation + citation validation + repair +
    deterministic rendering pipeline. No provider call happens when the
    context carries no evidence (zero-evidence answers are business
    outcomes, never LLM guesses).
    """
    started = perf_counter() if started is None else started
    notify = _safe_progress(progress)

    if not context.evidence:
        return _build_result(
            question,
            status=GenerationStatus.INSUFFICIENT_EVIDENCE,
            answer=GroundedAnswer(answer=insufficient_answer, insufficient_evidence=True),
            mode=retrieval_mode,
            retrieval_top_k=retrieval_top_k,
            evidence_count=0,
            retrieval_ms=retrieval_ms,
            generation_ms=0.0,
            started=started,
            failure_reason=empty_reason,
        )

    request = GenerationRequest(
        question=question,
        system_prompt=context.system_prompt,
        user_prompt=context.user_prompt,
        allowed_evidence_ids=context.evidence_ids,
    )

    generation_ms = 0.0
    repair_attempts = 0
    last_response: ProviderResponse | None = None
    semantic_error: InvalidStructuredOutput | CitationValidationFailure | None = None
    generated: GeneratedAnswer | None = None
    validation: CitationValidation | None = None
    for attempt in range(config.max_repair_attempts + 1):
        notify(ProgressStage.GENERATING)
        try:
            last_response = provider.generate(request)
        except ProviderFailure as exc:
            return _build_result(
                question,
                status=GenerationStatus.FAILED,
                answer=GroundedAnswer(answer="", insufficient_evidence=True),
                mode=retrieval_mode,
                retrieval_top_k=retrieval_top_k,
                evidence_count=len(context.evidence),
                retrieval_ms=retrieval_ms,
                generation_ms=generation_ms,
                started=started,
                failure_reason=f"ProviderFailure: {exc}",
            )
        generation_ms += last_response.latency_ms
        notify(ProgressStage.VALIDATING)
        try:
            generated = parse_structured_answer(last_response.text)
            validation = validate_generated_answer(generated, context.evidence)
            if not validation.ok:
                raise CitationValidationFailure("; ".join(validation.violations))
            repair_attempts = attempt
            semantic_error = None
            break
        except (InvalidStructuredOutput, CitationValidationFailure) as exc:
            semantic_error = exc
            repair_attempts = attempt + 1
            request = request.model_copy(
                update={
                    "repair_feedback": (
                        f"上一次输出违反规则：{exc}。"
                        "请重新输出一个严格符合 JSON schema 与 citation 规则的结果。"
                    )
                }
            )

    if semantic_error is not None or generated is None or validation is None:
        return _build_result(
            question,
            status=GenerationStatus.INSUFFICIENT_EVIDENCE,
            answer=GroundedAnswer(answer=insufficient_answer, insufficient_evidence=True),
            mode=retrieval_mode,
            retrieval_top_k=retrieval_top_k,
            evidence_count=len(context.evidence),
            retrieval_ms=retrieval_ms,
            generation_ms=generation_ms,
            started=started,
            repair_attempts=config.max_repair_attempts,
            provider_response=last_response,
            failure_reason=f"{type(semantic_error).__name__}: {semantic_error}",
            raw_answer=None if last_response is None else last_response.text,
        )

    if generated.insufficient_evidence:
        grounded = GroundedAnswer(answer=insufficient_answer, insufficient_evidence=True)
        status = GenerationStatus.INSUFFICIENT_EVIDENCE
    else:
        grounded = GroundedAnswer(
            answer=render_claims_answer(generated.claims),
            claims=generated.claims,
            sources=resolve_sources(validation.cited_evidence_ids, context.evidence),
            insufficient_evidence=False,
        )
        status = GenerationStatus.ANSWERED

    return _build_result(
        question,
        status=status,
        answer=grounded,
        mode=retrieval_mode,
        retrieval_top_k=retrieval_top_k,
        evidence_count=len(context.evidence),
        retrieval_ms=retrieval_ms,
        generation_ms=generation_ms,
        started=started,
        repair_attempts=repair_attempts,
        provider_response=last_response,
        raw_answer=generated.answer,
    )


class GroundedAnswerService:
    """Deterministic Question → Retrieval → Context → Provider → Validation workflow.

    This is a fixed orchestration, not an agent loop. Semantic repair is
    limited to one retry describing the violated rules; network retries live in
    the provider layer. The generation core is shared via
    generate_from_context (Phase 12C).
    """

    def __init__(
        self,
        retriever: Retriever,
        provider,
        *,
        context_builder: ContextBuilder | None = None,
        config: GroundedGenerationConfig | None = None,
    ) -> None:
        self.retriever = retriever
        self.provider = provider
        self.config = config or GroundedGenerationConfig()
        self.context_builder = context_builder or ContextBuilder(self.config.budget)

    def answer(
        self,
        question: str,
        *,
        retrieval_mode: RetrievalMode | None = None,
        top_k: int | None = None,
        progress: ProgressCallback | None = None,
        conversation_context: ConversationContext | None = None,
    ) -> GenerationResult:
        mode = retrieval_mode or self.config.retrieval_mode
        retrieval_top_k = top_k or self.config.retrieval_top_k
        notify = _safe_progress(progress)
        started = perf_counter()

        notify(ProgressStage.RETRIEVING)
        retrieval_started = perf_counter()
        try:
            retrieval_query = (
                conversation_context.retrieval_query(question)
                if conversation_context is not None
                else question
            )
            response = self.retriever.retrieve(
                RetrievalQuery(text=retrieval_query, top_k=retrieval_top_k)
            )
        except GenerationError:
            raise
        except Exception as exc:
            raise RetrievalFailure(f"retrieval failed: {exc}") from exc
        retrieval_ms = (perf_counter() - retrieval_started) * 1000

        context = self.context_builder.build(
            question, response.results, conversation_context=conversation_context
        )
        if not context.evidence:
            # Personal-specific early exit kept verbatim (failure_reason and
            # wording are part of the frozen Phase 8 behavior).
            return _build_result(
                question,
                status=GenerationStatus.INSUFFICIENT_EVIDENCE,
                answer=GroundedAnswer(
                    answer=INSUFFICIENT_EVIDENCE_ANSWER, insufficient_evidence=True
                ),
                mode=mode,
                retrieval_top_k=retrieval_top_k,
                evidence_count=0,
                retrieval_ms=retrieval_ms,
                generation_ms=0.0,
                started=started,
                failure_reason="empty_retrieval",
            )

        return generate_from_context(
            self.provider,
            self.config,
            question,
            context,
            progress=progress,
            started=started,
            retrieval_mode=mode,
            retrieval_top_k=retrieval_top_k,
            retrieval_ms=retrieval_ms,
            empty_reason="empty_retrieval",
        )


def _build_result(
    question: str,
    *,
    status: GenerationStatus,
    answer: GroundedAnswer,
    mode: str,
    retrieval_top_k: int,
    evidence_count: int,
    retrieval_ms: float,
    generation_ms: float,
    started: float,
    repair_attempts: int = 0,
    provider_response: ProviderResponse | None = None,
    failure_reason: str | None = None,
    raw_answer: str | None = None,
) -> GenerationResult:
    return GenerationResult(
        status=status,
        question=question,
        answer=answer,
        diagnostics=GenerationDiagnostics(
            retrieval_mode=mode,
            retrieval_top_k=retrieval_top_k,
            evidence_count=evidence_count,
            retrieval_latency_ms=retrieval_ms,
            provider=None if provider_response is None else provider_response.provider,
            model=None if provider_response is None else provider_response.model,
            generation_latency_ms=generation_ms,
            total_latency_ms=(perf_counter() - started) * 1000,
            repair_attempts=repair_attempts,
            input_tokens=(
                None if provider_response is None else provider_response.usage.input_tokens
            ),
            output_tokens=(
                None if provider_response is None else provider_response.usage.output_tokens
            ),
        ),
        failure_reason=failure_reason,
        raw_answer=raw_answer,
    )
