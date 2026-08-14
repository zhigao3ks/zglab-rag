from __future__ import annotations

from dataclasses import dataclass

from zglab_rag.domain.models import KnowledgeChunk, KnowledgeDocument, SourceDefinition
from zglab_rag.ingestion.chunking import MarkdownHeadingChunker
from zglab_rag.ingestion.markdown import MarkdownDocumentParser
from zglab_rag.sources.local_markdown import LocalMarkdownSourceLoader


@dataclass(frozen=True, slots=True)
class IngestionResult:
    document: KnowledgeDocument
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
