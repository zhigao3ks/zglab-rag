from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from zglab_rag.domain.models import RawDocument, SourceDefinition, SourceKind


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    source_id: str
    kind: SourceKind
    configured_path: str
    revision: str | None
    document_paths: tuple[str, ...]
    remote_url: str | None = None


class SourceAdapter(Protocol):
    def inspect(self, source: SourceDefinition) -> SourceSnapshot: ...

    def load(self, source: SourceDefinition) -> Iterable[RawDocument]: ...
