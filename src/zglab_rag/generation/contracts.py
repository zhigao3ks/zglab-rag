from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, Field, field_validator, model_validator

from zglab_rag.domain.models import Visibility
from zglab_rag.retrieval.contracts import RetrievalQuery, RetrievalResponse


class GenerationStatus(StrEnum):
    ANSWERED = "answered"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    FAILED = "failed"


class EvidenceOrigin(StrEnum):
    """Where generation evidence comes from (Phase 12C).

    PERSONAL and WEB evidence share the grounded generation pipeline but
    are never conflated: web pages are untrusted external data and can
    never masquerade as personal knowledge chunks.
    """

    PERSONAL = "personal"
    WEB = "web"


class ProgressStage(StrEnum):
    """Execution stages reported by the optional progress observer.

    Stages only name the current phase; they never carry evidence content,
    provider details or diagnostics. The generation domain knows nothing
    about SSE/FastAPI/asyncio: consumers receive a plain abstract callback.
    """

    RETRIEVING = "retrieving"
    # Phase 12D: emitted only on the web research product path (search +
    # safe fetch + extraction); the personal path never emits it.
    RESEARCHING = "researching"
    # Phase 14D Agent public lifecycle names. They carry no plan, evidence,
    # tool input/output or reasoning details.
    PLANNING = "planning"
    EXECUTING = "executing"
    SYNTHESIZING = "synthesizing"
    GENERATING = "generating"
    VALIDATING = "validating"


ProgressCallback = Callable[[ProgressStage], None]


class EvidenceItem(BaseModel):
    """One evidence chunk promoted into generation context with provenance.

    The short evidence_id is only valid inside a single request; the LLM never
    sees chunk_id, source_path, revision or score.

    Phase 12C additive evolution: ``origin`` distinguishes PERSONAL knowledge
    chunks from WEB evidence. Web items carry ``url``/``domain`` locators and
    leave the personal chunk identifiers (chunk_id / document_id / source_id)
    as None — a web page is never faked into a knowledge.db chunk.
    """

    evidence_id: str = Field(pattern=r"^E[1-9][0-9]*$")
    chunk_id: str | None = None
    document_id: str | None = None
    source_id: str | None = None
    source_path: str
    title: str
    section_path: list[str] = Field(default_factory=list)
    content: str
    revision: str | None = None
    visibility: Visibility = Visibility.PUBLIC
    rank: int = Field(gt=0)
    score: float
    origin: EvidenceOrigin = EvidenceOrigin.PERSONAL
    url: str | None = None
    domain: str | None = None

    @model_validator(mode="after")
    def enforce_public_evidence(self) -> EvidenceItem:
        if self.visibility != Visibility.PUBLIC:
            raise ValueError("generation evidence must be public")
        return self


class GeneratedClaim(BaseModel):
    """One factual claim from the LLM with evidence citations."""

    text: str = Field(min_length=1)
    citations: list[str] = Field(default_factory=list)


class GeneratedAnswer(BaseModel):
    """Structured generation output contract expected from the provider."""

    answer: str = Field(min_length=1)
    claims: list[GeneratedClaim] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)
    insufficient_evidence: bool = False

    @field_validator("answer")
    @classmethod
    def strip_answer(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("answer must not be blank")
        return stripped


class AnswerSource(BaseModel):
    """Public citation target resolved from a short evidence id.

    Phase 12C additive evolution: ``origin`` plus optional ``url``/``domain``
    let a source describe WEB evidence without breaking the frozen personal
    contract; personal sources keep origin=personal and url=None.
    """

    evidence_id: str
    source_id: str | None = None
    document_id: str | None = None
    chunk_id: str | None = None
    title: str
    source_path: str
    section_path: list[str] = Field(default_factory=list)
    source_url: str | None = None
    score: float | None = None
    origin: EvidenceOrigin = EvidenceOrigin.PERSONAL
    url: str | None = None
    domain: str | None = None


class GroundedAnswer(BaseModel):
    answer: str
    claims: list[GeneratedClaim] = Field(default_factory=list)
    sources: list[AnswerSource] = Field(default_factory=list)
    insufficient_evidence: bool = False


class GenerationRequest(BaseModel):
    question: str = Field(min_length=1)
    system_prompt: str = Field(min_length=1)
    user_prompt: str = Field(min_length=1)
    allowed_evidence_ids: tuple[str, ...] = ()
    repair_feedback: str | None = None


class ProviderUsage(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None


class ProviderResponse(BaseModel):
    provider: str
    model: str
    text: str
    latency_ms: float
    usage: ProviderUsage = Field(default_factory=ProviderUsage)
    raw: dict | None = None


class GenerationProvider(Protocol):
    name: str
    model: str

    def generate(self, request: GenerationRequest) -> ProviderResponse: ...


class Retriever(Protocol):
    def retrieve(self, query: RetrievalQuery) -> RetrievalResponse: ...


class GenerationDiagnostics(BaseModel):
    retrieval_mode: str
    retrieval_top_k: int
    evidence_count: int
    retrieval_latency_ms: float
    provider: str | None = None
    model: str | None = None
    generation_latency_ms: float = 0.0
    total_latency_ms: float
    repair_attempts: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None


class GenerationResult(BaseModel):
    status: GenerationStatus
    question: str
    answer: GroundedAnswer
    diagnostics: GenerationDiagnostics
    failure_reason: str | None = None
    # Provider 原始 answer 文本，仅作内部/debug 信息；最终公开回答由
    # validated claims 确定性渲染，raw_answer 不参与对外输出。
    raw_answer: str | None = None
