from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from zglab_rag.domain.models import Scope, SourceDefinition, SourceKind, Visibility
from zglab_rag.sources.errors import SourceReadError
from zglab_rag.sources.local_git import LocalGitSource
from zglab_rag.sources.sync import fast_forward_registered_sources, gitee_clone_url


def _git(directory: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(directory), *arguments], check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _source(*, source_id: str = "notes") -> SourceDefinition:
    return SourceDefinition(
        id=source_id,
        kind=SourceKind.GIT,
        scope=Scope.KNOWLEDGE,
        visibility=Visibility.PUBLIC,
        repository="zhigao3ks/notes",
        ref="main",
        acquisition={"provider": "gitee", "repository": "Zg443/notes"},
        include=["README.md"],
    )


@pytest.fixture
def origin_repository(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    work.mkdir()
    (work / "README.md").write_text("# Notes\n", encoding="utf-8")
    _git(work, "init", "--initial-branch=main")
    _git(work, "config", "user.name", "Test User")
    _git(work, "config", "user.email", "test@example.com")
    _git(work, "add", "README.md")
    _git(work, "commit", "-m", "initial")
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", str(origin))
    _git(work, "remote", "add", "origin", str(origin))
    _git(work, "push", "origin", "main")
    _git(origin, "symbolic-ref", "HEAD", "refs/heads/main")
    return origin


def _local_mirror(monkeypatch: pytest.MonkeyPatch, origin: Path) -> None:
    """Use a local Git transport while retaining the production lifecycle test."""
    monkeypatch.setattr("zglab_rag.sources.sync._acquisition_url", lambda _source: str(origin))
    monkeypatch.setattr("zglab_rag.sources.sync._normalise_gitee_url", lambda value: value)


def test_gitee_url_keeps_acquisition_namespace_separate_from_canonical() -> None:
    source = _source()

    assert source.repository == "zhigao3ks/notes"
    assert gitee_clone_url(source.acquisition.repository) == "https://gitee.com/Zg443/notes.git"


def test_missing_managed_checkout_is_cloned_and_provenance_stays_github(
    monkeypatch: pytest.MonkeyPatch, origin_repository: Path, tmp_path: Path
) -> None:
    _local_mirror(monkeypatch, origin_repository)
    source = _source()
    root = tmp_path / "sources"

    [result] = fast_forward_registered_sources(
        [source], project_root=tmp_path, source_checkout_root=root
    )

    assert result.cloned is True
    checkout = root / "notes"
    assert checkout.is_dir()
    _git(checkout, "remote", "set-url", "origin", "https://gitee.com/Zg443/notes.git")
    documents = list(LocalGitSource(tmp_path, source_checkout_root=root).load(source))
    assert documents[0].source_url == (
        f"https://github.com/zhigao3ks/notes/blob/{result.after_revision}/README.md"
    )


def test_existing_managed_checkout_fetches_and_fast_forwards(
    monkeypatch: pytest.MonkeyPatch, origin_repository: Path, tmp_path: Path
) -> None:
    _local_mirror(monkeypatch, origin_repository)
    source = _source()
    root = tmp_path / "sources"
    fast_forward_registered_sources([source], project_root=tmp_path, source_checkout_root=root)
    checkout = root / "notes"
    before = _git(checkout, "rev-parse", "HEAD")

    writer = tmp_path / "writer"
    _git(tmp_path, "clone", str(origin_repository), str(writer))
    _git(writer, "config", "user.name", "Test User")
    _git(writer, "config", "user.email", "test@example.com")
    (writer / "README.md").write_text("# Updated notes\n", encoding="utf-8")
    _git(writer, "add", "README.md")
    _git(writer, "commit", "-m", "update")
    _git(writer, "push", "origin", "main")

    [result] = fast_forward_registered_sources(
        [source], project_root=tmp_path, source_checkout_root=root
    )

    assert result.cloned is False
    assert result.changed is True
    assert result.before_revision == before
    assert _git(checkout, "rev-parse", "HEAD") == result.after_revision


def test_dirty_or_origin_mismatch_managed_checkout_fails_closed(
    monkeypatch: pytest.MonkeyPatch, origin_repository: Path, tmp_path: Path
) -> None:
    _local_mirror(monkeypatch, origin_repository)
    source = _source()
    root = tmp_path / "sources"
    fast_forward_registered_sources([source], project_root=tmp_path, source_checkout_root=root)
    checkout = root / "notes"
    (checkout / "README.md").write_text("local edit\n", encoding="utf-8")

    with pytest.raises(SourceReadError, match="uncommitted local changes"):
        fast_forward_registered_sources([source], project_root=tmp_path, source_checkout_root=root)

    _git(checkout, "checkout", "--", "README.md")
    _git(checkout, "remote", "set-url", "origin", "https://gitee.com/other/notes.git")
    with pytest.raises(SourceReadError, match="does not match configured acquisition mirror"):
        fast_forward_registered_sources([source], project_root=tmp_path, source_checkout_root=root)


def test_non_fast_forward_update_fails_closed(
    monkeypatch: pytest.MonkeyPatch, origin_repository: Path, tmp_path: Path
) -> None:
    _local_mirror(monkeypatch, origin_repository)
    source = _source()
    root = tmp_path / "sources"
    fast_forward_registered_sources([source], project_root=tmp_path, source_checkout_root=root)
    checkout = root / "notes"
    _git(checkout, "config", "user.name", "Test User")
    _git(checkout, "config", "user.email", "test@example.com")
    (checkout / "local.md").write_text("# Local commit\n", encoding="utf-8")
    _git(checkout, "add", "local.md")
    _git(checkout, "commit", "-m", "local divergence")

    writer = tmp_path / "writer"
    _git(tmp_path, "clone", str(origin_repository), str(writer))
    _git(writer, "config", "user.name", "Test User")
    _git(writer, "config", "user.email", "test@example.com")
    (writer / "remote.md").write_text("# Remote commit\n", encoding="utf-8")
    _git(writer, "add", "remote.md")
    _git(writer, "commit", "-m", "remote divergence")
    _git(writer, "push", "origin", "main")

    with pytest.raises(SourceReadError, match="sync failed"):
        fast_forward_registered_sources([source], project_root=tmp_path, source_checkout_root=root)


def test_clone_failure_removes_incomplete_checkout(
    monkeypatch: pytest.MonkeyPatch, origin_repository: Path, tmp_path: Path
) -> None:
    _local_mirror(monkeypatch, origin_repository)
    source = _source()
    root = tmp_path / "sources"

    def fail_clone(_directory, _source_id, *arguments, **_kwargs):
        temporary = Path(arguments[-1])
        temporary.mkdir(parents=True)
        (temporary / "partial").write_text("incomplete", encoding="utf-8")
        raise SourceReadError("clone failed")

    monkeypatch.setattr("zglab_rag.sources.sync._git_at", fail_clone)
    with pytest.raises(SourceReadError, match="clone failed"):
        fast_forward_registered_sources([source], project_root=tmp_path, source_checkout_root=root)

    assert not (root / "notes").exists()
    assert not list(root.glob(".notes.clone-*"))


def test_disabled_and_local_sources_are_not_bootstrapped(tmp_path: Path) -> None:
    disabled = _source()
    disabled.enabled = False
    local = SourceDefinition(
        id="identity-profile",
        kind=SourceKind.LOCAL,
        scope=Scope.IDENTITY,
        visibility=Visibility.PUBLIC,
        path="knowledge/identity/profile.md",
    )

    # The acquisition boundary itself ignores disabled and LOCAL registrations.
    assert fast_forward_registered_sources([disabled, local], project_root=tmp_path) == []


@pytest.mark.parametrize("repository", ["https://example.test/a/b.git", "a/b/c", "a/../b"])
def test_arbitrary_acquisition_urls_and_source_path_traversal_are_rejected(
    repository: str,
) -> None:
    with pytest.raises(ValidationError, match="owner/repository slug"):
        SourceDefinition(
            id="../escape",
            kind=SourceKind.GIT,
            scope=Scope.KNOWLEDGE,
            visibility=Visibility.PUBLIC,
            repository="zhigao3ks/notes",
            acquisition={"provider": "gitee", "repository": repository},
            include=["README.md"],
        )


def test_source_id_cannot_escape_managed_checkout_root() -> None:
    with pytest.raises(ValidationError, match="filesystem-safe identifier"):
        _source(source_id="../escape")
