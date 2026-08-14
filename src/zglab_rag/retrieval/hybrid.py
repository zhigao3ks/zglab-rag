from __future__ import annotations

from collections.abc import Sequence
from time import perf_counter

from pydantic import BaseModel

from zglab_rag.retrieval.config import HybridRetrievalConfig
from zglab_rag.retrieval.contracts import RetrievalFilter, RetrievalQuery, RetrievalResult
from zglab_rag.retrieval.lexical import LexicalRetriever
from zglab_rag.retrieval.vector import VectorRetriever


class HybridDiagnostics(BaseModel):
    vector_retrieval_latency_ms: float
    lexical_retrieval_latency_ms: float
    fusion_latency_ms: float
    total_retrieval_latency_ms: float
    vector_candidate_count: int
    lexical_candidate_count: int
    fused_candidate_count: int
    returned_count: int
    top_k: int
    filters: RetrievalFilter
    lexical_applicable: bool


class HybridResponse(BaseModel):
    results: list[RetrievalResult]
    diagnostics: HybridDiagnostics


def reciprocal_rank_fusion(
    vector_results: Sequence[RetrievalResult],
    lexical_results: Sequence[RetrievalResult],
    *,
    rrf_k: int = 60,
    vector_weight: float = 1.0,
    lexical_weight: float = 1.0,
) -> list[RetrievalResult]:
    if rrf_k <= 0:
        raise ValueError("rrf_k must be positive")
    by_id: dict[str, RetrievalResult] = {}
    vector_ranks: dict[str, int] = {}
    lexical_ranks: dict[str, int] = {}
    for rank, result in enumerate(vector_results, start=1):
        by_id[result.chunk_id] = result
        vector_ranks[result.chunk_id] = rank
    for rank, result in enumerate(lexical_results, start=1):
        by_id.setdefault(result.chunk_id, result)
        lexical_ranks[result.chunk_id] = rank

    fused: list[tuple[float, int, str, RetrievalResult]] = []
    for chunk_id, source in by_id.items():
        vector_rank = vector_ranks.get(chunk_id)
        lexical_rank = lexical_ranks.get(chunk_id)
        score = 0.0
        if vector_rank is not None:
            score += vector_weight / (rrf_k + vector_rank)
        if lexical_rank is not None:
            score += lexical_weight / (rrf_k + lexical_rank)
        best_rank = min(rank for rank in (vector_rank, lexical_rank) if rank is not None)
        result = source.model_copy(
            update={
                "rank": 1,
                "score": score,
                "distance": None,
                "retriever": "hybrid",
                "vector_rank": vector_rank,
                "lexical_rank": lexical_rank,
                "rrf_score": score,
                "raw_bm25": next(
                    (
                        item.raw_bm25
                        for item in lexical_results
                        if item.chunk_id == chunk_id
                    ),
                    None,
                ),
            }
        )
        fused.append((score, best_rank, chunk_id, result))
    fused.sort(key=lambda item: (-item[0], item[1], item[2]))
    return [item[3].model_copy(update={"rank": rank}) for rank, item in enumerate(fused, 1)]


class HybridRetriever:
    def __init__(
        self,
        vector_retriever: VectorRetriever,
        lexical_retriever: LexicalRetriever,
        *,
        config: HybridRetrievalConfig | None = None,
    ) -> None:
        self.vector_retriever = vector_retriever
        self.lexical_retriever = lexical_retriever
        self.config = config or HybridRetrievalConfig()

    @property
    def corpus_size(self) -> int:
        return max(self.vector_retriever.corpus_size, self.lexical_retriever.corpus_size)

    def retrieve(self, query: RetrievalQuery) -> HybridResponse:
        started = perf_counter()
        top_k = self.config.validate_top_k(query.top_k)
        vector_k = max(top_k, self.config.vector_candidate_k)
        lexical_k = max(top_k, self.config.lexical_candidate_k)

        vector_started = perf_counter()
        vector_response = self.vector_retriever.retrieve(
            query.model_copy(update={"top_k": vector_k})
        )
        vector_ms = (perf_counter() - vector_started) * 1000
        lexical_started = perf_counter()
        lexical_response = self.lexical_retriever.retrieve(
            query.model_copy(update={"top_k": lexical_k})
        )
        lexical_ms = (perf_counter() - lexical_started) * 1000
        fusion_started = perf_counter()
        fused = reciprocal_rank_fusion(
            vector_response.results,
            lexical_response.results,
            rrf_k=self.config.rrf_k,
            vector_weight=self.config.vector_weight,
            lexical_weight=self.config.lexical_weight,
        )
        fusion_ms = (perf_counter() - fusion_started) * 1000
        results = fused[:top_k]
        return HybridResponse(
            results=results,
            diagnostics=HybridDiagnostics(
                vector_retrieval_latency_ms=vector_ms,
                lexical_retrieval_latency_ms=lexical_ms,
                fusion_latency_ms=fusion_ms,
                total_retrieval_latency_ms=(perf_counter() - started) * 1000,
                vector_candidate_count=len(vector_response.results),
                lexical_candidate_count=len(lexical_response.results),
                fused_candidate_count=len(fused),
                returned_count=len(results),
                top_k=top_k,
                filters=query.filters,
                lexical_applicable=lexical_response.diagnostics.lexical_applicable,
            ),
        )
