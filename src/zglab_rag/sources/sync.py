"""Controlled acquisition of explicitly registered Git knowledge sources."""

from __future__ import annotations

import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from zglab_rag.domain.models import SourceDefinition, SourceKind
from zglab_rag.sources.errors import SourceConfigurationError, SourceReadError

GIT_COMMAND_TIMEOUT_SECONDS = 60


@dataclass(frozen=True, slots=True)
class GitSyncResult:
    source_id: str
    before_revision: str | None
    after_revision: str
    cloned: bool = False

    @property
    def changed(self) -> bool:
        return self.cloned or self.before_revision != self.after_revision


def gitee_clone_url(repository: str) -> str:
    """Build the only clone URL supported by this maintenance track."""
    parts = repository.split("/")
    if len(parts) != 2 or any(not part or "://" in part or "@" in part for part in parts):
        raise SourceConfigurationError(
            "Gitee acquisition repository must be an owner/repository slug"
        )
    return f"https://gitee.com/{repository}.git"


def fast_forward_registered_sources(
    sources: list[SourceDefinition],
    *,
    project_root: str | Path,
    source_checkout_root: str | Path | None = None,
) -> list[GitSyncResult]:
    """Bootstrap/update registered Git sources before ingestion and index access."""
    root = Path(project_root).resolve()
    managed_root = Path(source_checkout_root).resolve() if source_checkout_root else None
    results: list[GitSyncResult] = []
    for source in sources:
        if not source.enabled or source.kind != SourceKind.GIT:
            continue
        if source.acquisition:
            if managed_root is None:
                raise SourceConfigurationError("Managed acquisition requires source_checkout_root")
            results.append(_sync_managed(source, managed_root))
        else:
            results.append(_sync_legacy(source, root))
    return results


def _sync_managed(source: SourceDefinition, checkout_root: Path) -> GitSyncResult:
    repository = _managed_repository_path(source, checkout_root)
    expected_origin = _acquisition_url(source)
    if not repository.exists():
        return _clone_managed(source, repository, expected_origin)
    if not repository.is_dir():
        raise SourceConfigurationError(
            f"Git source '{source.id}' managed checkout is not a directory"
        )
    _verify_repository_root(repository, source.id)
    _require_clean(repository, source.id)
    _require_expected_origin(repository, source.id, expected_origin)
    return _fetch_fast_forward(repository, source)


def _sync_legacy(source: SourceDefinition, project_root: Path) -> GitSyncResult:
    repository = _legacy_repository_path(source, project_root)
    _verify_repository_root(repository, source.id)
    _require_clean(repository, source.id)
    if source.repository:
        _require_repository_slug(repository, source.id, source.repository)
    return _fetch_fast_forward(repository, source)


def _clone_managed(
    source: SourceDefinition, repository: Path, expected_origin: str
) -> GitSyncResult:
    repository.parent.mkdir(parents=True, exist_ok=True)
    temporary = repository.parent / f".{source.id}.clone-{uuid.uuid4().hex}"
    try:
        _git_at(
            repository.parent,
            source.id,
            "clone",
            "--branch",
            _ref(source),
            "--single-branch",
            expected_origin,
            str(temporary),
        )
        _verify_repository_root(temporary, source.id)
        _require_expected_origin(temporary, source.id, expected_origin)
        after = _git(temporary, source.id, "rev-parse", "HEAD")
        temporary.replace(repository)
        return GitSyncResult(source.id, None, after, cloned=True)
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)
        raise


def _fetch_fast_forward(repository: Path, source: SourceDefinition) -> GitSyncResult:
    before = _git(repository, source.id, "rev-parse", "HEAD")
    _git(repository, source.id, "fetch", "--prune", "origin", _ref(source))
    target = _git(repository, source.id, "rev-parse", "FETCH_HEAD")
    if target != before:
        _git(repository, source.id, "merge", "--ff-only", "FETCH_HEAD")
    return GitSyncResult(source.id, before, _git(repository, source.id, "rev-parse", "HEAD"))


def _managed_repository_path(source: SourceDefinition, checkout_root: Path) -> Path:
    candidate = (checkout_root / source.id).resolve()
    if candidate.parent != checkout_root:
        raise SourceConfigurationError(
            f"Git source '{source.id}' resolves outside managed checkout root"
        )
    return candidate


def _legacy_repository_path(source: SourceDefinition, root: Path) -> Path:
    if not source.local_path:
        raise SourceConfigurationError(f"Git source '{source.id}' is missing local_path")
    path = (root / source.local_path).resolve()
    if not path.is_dir():
        raise SourceConfigurationError(
            f"Git source '{source.id}' local_path is unavailable: {source.local_path}"
        )
    return path


def _acquisition_url(source: SourceDefinition) -> str:
    if source.acquisition is None:
        raise SourceConfigurationError(f"Git source '{source.id}' is missing acquisition metadata")
    if source.acquisition.provider != "gitee":
        raise SourceConfigurationError(
            f"Git source '{source.id}' has unsupported acquisition provider"
        )
    return gitee_clone_url(source.acquisition.repository)


def _ref(source: SourceDefinition) -> str:
    ref = source.ref or "HEAD"
    if ref.startswith("-"):
        raise SourceConfigurationError(f"Git source '{source.id}' has unsafe ref")
    return ref


def _verify_repository_root(repository: Path, source_id: str) -> None:
    top_level = Path(_git(repository, source_id, "rev-parse", "--show-toplevel")).resolve()
    if top_level != repository.resolve():
        raise SourceConfigurationError(
            f"Git source '{source_id}' checkout must be the repository root"
        )


def _require_clean(repository: Path, source_id: str) -> None:
    if _git(repository, source_id, "status", "--porcelain"):
        raise SourceReadError(f"Git source '{source_id}' has uncommitted local changes")


def _require_expected_origin(repository: Path, source_id: str, expected_origin: str) -> None:
    actual = _git(repository, source_id, "remote", "get-url", "origin")
    if _normalise_gitee_url(actual) != _normalise_gitee_url(expected_origin):
        raise SourceReadError(
            f"Git source '{source_id}' origin does not match configured acquisition mirror"
        )


def _require_repository_slug(repository: Path, source_id: str, expected_slug: str) -> None:
    actual = _git(repository, source_id, "remote", "get-url", "origin")
    actual_path = urlsplit(actual).path.rstrip("/").removesuffix(".git")
    if actual_path.rsplit("/", 2)[-2:] != expected_slug.split("/"):
        raise SourceReadError(
            f"Git source '{source_id}' origin does not match configured repository"
        )


def _normalise_gitee_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or parsed.netloc != "gitee.com" or parsed.query or parsed.fragment:
        return ""
    path = parsed.path.rstrip("/")
    return f"https://gitee.com{path if path.endswith('.git') else path + '.git'}"


def _git(repository: Path, source_id: str, *arguments: str) -> str:
    return _git_at(repository, source_id, *arguments, cwd_as_git=True)


def _git_at(directory: Path, source_id: str, *arguments: str, cwd_as_git: bool = False) -> str:
    command = ["git"]
    if cwd_as_git:
        command.extend(["-C", str(directory)])
    command.extend(arguments)
    try:
        result = subprocess.run(
            command,
            cwd=None if cwd_as_git else directory,
            check=False,
            capture_output=True,
            text=True,
            timeout=GIT_COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise SourceReadError(
            f"Git source '{source_id}' sync timed out after {GIT_COMMAND_TIMEOUT_SECONDS} seconds"
        ) from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "git command failed"
        raise SourceReadError(f"Git source '{source_id}' sync failed: {detail}")
    return result.stdout.strip()
