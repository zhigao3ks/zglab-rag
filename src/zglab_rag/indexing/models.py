from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from zglab_rag.domain.models import KnowledgeChunk, KnowledgeDocument, SourceDefinition
from zglab_rag.embeddings.config import EmbeddingModelConfig
from zglab_rag.evaluation.composition import TextComposition


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class EmbeddingProfile:
    profile_id: str
    model_id: str
    model_name: str
    dimension: int
    composition: TextComposition
    normalize: bool
    query_mode: str
    max_length: int
    config_hash: str

    @classmethod
    def create(
        cls,
        config: EmbeddingModelConfig,
        *,
        dimension: int,
        composition: TextComposition,
    ) -> EmbeddingProfile:
        values = {
            "composition": composition.value,
            "dimension": dimension,
            "max_length": config.max_length,
            "model_id": config.id,
            "model_name": config.model_name,
            "normalize": config.normalize,
            "query_mode": config.query_mode.value,
        }
        canonical = json.dumps(
            values,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        config_hash = _sha256(canonical)
        return cls(
            profile_id=f"ep_{config_hash}",
            config_hash=config_hash,
            model_id=config.id,
            model_name=config.model_name,
            dimension=dimension,
            composition=composition,
            normalize=config.normalize,
            query_mode=config.query_mode.value,
            max_length=config.max_length,
        )


@dataclass(frozen=True, slots=True)
class SourceIndexInput:
    source: SourceDefinition
    revision: str | None
    documents: list[KnowledgeDocument]
    chunks: list[KnowledgeChunk]


@dataclass(frozen=True, slots=True)
class PlannedChunk:
    chunk: KnowledgeChunk
    embedding_text: str
    embedding_input_hash: str


@dataclass(frozen=True, slots=True)
class StoredChunkState:
    chunk_id: str
    source_id: str
    embedding_profile_id: str | None
    embedding_input_hash: str | None


@dataclass(slots=True)
class IndexPlan:
    new: list[PlannedChunk] = field(default_factory=list)
    changed: list[PlannedChunk] = field(default_factory=list)
    unchanged: list[PlannedChunk] = field(default_factory=list)
    deleted: list[StoredChunkState] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.new) + len(self.changed) + len(self.unchanged)

    @property
    def needs_embedding(self) -> list[PlannedChunk]:
        return [*self.new, *self.changed]

    def statistics(self) -> dict[str, int]:
        return {
            "total": self.total,
            "new": len(self.new),
            "changed": len(self.changed),
            "unchanged": len(self.unchanged),
            "deleted": len(self.deleted),
            "needs_embedding": len(self.needs_embedding),
        }


@dataclass(frozen=True, slots=True)
class IndexRunResult:
    run_id: str
    document_count: int
    plan: IndexPlan
    embedded_chunks: int
    elapsed_seconds: float
