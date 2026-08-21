"""Atomic SQLite knowledge-index backups for production operations."""

from __future__ import annotations

import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class BackupResult:
    """Result of one completed database backup."""

    path: Path
    removed: tuple[Path, ...]


def backup_database(
    database_path: str | Path,
    backup_dir: str | Path,
    *,
    retain_count: int = 7,
    now: datetime | None = None,
) -> BackupResult:
    """Create a consistent SQLite snapshot, atomically publish it and prune old backups.

    SQLite's backup API reads a consistent snapshot even when the source database is in
    WAL mode. The destination is first written to a uniquely named file in the target
    directory, then atomically moved into place; callers never observe a partial backup.
    """
    if retain_count <= 0:
        raise ValueError("retain_count must be positive")
    source_path = Path(database_path)
    if not source_path.is_file():
        raise FileNotFoundError(source_path)

    target_dir = Path(backup_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    timestamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    target_path = target_dir / f"knowledge-{timestamp}.db"
    if target_path.exists():
        raise FileExistsError(target_path)
    temporary_path = target_dir / f".knowledge-{uuid.uuid4().hex}.tmp"

    source = sqlite3.connect(f"file:{source_path.resolve()}?mode=ro", uri=True)
    destination = sqlite3.connect(temporary_path)
    try:
        source.backup(destination)
        destination.commit()
    finally:
        destination.close()
        source.close()

    try:
        with temporary_path.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary_path, target_path)
        _fsync_directory(target_dir)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise

    backups = sorted(target_dir.glob("knowledge-*.db"), key=lambda item: item.name, reverse=True)
    removed = tuple(backups[retain_count:])
    for expired in removed:
        expired.unlink()
    return BackupResult(path=target_path, removed=removed)


def _fsync_directory(path: Path) -> None:
    """Persist the rename on platforms that support directory fsync."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    except (AttributeError, OSError):
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
