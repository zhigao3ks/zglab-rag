from __future__ import annotations

import hashlib
from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError, model_validator

from zglab_rag.evaluation.dataset import RelevantTarget


class GenerationDatasetError(ValueError):
    """Raised when a generation evaluation dataset is malformed."""


class GenerationQueryCategory(StrEnum):
    IDENTITY = "identity"
    KNOWLEDGE = "knowledge"
    PROJECT = "project"
    PROBLEM = "problem"
    MIXED_TECHNICAL = "mixed_technical"
    HARD_NEGATIVE = "hard_negative"


class GenerationQuery(BaseModel):
    id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    category: GenerationQueryCategory
    should_answer: bool
    expected_evidence: list[RelevantTarget] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_expectations(self) -> GenerationQuery:
        if self.should_answer and not self.expected_evidence:
            raise ValueError("should_answer=true queries require expected evidence targets")
        if self.category == GenerationQueryCategory.HARD_NEGATIVE and (
            self.expected_evidence or self.should_answer
        ):
            raise ValueError("hard_negative queries must expect no answer and no evidence")
        return self


class GenerationEvaluationDataset(BaseModel):
    version: int = Field(gt=0)
    queries: list[GenerationQuery] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_query_ids(self) -> GenerationEvaluationDataset:
        query_ids = [query.id for query in self.queries]
        if len(query_ids) != len(set(query_ids)):
            raise ValueError("generation dataset contains duplicate query IDs")
        return self


class LoadedGenerationDataset(BaseModel):
    dataset: GenerationEvaluationDataset
    sha256: str
    path: Path


def load_generation_dataset(path: str | Path) -> LoadedGenerationDataset:
    dataset_path = Path(path)
    try:
        payload = dataset_path.read_bytes()
        raw = yaml.safe_load(payload)
        dataset = GenerationEvaluationDataset.model_validate(raw)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise GenerationDatasetError(
            f"Unable to load generation evaluation dataset '{dataset_path}': {exc}"
        ) from exc
    return LoadedGenerationDataset(
        dataset=dataset,
        sha256=hashlib.sha256(payload).hexdigest(),
        path=dataset_path,
    )
