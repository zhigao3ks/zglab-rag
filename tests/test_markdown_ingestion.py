from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from zglab_rag.domain.models import RawDocument, Scope, SourceKind, Visibility
from zglab_rag.ingestion.chunking import ChunkingConfig, MarkdownHeadingChunker
from zglab_rag.ingestion.cli import main as ingestion_cli_main
from zglab_rag.ingestion.errors import (
    EmptyDocumentError,
    MalformedFrontmatterError,
    MissingMetadataError,
)
from zglab_rag.ingestion.markdown import MarkdownDocumentParser
from zglab_rag.ingestion.pipeline import LocalMarkdownIngestionPipeline
from zglab_rag.sources.local_markdown import LocalMarkdownSourceLoader
from zglab_rag.sources.registry import SourceRegistry

PROJECT_ROOT = Path(__file__).parents[1]
CONFIG_PATH = PROJECT_ROOT / "config/sources.yaml"


def _raw_document(
    content: str,
    *,
    visibility: Visibility = Visibility.PUBLIC,
    path: str = "knowledge/test.md",
) -> RawDocument:
    return RawDocument(
        source_id="test-source",
        source_kind=SourceKind.LOCAL,
        scope=Scope.KNOWLEDGE,
        visibility=visibility,
        priority=80,
        source_path=path,
        raw_content=content,
    )


def _chunker(*, target_size: int = 80, max_size: int = 120, overlap: int = 20):
    return MarkdownHeadingChunker(
        ChunkingConfig(target_size=target_size, max_size=max_size, overlap=overlap)
    )


@pytest.fixture
def profile_result():
    registry = SourceRegistry.from_yaml(CONFIG_PATH)
    source = registry.get("identity-profile")
    pipeline = LocalMarkdownIngestionPipeline(
        loader=LocalMarkdownSourceLoader(PROJECT_ROOT),
        parser=MarkdownDocumentParser(),
        chunker=_chunker(target_size=700, max_size=1200, overlap=120),
    )
    return pipeline.ingest(source)


def test_profile_markdown_loads_as_knowledge_document(profile_result) -> None:
    document = profile_result.document

    assert document.document_id == "identity-profile:knowledge/identity/profile.md"
    assert document.title == "黄志高个人知识档案"
    assert document.scope == Scope.IDENTITY
    assert document.visibility == Visibility.PUBLIC
    assert document.path == "knowledge/identity/profile.md"
    assert document.tags == ["Profile", "AI Agent", "RAG", "Multi-Agent"]
    assert profile_result.chunks


def test_heading_hierarchy_is_preserved() -> None:
    raw = _raw_document(
        """---
title: Nested headings
---
# 第一层
简介。

## 第二层
内容。

### 第三层
细节。
"""
    )
    document = MarkdownDocumentParser().parse(raw)
    chunks = _chunker().split(document)

    assert ["第一层"] in [chunk.section_path for chunk in chunks]
    assert ["第一层", "第二层"] in [chunk.section_path for chunk in chunks]
    assert ["第一层", "第二层", "第三层"] in [
        chunk.section_path for chunk in chunks
    ]
    assert all(chunk.content.strip() != "## 第二层" for chunk in chunks)


def test_oversized_section_uses_secondary_splitting() -> None:
    paragraphs = [f"第{index}段" + "内容" * 20 for index in range(12)]
    raw = _raw_document("---\ntitle: Long\n---\n# 长章节\n\n" + "\n\n".join(paragraphs))
    document = MarkdownDocumentParser().parse(raw)
    chunks = _chunker(target_size=100, max_size=140, overlap=20).split(document)

    assert len(chunks) > 1
    assert all(chunk.char_count <= 140 for chunk in chunks)
    assert all(chunk.section_path == ["长章节"] for chunk in chunks)


def test_repeated_chunking_produces_stable_chunk_ids(profile_result) -> None:
    config = ChunkingConfig(target_size=700, max_size=1200, overlap=120)
    second_chunks = MarkdownHeadingChunker(config).split(profile_result.document)

    assert [chunk.chunk_id for chunk in profile_result.chunks] == [
        chunk.chunk_id for chunk in second_chunks
    ]


def test_content_hashes_are_sha256(profile_result) -> None:
    document = profile_result.document
    source_text = (PROJECT_ROOT / document.path).read_text(encoding="utf-8")

    assert document.content_hash == hashlib.sha256(source_text.encode()).hexdigest()
    assert all(
        chunk.content_hash == hashlib.sha256(chunk.content.encode()).hexdigest()
        for chunk in profile_result.chunks
    )


def test_visibility_and_provenance_are_inherited_by_every_chunk(profile_result) -> None:
    document = profile_result.document

    assert all(chunk.visibility == document.visibility for chunk in profile_result.chunks)
    assert all(chunk.scope == document.scope for chunk in profile_result.chunks)
    assert all(chunk.source_id == document.source_id for chunk in profile_result.chunks)
    assert all(chunk.source_path == document.path for chunk in profile_result.chunks)
    assert all(chunk.revision == document.source_revision for chunk in profile_result.chunks)


def test_private_source_cannot_be_escalated_by_frontmatter() -> None:
    raw = _raw_document(
        "---\ntitle: Secret\nvisibility: public\n---\n# Secret\ncontent",
        visibility=Visibility.PRIVATE,
    )

    document = MarkdownDocumentParser().parse(raw)

    assert document.visibility == Visibility.PRIVATE
    assert all(chunk.visibility == Visibility.PRIVATE for chunk in _chunker().split(document))


def test_revision_is_propagated_to_chunks() -> None:
    raw = _raw_document("---\ntitle: Versioned\nrevision: abc123\n---\n# Versioned\ncontent")

    document = MarkdownDocumentParser().parse(raw)
    chunks = _chunker().split(document)

    assert document.source_revision == "abc123"
    assert chunks
    assert all(chunk.revision == "abc123" for chunk in chunks)


def test_malformed_frontmatter_has_explicit_error() -> None:
    raw = _raw_document("---\ntitle: [broken\n---\n# Body\ncontent")

    with pytest.raises(MalformedFrontmatterError, match="Malformed YAML frontmatter"):
        MarkdownDocumentParser().parse(raw)


def test_empty_markdown_body_has_explicit_error() -> None:
    raw = _raw_document("---\ntitle: Empty\n---\n\n")

    with pytest.raises(EmptyDocumentError, match="empty body"):
        MarkdownDocumentParser().parse(raw)


def test_missing_title_and_heading_has_explicit_error() -> None:
    raw = _raw_document("正文存在，但没有标题。")

    with pytest.raises(MissingMetadataError, match="missing required metadata 'title'"):
        MarkdownDocumentParser().parse(raw)


def test_invalid_chunking_configuration_has_explicit_error() -> None:
    with pytest.raises(ValueError, match="max_size"):
        ChunkingConfig(target_size=200, max_size=100, overlap=20)


def test_cli_prints_document_and_chunk_summary(capsys, monkeypatch) -> None:
    monkeypatch.chdir(PROJECT_ROOT)

    exit_code = ingestion_cli_main(["knowledge/identity/profile.md"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "document id: identity-profile:knowledge/identity/profile.md" in output
    assert "title: 黄志高个人知识档案" in output
    assert "chunk count:" in output
    assert "黄志高 · Personal Knowledge Profile > 基本身份" in output
