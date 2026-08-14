from __future__ import annotations

from dataclasses import dataclass

from zglab_rag.domain.models import KnowledgeChunk, KnowledgeDocument, SourceDefinition
from zglab_rag.ingestion.chunking import MarkdownHeadingChunker
from zglab_rag.ingestion.markdown import MarkdownDocumentParser
from zglab_rag.sources.base import SourceAdapter
from zglab_rag.sources.local_markdown import LocalMarkdownSourceLoader


@dataclass(frozen=True, slots=True)
class IngestionResult:
    document: KnowledgeDocument
    chunks: list[KnowledgeChunk]


@dataclass(frozen=True, slots=True)
class SourceIngestionResult:
    source_id: str
    revision: str | None
    documents: list[KnowledgeDocument]
    chunks: list[KnowledgeChunk]


class LocalMarkdownIngestionPipeline:
    def __init__(
        self,
        loader: LocalMarkdownSourceLoader,
        parser: MarkdownDocumentParser,
        chunker: MarkdownHeadingChunker,
    ) -> None:
        self._loader = loader
        self._parser = parser
        self._chunker = chunker

    def ingest(self, source: SourceDefinition) -> IngestionResult:
        raw_documents = list(self._loader.load(source))
        if len(raw_documents) != 1:
            raise ValueError(
                f"Local source '{source.id}' must produce exactly one document, "
                f"got {len(raw_documents)}"
            )
        document = self._parser.parse(raw_documents[0])
        return IngestionResult(document=document, chunks=self._chunker.split(document))


class MarkdownSourceIngestionPipeline:
    """Parse and chunk every document produced by one registered source adapter."""

    def __init__(
        self,
        adapter: SourceAdapter,
        parser: MarkdownDocumentParser,
        chunker: MarkdownHeadingChunker,
    ) -> None:
        self._adapter = adapter
        self._parser = parser
        self._chunker = chunker

    def ingest(self, source: SourceDefinition) -> SourceIngestionResult:
        snapshot = self._adapter.inspect(source)
        documents: list[KnowledgeDocument] = []
        chunks: list[KnowledgeChunk] = []
        revisions: set[str] = set()
        for raw_document in self._adapter.load(source):
            document = self._parser.parse(raw_document)
            documents.append(document)
            chunks.extend(self._chunker.split(document))
            if raw_document.revision:
                revisions.add(raw_document.revision)

        if len(revisions) > 1:
            raise ValueError(
                f"Source '{source.id}' returned documents from multiple revisions: "
                f"{sorted(revisions)}"
            )
        document_revision = next(iter(revisions), None)
        if snapshot.revision and document_revision and snapshot.revision != document_revision:
            raise ValueError(
                f"Source '{source.id}' changed revision during ingestion: "
                f"{snapshot.revision} -> {document_revision}"
            )
        return SourceIngestionResult(
            source_id=source.id,
            revision=snapshot.revision or document_revision,
            documents=documents,
            chunks=chunks,
        )
