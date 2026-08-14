from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Protocol

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
    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class IndexWriter(Protocol):
    def replace_document(
        self,
        document: KnowledgeDocument,
        chunks: Sequence[KnowledgeChunk],
        embeddings: Sequence[Sequence[float]],
    ) -> None: ...
