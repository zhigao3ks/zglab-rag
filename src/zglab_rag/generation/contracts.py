from __future__ import annotations

from typing import Protocol, Sequence

from pydantic import BaseModel, Field

from zglab_rag.domain.models import RetrievedChunk


class AnswerSource(BaseModel):
    source_id: str
    document_id: str
    title: str
    source_url: str | None = None
    section_path: list[str] = Field(default_factory=list)


class GroundedAnswer(BaseModel):
    answer: str
    sources: list[AnswerSource] = Field(default_factory=list)
    insufficient_evidence: bool = False


class ContextBuilder(Protocol):
    def build(self, question: str, evidence: Sequence[RetrievedChunk]) -> str: ...


class Generator(Protocol):
    def generate(self, question: str, evidence: Sequence[RetrievedChunk]) -> GroundedAnswer: ...
