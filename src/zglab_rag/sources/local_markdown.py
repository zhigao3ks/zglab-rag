from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from zglab_rag.domain.models import RawDocument, SourceDefinition, SourceKind


class LocalSourceError(ValueError):
    """Raised when a configured local source cannot be loaded safely."""


class LocalMarkdownSourceLoader:
    """Read one explicitly registered Markdown file from the project root."""

    def __init__(self, project_root: str | Path) -> None:
        self._project_root = Path(project_root).resolve()

    def load(self, source: SourceDefinition) -> Iterable[RawDocument]:
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

        yield RawDocument(
            source_id=source.id,
            source_kind=source.kind,
            scope=source.scope,
            visibility=source.visibility,
            priority=source.priority,
            source_path=source_path.relative_to(self._project_root).as_posix(),
            raw_content=source_path.read_text(encoding="utf-8"),
        )
