from pathlib import Path

from zglab_rag.domain.models import SourceDefinition, SourceKind
from zglab_rag.sources.base import SourceAdapter
from zglab_rag.sources.errors import SourceConfigurationError
from zglab_rag.sources.local_git import LocalGitSource
from zglab_rag.sources.local_markdown import LocalMarkdownSourceLoader


def create_source_adapter(
    source: SourceDefinition,
    *,
    project_root: str | Path,
) -> SourceAdapter:
    if source.kind == SourceKind.LOCAL:
        return LocalMarkdownSourceLoader(project_root)
    if source.kind == SourceKind.GIT:
        return LocalGitSource(project_root)
    raise SourceConfigurationError(
        f"Source '{source.id}' kind '{source.kind}' has no local acquisition adapter"
    )
