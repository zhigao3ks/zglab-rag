from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from zglab_rag.domain.models import RawDocument, SourceDefinition, SourceKind
from zglab_rag.sources.base import SourceSnapshot
from zglab_rag.sources.errors import LocalSourceError

__all__ = ["LocalMarkdownSourceLoader", "LocalSourceError"]


class LocalMarkdownSourceLoader:
    """Read one explicitly registered Markdown file from the project root."""

    def __init__(self, project_root: str | Path) -> None:
        self._project_root = Path(project_root).resolve()

    def _source_path(self, source: SourceDefinition) -> Path:
        if source.kind != SourceKind.LOCAL:
            raise LocalSourceError(
                f"Source '{source.id}' has kind '{source.kind}', expected 'local'"
            )
        if not source.path:
            raise LocalSourceError(f"Local source '{source.id}' is missing required field 'path'")

        source_path = (self._project_root / source.path).resolve()
        if not source_path.is_relative_to(self._project_root):
            raise LocalSourceError(
                f"Local source '{source.id}' resolves outside the project root: {source.path}"
            )
        if source_path.suffix.lower() not in {".md", ".markdown"}:
            raise LocalSourceError(
                f"Local source '{source.id}' is not a Markdown document: {source.path}"
            )
        if not source_path.is_file():
            raise LocalSourceError(
                f"Registered local source '{source.id}' does not exist: {source.path}"
            )
        return source_path

    def inspect(self, source: SourceDefinition) -> SourceSnapshot:
        source_path = self._source_path(source)
        relative_path = source_path.relative_to(self._project_root).as_posix()
        return SourceSnapshot(
            source_id=source.id,
            kind=source.kind,
            configured_path=source.path or relative_path,
            revision=None,
            document_paths=(relative_path,),
        )

    def load(self, source: SourceDefinition) -> Iterable[RawDocument]:
        source_path = self._source_path(source)

        yield RawDocument(
            source_id=source.id,
            source_kind=source.kind,
            scope=source.scope,
            visibility=source.visibility,
            priority=source.priority,
            source_path=source_path.relative_to(self._project_root).as_posix(),
            raw_content=source_path.read_text(encoding="utf-8"),
        )
