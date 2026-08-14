from __future__ import annotations

from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError


class RerankerConfigurationError(ValueError):
    """Raised when the reranker model registry is invalid."""


class RerankerBackend(StrEnum):
    TORCH = "torch"
    ONNX = "onnx"
    OPENVINO = "openvino"


class RerankerModelConfig(BaseModel):
    id: str = Field(min_length=1)
    model_name: str = Field(min_length=1)
    backend: RerankerBackend
    max_length: int = Field(gt=0)
    batch_size: int = Field(gt=0)
    enabled: bool = True


class RerankerModelRegistryConfig(BaseModel):
    version: int = Field(default=1, gt=0)
    models: list[RerankerModelConfig] = Field(min_length=1)


class RerankerModelRegistry:
    def __init__(self, config: RerankerModelRegistryConfig) -> None:
        model_ids = [model.id for model in config.models]
        if len(model_ids) != len(set(model_ids)):
            raise RerankerConfigurationError("Reranker model registry contains duplicate IDs")
        self.version = config.version
        self._models = {model.id: model for model in config.models}

    @classmethod
    def from_yaml(cls, path: str | Path) -> RerankerModelRegistry:
        config_path = Path(path)
        try:
            raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            config = RerankerModelRegistryConfig.model_validate(raw)
        except (OSError, yaml.YAMLError, ValidationError) as exc:
            raise RerankerConfigurationError(
                f"Unable to load reranker model registry '{config_path}': {exc}"
            ) from exc
        return cls(config)

    def all(self, *, enabled_only: bool = True) -> list[RerankerModelConfig]:
        models = list(self._models.values())
        return [model for model in models if model.enabled] if enabled_only else models

    def get_enabled(self, model_id: str) -> RerankerModelConfig:
        try:
            model = self._models[model_id]
        except KeyError as exc:
            raise RerankerConfigurationError(f"Unknown reranker model: {model_id}") from exc
        if not model.enabled:
            raise RerankerConfigurationError(f"Reranker model is disabled: {model_id}")
        return model
