from enum import StrEnum

from zglab_rag.domain.models import KnowledgeChunk


class TextComposition(StrEnum):
    CONTENT_ONLY = "content_only"
    CONTEXTUAL = "contextual"


def compose_document_text(chunk: KnowledgeChunk, composition: TextComposition) -> str:
    if composition == TextComposition.CONTENT_ONLY:
        return chunk.content
    if composition == TextComposition.CONTEXTUAL:
        section = " > ".join(chunk.section_path) or "(root)"
        return f"Title: {chunk.title}\nSection: {section}\n\n{chunk.content}"
    raise ValueError(f"Unsupported text composition: {composition}")
