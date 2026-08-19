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


class ProgressStage(StrEnum):
    """Execution stages reported by the optional progress observer.

    Stages only name the current phase; they never carry evidence content,
    provider details or diagnostics. The generation domain knows nothing
    about SSE/FastAPI/asyncio: consumers receive a plain abstract callback.
    """

    RETRIEVING = "retrieving"
    GENERATING = "generating"
    VALIDATING = "validating"


ProgressCallback = Callable[[ProgressStage], None]


class EvidenceItem(BaseModel):
    """One retrieved chunk promoted into generation context with provenance.

    The short evidence_id is only valid inside a single request; the LLM never
    sees chunk_id, source_path, revision or score.
    """

    evidence_id: str = Field(pattern=r"^E[1-9][0-9]*$")
    chunk_id: str
    document_id: str
    source_id: str
    source_path: str
    title: str
    section_path: list[str] = Field(default_factory=list)
    content: str
    revision: str | None = None
    visibility: Visibility = Visibility.PUBLIC
    rank: int = Field(gt=0)
    score: float

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
    """Public citation target resolved from a short evidence id."""

    evidence_id: str
    source_id: str
    document_id: str
    chunk_id: str
    title: str
    source_path: str
    section_path: list[str] = Field(default_factory=list)
    source_url: str | None = None
    score: float | None = None


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
