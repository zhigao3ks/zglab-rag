from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from zglab_rag.domain.models import RetrievedChunk, Visibility


class VectorRetriever(Protocol):
    def search(
        self,
        query: str,
        *,
        top_k: int,
        visibility: Visibility = Visibility.PUBLIC,
    ) -> list[RetrievedChunk]: ...


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
