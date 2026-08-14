from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Protocol

import numpy as np
from numpy.typing import NDArray

from zglab_rag.domain.models import (
    KnowledgeChunk,
    KnowledgeDocument,
    RawDocument,
    SourceDefinition,
)


class SourceLoader(Protocol):
    def load(self, source: SourceDefinition) -> Iterable[RawDocument]: ...


class DocumentParser(Protocol):
    def parse(self, raw_document: RawDocument) -> KnowledgeDocument: ...


class Chunker(Protocol):
    def split(self, document: KnowledgeDocument) -> list[KnowledgeChunk]: ...


class EmbeddingProvider(Protocol):
    @property
    def model_name(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    @property
    def device(self) -> str: ...

    def encode_documents(self, texts: Sequence[str]) -> NDArray[np.float32]: ...

    def encode_queries(self, texts: Sequence[str]) -> NDArray[np.float32]: ...


class IndexWriter(Protocol):
    def replace_document(
        self,
        document: KnowledgeDocument,
        chunks: Sequence[KnowledgeChunk],
        embeddings: Sequence[Sequence[float]],
    ) -> None: ...
