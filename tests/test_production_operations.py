from __future__ import annotations

import json
import logging
import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.test_public_api import FakeRuntime, _make_answered_result
from zglab_rag.api.concurrency import ConcurrencyGuard
from zglab_rag.api.main import create_app
from zglab_rag.api.rate_limit import RateLimiter
from zglab_rag.cli import _backup_before_apply
from zglab_rag.config import Settings
from zglab_rag.production.backup import backup_database
from zglab_rag.sources.errors import SourceReadError
from zglab_rag.sources.sync import _git


def test_sqlite_backup_is_consistent_atomic_and_prunes_old_files(tmp_path: Path) -> None:
    database_path = tmp_path / "knowledge.db"
    connection = sqlite3.connect(database_path)
    connection.execute("CREATE TABLE records(value TEXT)")
    connection.execute("INSERT INTO records(value) VALUES ('before-backup')")
    connection.commit()
    connection.close()

    backup_dir = tmp_path / "backups"
    first = backup_database(
        database_path,
        backup_dir,
        retain_count=2,
        now=datetime(2026, 8, 1, tzinfo=UTC),
    )
    second = backup_database(
        database_path,
        backup_dir,
        retain_count=2,
        now=datetime(2026, 8, 2, tzinfo=UTC),
    )
    copied = sqlite3.connect(first.path)
    assert copied.execute("SELECT value FROM records").fetchone()[0] == "before-backup"
    copied.close()
    third = backup_database(
        database_path,
        backup_dir,
        retain_count=2,
        now=datetime(2026, 8, 3, tzinfo=UTC),
    )

    assert second.path.exists()
    assert third.removed == (first.path,)
    assert sorted(item.name for item in backup_dir.iterdir()) == [
        "knowledge-20260802T000000Z.db",
        "knowledge-20260803T000000Z.db",
    ]


def _app_with_fake_runtime() -> tuple:
    settings = Settings(
        api_max_concurrent_requests=2,
        api_rate_limit_requests=10,
        api_cors_origins=["http://testserver"],
    )
    runtime = FakeRuntime(settings=settings)
    runtime.service.result = _make_answered_result("不会记录的问题")
    app = create_app(
        runtime=runtime,
        settings=settings,
        concurrency_guard=ConcurrencyGuard(max_concurrent=2),
        rate_limiter=RateLimiter(max_requests=10, window_seconds=60),
    )
    return app, runtime


def test_ready_requires_lifespan_then_reports_ready() -> None:
    app, _ = _app_with_fake_runtime()
    with TestClient(app) as client:
        response = client.get("/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_access_log_is_structured_and_excludes_question(caplog) -> None:
    caplog.set_level(logging.INFO, logger="zglab_rag.api.main")
    app, _ = _app_with_fake_runtime()
    with TestClient(app) as client:
        response = client.post("/api/v1/ask", json={"question": "绝不应写入日志的完整问题"})

    assert response.status_code == 200
    records = [
        record.message for record in caplog.records if '"event":"http_request"' in record.message
    ]
    assert records
    payload = json.loads(records[-1])
    assert payload["path"] == "/api/v1/ask"
    assert payload["status"] == 200
    assert payload["error_code"] is None
    assert "绝不应写入日志的完整问题" not in "\n".join(record.message for record in caplog.records)


def test_backup_rejects_missing_database(tmp_path: Path) -> None:
    missing = tmp_path / "missing.db"
    try:
        backup_database(missing, tmp_path / "backups")
    except FileNotFoundError as exc:
        assert exc.filename is None or "missing.db" in str(exc)
    else:
        raise AssertionError("expected missing database backup to fail")


def test_initial_index_skips_pre_apply_backup(tmp_path: Path) -> None:
    result = _backup_before_apply(tmp_path / "knowledge.db", tmp_path / "backups", 7)

    assert result is None
    assert not (tmp_path / "backups").exists()


def test_git_sync_timeout_is_reported_before_ingestion(
    monkeypatch, tmp_path: Path
) -> None:
    def _timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("git", 60)

    monkeypatch.setattr("zglab_rag.sources.sync.subprocess.run", _timeout)

    with pytest.raises(SourceReadError, match="timed out after 60 seconds"):
        _git(tmp_path, "notes", "fetch", "origin", "main")
