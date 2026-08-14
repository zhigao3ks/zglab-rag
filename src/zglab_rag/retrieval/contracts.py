from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, Protocol

from pydantic import BaseModel, Field, model_validator

from zglab_rag.domain.models import RetrievedChunk, Scope, Visibility


class RetrievalFilter(BaseModel):
    """Relational constraints applied before candidate metadata is exposed."""

    visibility: Visibility = Visibility.PUBLIC
    source_ids: tuple[str, ...] = ()
    scopes: tuple[Scope, ...] = ()

    @model_validator(mode="after")
    def enforce_public_baseline(self) -> RetrievalFilter:
        if self.visibility != Visibility.PUBLIC:
            raise ValueError("Vector retrieval is public-only until authenticated mode exists")
        self.source_ids = tuple(sorted(set(self.source_ids)))
        self.scopes = tuple(sorted(set(self.scopes), key=str))
        return self


class RetrievalQuery(BaseModel):
    text: str = Field(min_length=1)
    top_k: int | None = None
    filters: RetrievalFilter = Field(default_factory=RetrievalFilter)


class RetrievalResult(BaseModel):
    chunk_id: str
    document_id: str
    source_id: str
    source_path: str
    scope: Scope
    title: str
    section_path: list[str]
    content: str
    visibility: Visibility
    revision: str | None
    rank: int = Field(gt=0)
    score: float
    distance: float
    retriever: Literal["vector"] = "vector"


class RetrievalDiagnostics(BaseModel):
    query_embedding_latency_ms: float
    vector_search_latency_ms: float
    total_retrieval_latency_ms: float
    candidate_count: int
    filtered_count: int
    returned_count: int
    top_k: int
    filters: RetrievalFilter


class RetrievalResponse(BaseModel):
    results: list[RetrievalResult]
    diagnostics: RetrievalDiagnostics


class Retriever(Protocol):
    def retrieve(self, query: RetrievalQuery) -> RetrievalResponse: ...


class LexicalRetriever(Protocol):
    def search(
        self,
        query: str,
        *,
        top_k: int,
        visibility: Visibility = Visibility.PUBLIC,
    ) -> list[RetrievedChunk]: ...


class HybridFusion(Protocol):
    def fuse(
        self,
        vector_results: Sequence[RetrievedChunk],
        lexical_results: Sequence[RetrievedChunk],
        *,
        top_k: int,
    ) -> list[RetrievedChunk]: ...


class Reranker(Protocol):
    def rerank(
        self,
        query: str,
        candidates: Sequence[RetrievedChunk],
        *,
        top_k: int,
    ) -> list[RetrievedChunk]: ...
