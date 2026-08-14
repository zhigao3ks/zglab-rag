from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from zglab_rag.domain.models import (
    RawDocument,
    Scope,
    SourceDefinition,
    SourceKind,
    SourceRegistryConfig,
    Visibility,
)
from zglab_rag.ingestion.chunking import ChunkingConfig, MarkdownHeadingChunker
from zglab_rag.ingestion.markdown import MarkdownDocumentParser
from zglab_rag.ingestion.pipeline import MarkdownSourceIngestionPipeline
from zglab_rag.sources.errors import (
    NotGitRepositoryError,
    SourceNotRegisteredError,
    SourcePathNotFoundError,
)
from zglab_rag.sources.local_git import LocalGitSource
from zglab_rag.sources.registry import SourceRegistry


def _git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


@pytest.fixture
def git_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "registered-repo"
    repository.mkdir()
    documents = {
        "README.md": "# Registered Repository\n\nPublic overview.\n",
        "docs/guide.md": "# Guide\n\n## Setup\n\nConfigured content.\n",
        "docs/nested/design.md": "# Design\n\nNested content.\n",
        "docs/private.md": (
            "---\ntitle: Private note\nvisibility: private\n---\n# Private note\n\nRestricted.\n"
        ),
        "archive/hidden.md": "# Archived\n\nMust be excluded.\n",
        "notes.txt": "Not Markdown and not allowlisted.\n",
    }
    for relative_path, content in documents.items():
        path = repository / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    outside_file = tmp_path / "outside.md"
    outside_file.write_text("# Outside\n\nMust not be followed.\n", encoding="utf-8")
    (repository / "linked.md").symlink_to(outside_file)

    _git(repository, "init", "--initial-branch=main")
    _git(repository, "config", "user.name", "Test User")
    _git(repository, "config", "user.email", "test@example.com")
    _git(repository, "remote", "add", "origin", "https://github.com/example/registered-repo.git")
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "test fixture")
    return repository


def _source(
    repository: Path,
    *,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    visibility: Visibility = Visibility.PUBLIC,
) -> SourceDefinition:
    return SourceDefinition(
        id="registered-source",
        kind=SourceKind.GIT,
        scope=Scope.KNOWLEDGE,
        visibility=visibility,
        priority=80,
        repository="example/registered-repo",
        ref="main",
        local_path=repository.name,
        include=include or ["README.md", "docs/**/*.md"],
        exclude=exclude or [],
    )


def test_local_git_discovery_uses_allowlist_and_deterministic_order(
    git_repository: Path,
) -> None:
    source = _source(
        git_repository,
        include=["docs/**/*.md", "README.md", "docs/guide.md"],
    )
    adapter = LocalGitSource(git_repository.parent)

    first = adapter.inspect(source)
    second = adapter.inspect(source)

    assert first.document_paths == (
        "README.md",
        "docs/guide.md",
        "docs/nested/design.md",
        "docs/private.md",
    )
    assert second.document_paths == first.document_paths


def test_exclude_overrides_include(git_repository: Path) -> None:
    source = _source(
        git_repository,
        include=[
            "README.md",
            "docs/guide.md",
            "docs/nested/*.md",
            "archive/**/*.md",
        ],
        exclude=["archive/**", "docs/nested/**"],
    )

    snapshot = LocalGitSource(git_repository.parent).inspect(source)

    assert snapshot.document_paths == ("README.md", "docs/guide.md")


def test_symlink_outside_repository_is_not_followed(git_repository: Path) -> None:
    source = _source(git_repository, include=["*.md"])

    snapshot = LocalGitSource(git_repository.parent).inspect(source)

    assert "README.md" in snapshot.document_paths
    assert "linked.md" not in snapshot.document_paths


def test_revision_and_repo_relative_source_path(git_repository: Path) -> None:
    source = _source(git_repository)
    adapter = LocalGitSource(git_repository.parent)

    documents = list(adapter.load(source))
    expected_revision = _git(git_repository, "rev-parse", "HEAD")

    assert documents
    assert all(document.revision == expected_revision for document in documents)
    assert [document.source_path for document in documents] == [
        "README.md",
        "docs/guide.md",
        "docs/nested/design.md",
        "docs/private.md",
    ]
    assert all(not Path(document.source_path).is_absolute() for document in documents)
    assert all(
        document.source_url
        == (
            "https://github.com/example/registered-repo/blob/"
            f"{expected_revision}/{document.source_path}"
        )
        for document in documents
    )


def test_invalid_local_path_has_explicit_error(tmp_path: Path) -> None:
    source = _source(tmp_path / "missing")

    with pytest.raises(SourcePathNotFoundError, match="does not exist"):
        LocalGitSource(tmp_path).inspect(source)


def test_existing_non_git_path_has_explicit_error(tmp_path: Path) -> None:
    directory = tmp_path / "not-a-repository"
    directory.mkdir()
    source = _source(directory)

    with pytest.raises(NotGitRepositoryError, match="Invalid local Git repository"):
        LocalGitSource(tmp_path).inspect(source)


def test_visibility_and_stable_chunk_ids_across_repeated_ingestion(
    git_repository: Path,
) -> None:
    source = _source(git_repository, visibility=Visibility.PRIVATE)
    adapter = LocalGitSource(git_repository.parent)
    pipeline = MarkdownSourceIngestionPipeline(
        adapter=adapter,
        parser=MarkdownDocumentParser(),
        chunker=MarkdownHeadingChunker(
            ChunkingConfig(target_size=80, max_size=120, overlap=20)
        ),
    )

    first = pipeline.ingest(source)
    second = pipeline.ingest(source)

    assert first.documents
    assert all(document.visibility == Visibility.PRIVATE for document in first.documents)
    assert all(chunk.visibility == Visibility.PRIVATE for chunk in first.chunks)
    assert [chunk.chunk_id for chunk in first.chunks] == [
        chunk.chunk_id for chunk in second.chunks
    ]
    assert all(chunk.revision == first.revision for chunk in first.chunks)


def test_document_frontmatter_can_tighten_source_visibility(git_repository: Path) -> None:
    source = _source(git_repository, include=["docs/private.md"])
    pipeline = MarkdownSourceIngestionPipeline(
        adapter=LocalGitSource(git_repository.parent),
        parser=MarkdownDocumentParser(),
        chunker=MarkdownHeadingChunker(
            ChunkingConfig(target_size=80, max_size=120, overlap=20)
        ),
    )

    result = pipeline.ingest(source)

    assert len(result.documents) == 1
    assert result.documents[0].visibility == Visibility.PRIVATE
    assert result.chunks
    assert all(chunk.visibility == Visibility.PRIVATE for chunk in result.chunks)


def test_empty_allowlist_match_keeps_source_revision(git_repository: Path) -> None:
    source = _source(git_repository, include=["missing/**/*.md"])
    pipeline = MarkdownSourceIngestionPipeline(
        adapter=LocalGitSource(git_repository.parent),
        parser=MarkdownDocumentParser(),
        chunker=MarkdownHeadingChunker(
            ChunkingConfig(target_size=80, max_size=120, overlap=20)
        ),
    )

    result = pipeline.ingest(source)

    assert result.revision == _git(git_repository, "rev-parse", "HEAD")
    assert result.documents == []
    assert result.chunks == []


def test_unregistered_source_is_rejected(git_repository: Path) -> None:
    registry = SourceRegistry(SourceRegistryConfig(sources=[_source(git_repository)]))

    with pytest.raises(SourceNotRegisteredError, match="not registered"):
        registry.get_enabled("unknown-repository")


@pytest.mark.parametrize("pattern", ["**/*", "**/*.md"])
def test_repository_wide_catch_all_is_rejected(
    git_repository: Path,
    pattern: str,
) -> None:
    with pytest.raises(ValidationError, match="repository-wide catch-all"):
        _source(git_repository, include=[pattern])


def test_oversized_fenced_block_is_kept_intact() -> None:
    fenced_block = "```mermaid\n" + "A --> B\n" * 30 + "```"
    raw_content = f"# Diagram\n\nBefore.\n\n{fenced_block}\n\nAfter. " + "text " * 40
    source_document = _source_document(raw_content)
    document = MarkdownDocumentParser().parse(source_document)

    chunks = MarkdownHeadingChunker(
        ChunkingConfig(target_size=80, max_size=120, overlap=20)
    ).split(document)

    chunks_with_fence = [chunk for chunk in chunks if "```" in chunk.content]
    assert any(fenced_block in chunk.content for chunk in chunks_with_fence)
    assert all(chunk.content.count("```") % 2 == 0 for chunk in chunks_with_fence)
    assert any(chunk.char_count > 120 for chunk in chunks_with_fence)


def _source_document(content: str) -> RawDocument:
    return RawDocument(
        source_id="fence-test",
        source_kind=SourceKind.GIT,
        scope=Scope.KNOWLEDGE,
        visibility=Visibility.PUBLIC,
        priority=80,
        source_path="docs/fence.md",
        raw_content=content,
        revision="abc123",
    )
