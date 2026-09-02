from __future__ import annotations

import os
import threading
from collections.abc import Callable, Sequence
from typing import Any, Protocol

import numpy as np
from numpy.typing import NDArray

from zglab_rag.embeddings.config import EmbeddingModelConfig, QueryMode

BGE_ZH_QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："


class DeviceUnavailableError(RuntimeError):
    """Raised instead of silently changing the requested execution device."""


class SentenceTransformerModel(Protocol):
    max_seq_length: int

    def get_sentence_embedding_dimension(self) -> int | None: ...

    def encode_document(self, sentences: list[str], **kwargs: Any) -> Any: ...

    def encode_query(self, sentences: list[str], **kwargs: Any) -> Any: ...


ModelFactory = Callable[[str, str], SentenceTransformerModel]


def _default_model_factory(model_name: str, device: str) -> SentenceTransformerModel:
    from sentence_transformers import SentenceTransformer

    # Production pins approved model snapshots in the local HF cache. Resolving
    # the snapshot ourselves keeps startup offline after deployment and avoids a
    # first-request network dependency; the configured model name and embedding
    # behaviour remain unchanged.
    model_reference = model_name
    if os.environ.get("HF_HUB_OFFLINE") == "1" and "/" in model_name:
        from huggingface_hub import snapshot_download

        model_reference = snapshot_download(model_name, local_files_only=True)
    return SentenceTransformer(model_reference, device=device)


def ensure_device_available(device: str) -> None:
    if device == "cpu":
        return
    if device != "cuda":
        raise DeviceUnavailableError(f"Unsupported embedding device: {device}")

    import torch

    if not torch.cuda.is_available():
        raise DeviceUnavailableError(
            "CUDA was explicitly requested but torch.cuda.is_available() is false"
        )


class SentenceTransformerEmbeddingProvider:
    """Sentence Transformers adapter preserving model-specific retrieval semantics."""

    def __init__(
        self,
        config: EmbeddingModelConfig,
        *,
        device: str,
        batch_size: int = 32,
        model_factory: ModelFactory | None = None,
    ) -> None:
        ensure_device_available(device)
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        self.config = config
        self._device = device
        self._batch_size = batch_size
        self._model = (model_factory or _default_model_factory)(config.model_name, device)
        self._model.max_seq_length = config.max_length
        dimension_getter = getattr(self._model, "get_embedding_dimension", None)
        dimension = (
            dimension_getter()
            if dimension_getter is not None
            else self._model.get_sentence_embedding_dimension()
        )
        if not dimension:
            raise ValueError(f"Model '{config.model_name}' did not report an embedding dimension")
        self._dimension = dimension
        # One model instance is shared by public retrieval and the internal
        # embedding API. Serialize inference so concurrent callers do not race
        # through the underlying SentenceTransformer model.
        self._encode_lock = threading.Lock()

    @property
    def model_name(self) -> str:
        return self.config.model_name

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def device(self) -> str:
        return self._device

    def encode_documents(self, texts: Sequence[str]) -> NDArray[np.float32]:
        prepared = self._prepare_documents(texts)
        return self._encode(prepared, query=False)

    def encode_queries(self, texts: Sequence[str]) -> NDArray[np.float32]:
        prepared = self._prepare_queries(texts)
        return self._encode(prepared, query=True)

    def _prepare_documents(self, texts: Sequence[str]) -> list[str]:
        if self.config.query_mode == QueryMode.E5_PREFIX:
            return [f"passage: {text}" for text in texts]
        return list(texts)

    def _prepare_queries(self, texts: Sequence[str]) -> list[str]:
        if self.config.query_mode == QueryMode.BGE_ZH_INSTRUCTION:
            return [f"{BGE_ZH_QUERY_INSTRUCTION}{text}" for text in texts]
        if self.config.query_mode == QueryMode.E5_PREFIX:
            return [f"query: {text}" for text in texts]
        return list(texts)

    def _encode(self, texts: list[str], *, query: bool) -> NDArray[np.float32]:
        if not texts:
            return np.empty((0, self.dimension), dtype=np.float32)

        kwargs: dict[str, Any] = {
            "batch_size": self._batch_size,
            "convert_to_numpy": True,
            "normalize_embeddings": self.config.normalize,
            "show_progress_bar": False,
        }
        if query and self.config.query_mode == QueryMode.MODEL_QUERY_PROMPT:
            kwargs["prompt_name"] = "query"

        encoder = self._model.encode_query if query else self._model.encode_document
        with self._encode_lock:
            embeddings = np.asarray(encoder(texts, **kwargs), dtype=np.float32)
        if embeddings.shape != (len(texts), self.dimension):
            raise ValueError(
                f"Model '{self.model_name}' returned shape {embeddings.shape}; "
                f"expected {(len(texts), self.dimension)}"
            )
        return embeddings
