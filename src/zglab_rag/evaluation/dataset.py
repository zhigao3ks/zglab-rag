from __future__ import annotations

import hashlib
from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError, model_validator

from zglab_rag.domain.models import KnowledgeChunk


class EvaluationDatasetError(ValueError):
    """Raised when a retrieval evaluation dataset is malformed."""


class QueryCategory(StrEnum):
    IDENTITY = "identity"
    KNOWLEDGE = "knowledge"
    PROJECT = "project"
    PROBLEM = "problem"
    MIXED_TECHNICAL = "mixed_technical"
    HARD_NEGATIVE = "hard_negative"


class QuerySubset(StrEnum):
    STRUCTURE = "structure"
    GRAPH = "graph"
    MULTI_HOP = "multi_hop"


class RelevantTarget(BaseModel):
    source_id: str = Field(min_length=1)
    source_path: str = Field(min_length=1)
    section_path: list[str] = Field(default_factory=list)

    def matches(self, chunk: KnowledgeChunk) -> bool:
        section_matches = not self.section_path or (
            chunk.section_path[: len(self.section_path)] == self.section_path
        )
        return (
            chunk.source_id == self.source_id
            and chunk.source_path == self.source_path
            and section_matches
        )


class EvaluationQuery(BaseModel):
    id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    category: QueryCategory
    relevant: list[RelevantTarget] = Field(default_factory=list)
    needs_review: bool = False
    subsets: tuple[QuerySubset, ...] = ()

    @model_validator(mode="after")
    def validate_relevance(self) -> EvaluationQuery:
        if self.category == QueryCategory.HARD_NEGATIVE:
            if self.relevant:
                raise ValueError("hard_negative queries must not define relevant targets")
        elif not self.relevant and not self.needs_review:
            raise ValueError("scored queries require at least one relevant target")
        return self


class RetrievalEvaluationDataset(BaseModel):
    version: int = Field(gt=0)
    queries: list[EvaluationQuery] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_query_ids(self) -> RetrievalEvaluationDataset:
        query_ids = [query.id for query in self.queries]
        if len(query_ids) != len(set(query_ids)):
            raise ValueError("evaluation dataset contains duplicate query IDs")
        return self

    def category_distribution(self) -> dict[str, int]:
        distribution = {category.value: 0 for category in QueryCategory}
        for query in self.queries:
            distribution[query.category.value] += 1
        return distribution


class LoadedEvaluationDataset(BaseModel):
    dataset: RetrievalEvaluationDataset
    sha256: str
    path: Path


def load_evaluation_dataset(path: str | Path) -> LoadedEvaluationDataset:
    dataset_path = Path(path)
    try:
        payload = dataset_path.read_bytes()
        raw = yaml.safe_load(payload)
        dataset = RetrievalEvaluationDataset.model_validate(raw)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise EvaluationDatasetError(
            f"Unable to load retrieval evaluation dataset '{dataset_path}': {exc}"
        ) from exc
    return LoadedEvaluationDataset(
        dataset=dataset,
        sha256=hashlib.sha256(payload).hexdigest(),
        path=dataset_path,
    )
