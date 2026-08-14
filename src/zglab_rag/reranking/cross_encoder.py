from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Protocol

import numpy as np
from numpy.typing import NDArray

from zglab_rag.embeddings.sentence_transformer import ensure_device_available
from zglab_rag.reranking.config import RerankerModelConfig


class CrossEncoderModel(Protocol):
    def predict(self, inputs: list[tuple[str, str]], **kwargs: Any) -> Any: ...


ModelFactory = Callable[[RerankerModelConfig, str], CrossEncoderModel]


def _default_model_factory(config: RerankerModelConfig, device: str) -> CrossEncoderModel:
    from sentence_transformers import CrossEncoder

    return CrossEncoder(
        config.model_name,
        device=device,
        backend=config.backend.value,
        max_length=config.max_length,
    )


class CrossEncoderRerankerProvider:
    """Sentence Transformers CrossEncoder adapter using query/passage pairs."""

    def __init__(
        self,
        config: RerankerModelConfig,
        *,
        device: str = "cpu",
        model_path: str | Path | None = None,
        model_factory: ModelFactory | None = None,
    ) -> None:
        ensure_device_available(device)
        self.config = config
        self.device = device
        load_config = (
            config
            if model_path is None
            else config.model_copy(update={"model_name": str(model_path)})
        )
        self._model = (model_factory or _default_model_factory)(load_config, device)

    @property
    def model_name(self) -> str:
        return self.config.model_name

    @property
    def backend(self) -> str:
        return self.config.backend.value

    @property
    def batch_size(self) -> int:
        return self.config.batch_size

    def score(self, query: str, passages: Sequence[str]) -> NDArray[np.float32]:
        if not passages:
            return np.empty(0, dtype=np.float32)
        pairs = [(query, passage) for passage in passages]
        raw = self._model.predict(
            pairs,
            batch_size=self.batch_size,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        return np.asarray(raw, dtype=np.float32).reshape(-1)
