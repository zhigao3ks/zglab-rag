from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class VectorRetrievalConfig:
    default_top_k: int = 5
    max_top_k: int = 50
    candidate_factor: int = 4
    minimum_candidate_k: int = 20
    maximum_candidate_k: int = 1000

    def __post_init__(self) -> None:
        if self.default_top_k <= 0:
            raise ValueError("default_top_k must be positive")
        if self.max_top_k < self.default_top_k:
            raise ValueError("max_top_k must be at least default_top_k")
        if self.candidate_factor < 2:
            raise ValueError("candidate_factor must be at least 2")
        if self.minimum_candidate_k <= 0:
            raise ValueError("minimum_candidate_k must be positive")
        if self.maximum_candidate_k < self.minimum_candidate_k:
            raise ValueError("maximum_candidate_k must be at least minimum_candidate_k")

    def validate_top_k(self, requested: int | None) -> int:
        top_k = self.default_top_k if requested is None else requested
        if top_k <= 0:
            raise ValueError("top_k must be positive")
        if top_k > self.max_top_k:
            raise ValueError(f"top_k must not exceed {self.max_top_k}")
        return top_k


@dataclass(frozen=True, slots=True)
class HybridRetrievalConfig:
    default_top_k: int = 5
    max_top_k: int = 50
    vector_candidate_k: int = 50
    lexical_candidate_k: int = 50
    rrf_k: int = 60
    vector_weight: float = 1.0
    lexical_weight: float = 1.0

    def __post_init__(self) -> None:
        if self.default_top_k <= 0:
            raise ValueError("default_top_k must be positive")
        if self.max_top_k < self.default_top_k:
            raise ValueError("max_top_k must be at least default_top_k")
        if min(self.vector_candidate_k, self.lexical_candidate_k) <= 0:
            raise ValueError("candidate pool sizes must be positive")
        if self.rrf_k <= 0:
            raise ValueError("rrf_k must be positive")
        if self.vector_weight < 0 or self.lexical_weight < 0:
            raise ValueError("RRF weights must be non-negative")
        if self.vector_weight == 0 and self.lexical_weight == 0:
            raise ValueError("at least one RRF weight must be positive")

    def validate_top_k(self, requested: int | None) -> int:
        top_k = self.default_top_k if requested is None else requested
        if top_k <= 0:
            raise ValueError("top_k must be positive")
        if top_k > self.max_top_k:
            raise ValueError(f"top_k must not exceed {self.max_top_k}")
        return top_k


@dataclass(frozen=True, slots=True)
class HierarchicalRetrievalConfig:
    default_top_k: int = 5
    max_top_k: int = 50
    document_candidates: int = 8
    section_candidates: int = 12
    chunk_candidates: int = 30

    def __post_init__(self) -> None:
        if not 0 < self.default_top_k <= self.max_top_k:
            raise ValueError("invalid hierarchical top-k bounds")
        if min(self.document_candidates, self.section_candidates, self.chunk_candidates) <= 0:
            raise ValueError("hierarchical candidate limits must be positive")

    def validate_top_k(self, requested: int | None) -> int:
        top_k = self.default_top_k if requested is None else requested
        if not 0 < top_k <= self.max_top_k:
            raise ValueError("top_k exceeds hierarchical bounds")
        return top_k


@dataclass(frozen=True, slots=True)
class GraphRetrievalConfig:
    default_top_k: int = 5
    max_top_k: int = 50
    max_start_nodes: int = 8
    max_hops: int = 2
    max_nodes: int = 24
    max_edges: int = 64
    max_candidate_documents: int = 12

    def __post_init__(self) -> None:
        values = (
            self.max_start_nodes,
            self.max_hops,
            self.max_nodes,
            self.max_edges,
            self.max_candidate_documents,
        )
        if not 0 < self.default_top_k <= self.max_top_k or min(values) <= 0:
            raise ValueError("invalid graph retrieval bounds")

    def validate_top_k(self, requested: int | None) -> int:
        top_k = self.default_top_k if requested is None else requested
        if not 0 < top_k <= self.max_top_k:
            raise ValueError("top_k exceeds graph bounds")
        return top_k


@dataclass(frozen=True, slots=True)
class IntelligentRetrievalConfig:
    default_top_k: int = 5
    max_top_k: int = 50
    hybrid_weight: float = 1.0
    hierarchical_weight: float = 1.0
    graph_weight: float = 1.0
    rrf_k: int = 60

    def __post_init__(self) -> None:
        if not 0 < self.default_top_k <= self.max_top_k:
            raise ValueError("invalid intelligent top-k bounds")
        if min(self.hybrid_weight, self.hierarchical_weight, self.graph_weight) < 0:
            raise ValueError("fusion weights must be non-negative")
        if not any((self.hybrid_weight, self.hierarchical_weight, self.graph_weight)):
            raise ValueError("at least one fusion weight is required")
        if self.rrf_k <= 0:
            raise ValueError("rrf_k must be positive")

    def validate_top_k(self, requested: int | None) -> int:
        top_k = self.default_top_k if requested is None else requested
        if not 0 < top_k <= self.max_top_k:
            raise ValueError("top_k exceeds intelligent bounds")
        return top_k
