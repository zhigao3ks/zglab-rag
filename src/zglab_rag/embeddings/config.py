from __future__ import annotations

from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError


class EmbeddingConfigurationError(ValueError):
    """Raised when the embedding model registry is invalid."""


class EmbeddingBackend(StrEnum):
    SENTENCE_TRANSFORMERS = "sentence_transformers"


class QueryMode(StrEnum):
    BGE_ZH_INSTRUCTION = "bge_zh_instruction"
    E5_PREFIX = "e5_prefix"
    MODEL_QUERY_PROMPT = "model_query_prompt"


class EmbeddingModelConfig(BaseModel):
    id: str = Field(min_length=1)
    model_name: str = Field(min_length=1)
    backend: EmbeddingBackend
    query_mode: QueryMode
    normalize: bool = True
    max_length: int = Field(gt=0)
    enabled: bool = True


class EmbeddingModelRegistryConfig(BaseModel):
    version: int = Field(default=1, gt=0)
    models: list[EmbeddingModelConfig] = Field(min_length=1)


class EmbeddingModelRegistry:
    def __init__(self, config: EmbeddingModelRegistryConfig) -> None:
        model_ids = [model.id for model in config.models]
        if len(model_ids) != len(set(model_ids)):
            raise EmbeddingConfigurationError("Embedding model registry contains duplicate IDs")
        self.version = config.version
        self._models = {model.id: model for model in config.models}

    @classmethod
    def from_yaml(cls, path: str | Path) -> EmbeddingModelRegistry:
        config_path = Path(path)
        try:
            raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            config = EmbeddingModelRegistryConfig.model_validate(raw)
        except (OSError, yaml.YAMLError, ValidationError) as exc:
            raise EmbeddingConfigurationError(
                f"Unable to load embedding model registry '{config_path}': {exc}"
            ) from exc
        return cls(config)

    def all(self, *, enabled_only: bool = True) -> list[EmbeddingModelConfig]:
        models = list(self._models.values())
        if enabled_only:
            return [model for model in models if model.enabled]
        return models

    def get_enabled(self, model_id: str) -> EmbeddingModelConfig:
        try:
            model = self._models[model_id]
        except KeyError as exc:
            raise EmbeddingConfigurationError(f"Unknown embedding model: {model_id}") from exc
        if not model.enabled:
            raise EmbeddingConfigurationError(f"Embedding model is disabled: {model_id}")
        return model
