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
