from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from zglab_rag.domain.models import KnowledgeChunk, KnowledgeDocument
from zglab_rag.ingestion.markdown import sha256_text

_HEADING_PATTERN = re.compile(r"^[ \t]{0,3}(#{1,6})[ \t]+(.*?)[ \t]*$")
_FENCE_PATTERN = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})(.*)$")
_BREAK_SEPARATORS = ("\n\n", "\n", "。", "！", "？", ". ", "! ", "? ", "；", "; ", " ")

type FenceState = tuple[str, int]


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


@dataclass(frozen=True, slots=True)
class FenceSpan:
    start: int
    end: int


def _opened_fence(match: re.Match[str]) -> FenceState:
    marker = match.group(1)
    return marker[0], len(marker)


def _closes_fence(match: re.Match[str], active_fence: FenceState) -> bool:
    marker = match.group(1)
    marker_type, minimum_length = active_fence
    return (
        marker[0] == marker_type
        and len(marker) >= minimum_length
        and not match.group(2).strip()
    )


def _parse_sections(markdown: str) -> list[MarkdownSection]:
    sections: list[MarkdownSection] = []
    heading_stack: list[str] = []
    current_path: tuple[str, ...] = ()
    current_lines: list[str] = []
    active_fence: FenceState | None = None

    def flush() -> None:
        content = "".join(current_lines).strip()
        heading_only = bool(_HEADING_PATTERN.fullmatch(content))
        if content and not heading_only:
            sections.append(MarkdownSection(path=current_path, content=content))

    for line in markdown.splitlines(keepends=True):
        fence_match = _FENCE_PATTERN.match(line)
        if active_fence is not None:
            heading = None
            if fence_match and _closes_fence(fence_match, active_fence):
                active_fence = None
        elif fence_match:
            active_fence = _opened_fence(fence_match)
            heading = None
        else:
            heading = _HEADING_PATTERN.match(line.rstrip("\n"))
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


def _fenced_spans(text: str) -> list[FenceSpan]:
    spans: list[FenceSpan] = []
    active_fence: FenceState | None = None
    fence_start = 0
    offset = 0
    for line in text.splitlines(keepends=True):
        fence_match = _FENCE_PATTERN.match(line)
        if active_fence is None and fence_match:
            active_fence = _opened_fence(fence_match)
            fence_start = offset
        elif (
            active_fence is not None
            and fence_match
            and _closes_fence(fence_match, active_fence)
        ):
            spans.append(FenceSpan(start=fence_start, end=offset + len(line)))
            active_fence = None
        offset += len(line)
    if active_fence is not None:
        spans.append(FenceSpan(start=fence_start, end=len(text)))
    return spans


def _protect_end(start: int, proposed_end: int, fence_spans: list[FenceSpan]) -> int:
    for span in fence_spans:
        if span.start < proposed_end < span.end:
            return span.start if span.start > start else span.end
    return proposed_end


def _protect_overlap_start(
    current_start: int,
    end: int,
    overlap: int,
    fence_spans: list[FenceSpan],
) -> int:
    proposed_start = end - overlap
    if proposed_start <= current_start:
        return end
    for span in fence_spans:
        if span.start < proposed_start < span.end:
            return span.start if span.start > current_start else span.end
    return proposed_start


def _split_oversized(content: str, config: ChunkingConfig) -> list[str]:
    if len(content) <= config.max_size:
        return [content]

    pieces: list[str] = []
    fence_spans = _fenced_spans(content)
    start = 0
    while start < len(content):
        remaining = len(content) - start
        if remaining <= config.max_size:
            end = len(content)
        else:
            ideal = min(start + config.target_size, len(content))
            hard_limit = min(start + config.max_size, len(content))
            end = _boundary(content, start, ideal, hard_limit)
            end = _protect_end(start, end, fence_spans)
        if end <= start:
            end = min(start + config.max_size, len(content))

        piece = content[start:end].strip()
        if piece:
            pieces.append(piece)
        if end == len(content):
            break

        next_start = _protect_overlap_start(start, end, config.overlap, fence_spans)
        next_start = max(start + 1, next_start)
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
