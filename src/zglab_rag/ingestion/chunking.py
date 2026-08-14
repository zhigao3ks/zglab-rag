from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from zglab_rag.domain.models import KnowledgeChunk, KnowledgeDocument
from zglab_rag.ingestion.markdown import sha256_text

_HEADING_PATTERN = re.compile(r"^[ \t]{0,3}(#{1,6})[ \t]+(.*?)[ \t]*$")
_FENCE_PATTERN = re.compile(r"^[ \t]*(`{3,}|~{3,})")
_BREAK_SEPARATORS = ("\n\n", "\n", "。", "！", "？", ". ", "! ", "? ", "；", "; ", " ")


@dataclass(frozen=True, slots=True)
class ChunkingConfig:
    target_size: int
    max_size: int
    overlap: int

    def __post_init__(self) -> None:
        if self.target_size <= 0:
            raise ValueError("target_size must be greater than zero")
        if self.max_size < self.target_size:
            raise ValueError("max_size must be greater than or equal to target_size")
        if not 0 <= self.overlap < self.target_size:
            raise ValueError("overlap must be non-negative and smaller than target_size")


@dataclass(frozen=True, slots=True)
class MarkdownSection:
    path: tuple[str, ...]
    content: str


def _parse_sections(markdown: str) -> list[MarkdownSection]:
    sections: list[MarkdownSection] = []
    heading_stack: list[str] = []
    current_path: tuple[str, ...] = ()
    current_lines: list[str] = []
    active_fence: str | None = None

    def flush() -> None:
        content = "".join(current_lines).strip()
        heading_only = bool(_HEADING_PATTERN.fullmatch(content))
        if content and not heading_only:
            sections.append(MarkdownSection(path=current_path, content=content))

    for line in markdown.splitlines(keepends=True):
        fence_match = _FENCE_PATTERN.match(line)
        if fence_match:
            marker = fence_match.group(1)
            if active_fence is None:
                active_fence = marker[0]
            elif marker[0] == active_fence:
                active_fence = None

        heading = _HEADING_PATTERN.match(line.rstrip("\n")) if active_fence is None else None
        if heading:
            flush()
            current_lines = [line]
            level = len(heading.group(1))
            heading_text = re.sub(r"[ \t]+#+[ \t]*$", "", heading.group(2)).strip()
            heading_stack[level - 1 :] = [heading_text]
            current_path = tuple(heading_stack)
        else:
            current_lines.append(line)

    flush()
    return sections


def _boundary(text: str, start: int, ideal: int, hard_limit: int) -> int:
    lower_limit = min(ideal, start + max(1, (ideal - start) // 2))
    for separator in _BREAK_SEPARATORS:
        before = text.rfind(separator, lower_limit, ideal + 1)
        after = text.find(separator, ideal, hard_limit + 1)
        candidates = [position for position in (before, after) if position >= 0]
        if candidates:
            position = min(candidates, key=lambda item: abs(item - ideal))
            return min(position + len(separator), hard_limit)
    return hard_limit


def _split_oversized(content: str, config: ChunkingConfig) -> list[str]:
    if len(content) <= config.max_size:
        return [content]

    pieces: list[str] = []
    start = 0
    while start < len(content):
        remaining = len(content) - start
        if remaining <= config.max_size:
            end = len(content)
        else:
            ideal = min(start + config.target_size, len(content))
            hard_limit = min(start + config.max_size, len(content))
            end = _boundary(content, start, ideal, hard_limit)
        if end <= start:
            end = min(start + config.max_size, len(content))

        piece = content[start:end].strip()
        if piece:
            pieces.append(piece)
        if end == len(content):
            break

        next_start = max(start + 1, end - config.overlap)
        while next_start < end and content[next_start].isspace():
            next_start += 1
        start = next_start

    return pieces


def _chunk_id(
    document_id: str,
    section_path: tuple[str, ...],
    section_occurrence: int,
    part_index: int,
    content_hash: str,
) -> str:
    identity = "\x1f".join(
        (
            document_id,
            *section_path,
            str(section_occurrence),
            str(part_index),
            content_hash,
        )
    )
    return f"{document_id}#{sha256_text(identity)[:24]}"


class MarkdownHeadingChunker:
    """Split Markdown on heading boundaries, then split only oversized sections."""

    def __init__(self, config: ChunkingConfig) -> None:
        self._config = config

    def split(self, document: KnowledgeDocument) -> list[KnowledgeChunk]:
        chunks: list[KnowledgeChunk] = []
        path_occurrences: defaultdict[tuple[str, ...], int] = defaultdict(int)

        for section in _parse_sections(document.content):
            section_occurrence = path_occurrences[section.path]
            path_occurrences[section.path] += 1
            for part_index, content in enumerate(_split_oversized(section.content, self._config)):
                content_hash = sha256_text(content)
                chunks.append(
                    KnowledgeChunk(
                        chunk_id=_chunk_id(
                            document.document_id,
                            section.path,
                            section_occurrence,
                            part_index,
                            content_hash,
                        ),
                        document_id=document.document_id,
                        source_id=document.source_id,
                        scope=document.scope,
                        visibility=document.visibility,
                        priority=document.priority,
                        title=document.title,
                        section_path=list(section.path),
                        chunk_index=len(chunks),
                        content=content,
                        content_hash=content_hash,
                        char_count=len(content),
                        project=document.project,
                        tags=list(document.tags),
                        source_url=document.source_url,
                        source_path=document.path,
                        revision=document.source_revision,
                    )
                )

        return chunks
