from __future__ import annotations

import fnmatch
import subprocess
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import quote, urlsplit

from zglab_rag.domain.models import RawDocument, SourceDefinition, SourceKind
from zglab_rag.sources.base import SourceSnapshot
from zglab_rag.sources.errors import (
    NotGitRepositoryError,
    RepositoryMismatchError,
    SourceConfigurationError,
    SourcePathNotFoundError,
    SourceReadError,
)

_MARKDOWN_SUFFIXES = {".md", ".markdown"}


class LocalGitSource:
    """Discover allowlisted Markdown files in one registered local Git repository."""

    def __init__(
        self, project_root: str | Path, *, source_checkout_root: str | Path | None = None
    ) -> None:
        self._project_root = Path(project_root).resolve()
        self._source_checkout_root = (
            Path(source_checkout_root).resolve() if source_checkout_root is not None else None
        )

    def inspect(self, source: SourceDefinition) -> SourceSnapshot:
        repository_path = self._repository_path(source)
        revision = self._git(repository_path, "rev-parse", "HEAD")
        remote_url = self._optional_origin(repository_path)
        expected_remote = _acquisition_remote(source) or source.repository
        if remote_url and expected_remote:
            actual_repository = _repository_slug(remote_url)
            expected_repository = _repository_slug(expected_remote)
            if actual_repository.casefold() != expected_repository.casefold():
                raise RepositoryMismatchError(
                    f"Git source '{source.id}' origin '{remote_url}' does not match configured "
                    f"repository '{expected_remote}'"
                )

        document_paths = tuple(self._discover(repository_path, source))
        return SourceSnapshot(
            source_id=source.id,
            kind=source.kind,
            configured_path=self._configured_path(source),
            revision=revision,
            document_paths=document_paths,
            remote_url=remote_url,
        )

    def load(self, source: SourceDefinition) -> Iterable[RawDocument]:
        repository_path = self._repository_path(source)
        snapshot = self.inspect(source)
        for relative_path in snapshot.document_paths:
            document_path = repository_path / relative_path
            try:
                raw_content = document_path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                raise SourceReadError(
                    f"Unable to read source '{source.id}' document '{relative_path}': {exc}"
                ) from exc

            yield RawDocument(
                source_id=source.id,
                source_kind=source.kind,
                scope=source.scope,
                visibility=source.visibility,
                priority=source.priority,
                source_path=relative_path,
                raw_content=raw_content,
                source_url=_source_url(source.repository, snapshot.revision, relative_path),
                revision=snapshot.revision,
            )

    def _repository_path(self, source: SourceDefinition) -> Path:
        if source.kind != SourceKind.GIT:
            raise SourceConfigurationError(
                f"Source '{source.id}' has kind '{source.kind}', expected 'git'"
            )
        if source.acquisition:
            if self._source_checkout_root is None:
                raise SourceConfigurationError(
                    f"Git source '{source.id}' has acquisition metadata but no checkout root"
                )
            repository_path = self._source_checkout_root / source.id
            configured_path = str(repository_path)
        elif source.local_path:
            repository_path = (self._project_root / source.local_path).resolve()
            configured_path = source.local_path
        else:
            raise SourceConfigurationError(f"Git source '{source.id}' is missing a checkout path")
        if not repository_path.exists():
            raise SourcePathNotFoundError(
                f"Git source '{source.id}' checkout does not exist: {configured_path}"
            )
        if not repository_path.is_dir():
            raise NotGitRepositoryError(
                f"Git source '{source.id}' local_path is not a directory: {source.local_path}"
            )

        top_level = Path(
            self._git(repository_path, "rev-parse", "--show-toplevel")
        ).resolve()
        if top_level != repository_path:
            raise NotGitRepositoryError(
                f"Git source '{source.id}' local_path must point to the repository root: "
                f"{configured_path}"
            )
        return repository_path

    def _configured_path(self, source: SourceDefinition) -> str:
        if source.acquisition and self._source_checkout_root is not None:
            return str(self._source_checkout_root / source.id)
        return source.local_path or ""

    def _discover(self, repository_path: Path, source: SourceDefinition) -> list[str]:
        if not source.include:
            raise SourceConfigurationError(
                f"Git source '{source.id}' requires a non-empty include allowlist"
            )

        matches: set[str] = set()
        try:
            for pattern in source.include:
                for candidate in repository_path.glob(pattern):
                    if not _safe_regular_file(candidate, repository_path):
                        continue
                    if candidate.suffix.lower() not in _MARKDOWN_SUFFIXES:
                        continue
                    relative_path = candidate.relative_to(repository_path).as_posix()
                    if any(_matches_pattern(relative_path, rule) for rule in source.exclude):
                        continue
                    matches.add(relative_path)
        except (OSError, ValueError) as exc:
            raise SourceConfigurationError(
                f"Unable to evaluate patterns for Git source '{source.id}': {exc}"
            ) from exc
        return sorted(matches)

    @staticmethod
    def _git(repository_path: Path, *arguments: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(repository_path), *arguments],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "git command failed"
            raise NotGitRepositoryError(
                f"Invalid local Git repository '{repository_path}': {detail}"
            )
        return result.stdout.strip()

    @staticmethod
    def _optional_origin(repository_path: Path) -> str | None:
        result = subprocess.run(
            ["git", "-C", str(repository_path), "remote", "get-url", "origin"],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None


def _safe_regular_file(candidate: Path, repository_path: Path) -> bool:
    try:
        relative = candidate.relative_to(repository_path)
    except ValueError:
        return False

    current = repository_path
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            return False
    try:
        return candidate.is_file() and candidate.resolve().is_relative_to(repository_path)
    except OSError:
        return False


def _matches_pattern(path: str, pattern: str) -> bool:
    normalized = pattern.replace("\\", "/")
    return fnmatch.fnmatchcase(path, normalized) or (
        normalized.startswith("**/") and fnmatch.fnmatchcase(path, normalized[3:])
    )


def _repository_slug(value: str) -> str:
    normalized = value.strip().rstrip("/")
    if "://" in normalized:
        normalized = urlsplit(normalized).path
    elif ":" in normalized and "@" in normalized.split(":", maxsplit=1)[0]:
        normalized = normalized.split(":", maxsplit=1)[1]
    normalized = normalized.strip("/")
    if normalized.endswith(".git"):
        normalized = normalized[:-4]
    parts = normalized.split("/")
    if len(parts) < 2:
        raise SourceConfigurationError(f"Unable to identify repository from value: {value}")
    return "/".join(parts[-2:])


def _source_url(repository: str | None, revision: str | None, source_path: str) -> str | None:
    if not repository or not revision:
        return None
    repository_slug = _repository_slug(repository)
    return f"https://github.com/{repository_slug}/blob/{revision}/{quote(source_path, safe='/')}"


def _acquisition_remote(source: SourceDefinition) -> str | None:
    if source.acquisition is None:
        return None
    if source.acquisition.provider == "gitee":
        return source.acquisition.repository
    raise SourceConfigurationError(
        f"Git source '{source.id}' has unsupported acquisition provider "
        f"'{source.acquisition.provider}'"
    )
