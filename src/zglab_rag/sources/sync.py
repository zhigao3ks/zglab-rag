"""Explicit, registered Git checkout synchronization for production operations."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from zglab_rag.domain.models import SourceDefinition, SourceKind
from zglab_rag.sources.errors import SourceConfigurationError, SourceReadError

GIT_COMMAND_TIMEOUT_SECONDS = 60


@dataclass(frozen=True, slots=True)
class GitSyncResult:
    """One registered Git checkout's fast-forward result."""

    source_id: str
    before_revision: str
    after_revision: str

    @property
    def changed(self) -> bool:
        return self.before_revision != self.after_revision


def fast_forward_registered_sources(
    sources: list[SourceDefinition], *, project_root: str | Path
) -> list[GitSyncResult]:
    """Fetch and fast-forward only configured Git repositories.

    A dirty checkout or any fetch/merge error stops before ingestion. The index is not
    opened by this function, so a source-sync failure cannot alter the serving index.
    """
    root = Path(project_root).resolve()
    results: list[GitSyncResult] = []
    for source in sources:
        if source.kind != SourceKind.GIT:
            continue
        repository = _repository_path(source, root)
        _require_clean(repository, source.id)
        before = _git(repository, source.id, "rev-parse", "HEAD")
        ref = source.ref or "HEAD"
        _git(repository, source.id, "fetch", "--prune", "origin", ref)
        target = _git(repository, source.id, "rev-parse", "FETCH_HEAD")
        if target != before:
            _git(repository, source.id, "merge", "--ff-only", "FETCH_HEAD")
        after = _git(repository, source.id, "rev-parse", "HEAD")
        results.append(GitSyncResult(source.id, before, after))
    return results


def _repository_path(source: SourceDefinition, root: Path) -> Path:
    if not source.local_path:
        raise SourceConfigurationError(f"Git source '{source.id}' is missing local_path")
    path = (root / source.local_path).resolve()
    if not path.is_dir():
        raise SourceConfigurationError(
            f"Git source '{source.id}' local_path is unavailable: {source.local_path}"
        )
    top_level = Path(_git(path, source.id, "rev-parse", "--show-toplevel")).resolve()
    if top_level != path:
        raise SourceConfigurationError(
            f"Git source '{source.id}' local_path is not a repository root: {source.local_path}"
        )
    return path


def _require_clean(repository: Path, source_id: str) -> None:
    if _git(repository, source_id, "status", "--porcelain"):
        raise SourceReadError(f"Git source '{source_id}' has uncommitted local changes")


def _git(repository: Path, source_id: str, *arguments: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repository), *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=GIT_COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise SourceReadError(
            f"Git source '{source_id}' sync timed out after "
            f"{GIT_COMMAND_TIMEOUT_SECONDS} seconds"
        ) from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "git command failed"
        raise SourceReadError(f"Git source '{source_id}' sync failed: {detail}")
    return result.stdout.strip()
