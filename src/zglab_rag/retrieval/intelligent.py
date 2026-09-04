from __future__ import annotations

from collections.abc import Sequence
from time import perf_counter

from pydantic import BaseModel

from zglab_rag.retrieval.config import IntelligentRetrievalConfig
from zglab_rag.retrieval.contracts import RetrievalFilter, RetrievalQuery, RetrievalResult


class IntelligentDiagnostics(BaseModel):
    hybrid_candidate_count: int
    hierarchical_candidate_count: int
    graph_candidate_count: int
    fusion_candidate_count: int
    returned_count: int
    fusion_latency_ms: float
    total_retrieval_latency_ms: float
    top_k: int
    filters: RetrievalFilter


class IntelligentResponse(BaseModel):
    results: list[RetrievalResult]
    diagnostics: IntelligentDiagnostics


def intelligent_rrf(
    hybrid: Sequence[RetrievalResult],
    hierarchical: Sequence[RetrievalResult],
    graph: Sequence[RetrievalResult],
    *,
    config: IntelligentRetrievalConfig,
) -> list[RetrievalResult]:
    by_id: dict[str, RetrievalResult] = {}
    ranks: dict[str, dict[str, int]] = {}
    for name, values in (
        ("hybrid", hybrid),
        ("hierarchical", hierarchical),
        ("graph", graph),
    ):
        for rank, result in enumerate(values, start=1):
            by_id.setdefault(result.chunk_id, result)
            ranks.setdefault(result.chunk_id, {})[name] = rank
    weights = {
        "hybrid": config.hybrid_weight,
        "hierarchical": config.hierarchical_weight,
        "graph": config.graph_weight,
    }
    fused: list[tuple[float, int, str, RetrievalResult]] = []
    for chunk_id, result in by_id.items():
        component = ranks[chunk_id]
        score = sum(
            weights[name] / (config.rrf_k + rank) for name, rank in component.items()
        )
        fused.append(
            (
                score,
                min(component.values()),
                chunk_id,
                result.model_copy(
                    update={
                        "rank": 1,
                        "retriever": "intelligent",
                        "score": score,
                        "distance": None,
                        "rrf_score": score,
                        "fusion_score": score,
                        "hybrid_rank": component.get("hybrid"),
                        "hierarchical_rank": component.get("hierarchical"),
                        "graph_rank": component.get("graph"),
                    }
                ),
            )
        )
    fused.sort(key=lambda item: (-item[0], item[1], item[2]))
    return [
        item[3].model_copy(update={"rank": rank})
        for rank, item in enumerate(fused, start=1)
    ]


class IntelligentRetriever:
    """One Hybrid (and therefore one vector query) plus two SQL-only routes."""

    def __init__(
        self,
        hybrid,
        hierarchical,
        graph,
        *,
        config: IntelligentRetrievalConfig | None = None,
    ) -> None:
        self.hybrid = hybrid
        self.hierarchical = hierarchical
        self.graph = graph
        self.config = config or IntelligentRetrievalConfig()

    @property
    def corpus_size(self) -> int:
        return max(
            self.hybrid.corpus_size,
            self.hierarchical.corpus_size,
            self.graph.corpus_size,
        )

    def retrieve(self, query: RetrievalQuery) -> IntelligentResponse:
        started = perf_counter()
        top_k = self.config.validate_top_k(query.top_k)
        component_query = query.model_copy(update={"top_k": top_k})
        hybrid = self.hybrid.retrieve(component_query).results
        hierarchical = self.hierarchical.retrieve(component_query).results
        graph = self.graph.retrieve(component_query).results
        fusion_started = perf_counter()
        fused = intelligent_rrf(
            hybrid, hierarchical, graph, config=self.config
        )
        fusion_ms = (perf_counter() - fusion_started) * 1000
        results = fused[:top_k]
        return IntelligentResponse(
            results=results,
            diagnostics=IntelligentDiagnostics(
                hybrid_candidate_count=len(hybrid),
                hierarchical_candidate_count=len(hierarchical),
                graph_candidate_count=len(graph),
                fusion_candidate_count=len(fused),
                returned_count=len(results),
                fusion_latency_ms=fusion_ms,
                total_retrieval_latency_ms=(perf_counter() - started) * 1000,
                top_k=top_k,
                filters=query.filters,
            ),
        )
