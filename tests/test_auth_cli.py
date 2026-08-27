"""Phase 11 tests for the admin CLI (`zglab-rag user`, `auth init`, backup --auth).

The CLI is the only admin surface in Phase 11 (no Web Admin Console), so
these tests cover provisioning output, one-time activation URLs, state
transitions and auth.db backups end to end through cli.main().
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

from zglab_rag.cli import main


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    auth_db = tmp_path / "auth.db"
    monkeypatch.setenv("ZGLAB_RAG_AUTH_DATABASE_PATH", str(auth_db))
    monkeypatch.setenv("ZGLAB_RAG_AUTH_PUBLIC_BASE_URL", "https://ask.zglab.fun")
    monkeypatch.setenv("ZGLAB_RAG_BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setenv("ZGLAB_RAG_DATABASE_PATH", str(tmp_path / "knowledge.db"))
    return auth_db


def _activation_url_from(output: str) -> str:
    match = re.search(r"activation_url=(\S+)", output)
    assert match, output
    return match.group(1)


def test_auth_init_creates_database(cli_env: Path, capsys: pytest.CaptureFixture) -> None:
    assert main(["auth", "init"]) == 0
    captured = capsys.readouterr()
    assert "schema_version=3" in captured.out
    assert cli_env.is_file()


def test_user_create_outputs_single_use_activation_url(
    cli_env: Path, capsys: pytest.CaptureFixture
) -> None:
    assert main(["user", "create", "alice"]) == 0
    captured = capsys.readouterr()
    url = _activation_url_from(captured.out)
    # Fragment transport: the token lives after '#', so it is never sent
    # to the server (no Nginx access log / Referer exposure).
    assert url.startswith("https://ask.zglab.fun/activate#token=")
    assert "/activate/" not in url
    assert "status=PENDING" in captured.out

    token = url.split("#token=", 1)[-1]
    # The plaintext token must not exist in the database; only its hash.
    raw = sqlite3.connect(cli_env)
    dump = "\n".join(raw.iterdump())
    raw.close()
    assert token not in dump


def test_user_create_admin_role(cli_env: Path, capsys: pytest.CaptureFixture) -> None:
    assert main(["user", "create", "owner", "--role", "ADMIN"]) == 0
    capsys.readouterr()
    assert main(["user", "show", "owner"]) == 0
    assert "role=ADMIN" in capsys.readouterr().out


def test_user_create_duplicate_fails(cli_env: Path, capsys: pytest.CaptureFixture) -> None:
    assert main(["user", "create", "alice"]) == 0
    capsys.readouterr()
    assert main(["user", "create", "ALICE"]) == 1
    assert "DuplicateUsernameError" in capsys.readouterr().err


def test_user_list_and_show_never_reprint_token(
    cli_env: Path, capsys: pytest.CaptureFixture
) -> None:
    assert main(["user", "create", "alice"]) == 0
    first_output = capsys.readouterr().out
    url = _activation_url_from(first_output)

    assert main(["user", "list"]) == 0
    list_output = capsys.readouterr().out
    assert "user=alice" in list_output
    assert url not in list_output

    assert main(["user", "show", "alice"]) == 0
    show_output = capsys.readouterr().out
    assert url not in show_output
    assert "password_hash" not in show_output


def test_user_disable_enable_and_revoke(cli_env: Path, capsys: pytest.CaptureFixture) -> None:
    assert main(["user", "create", "alice"]) == 0
    capsys.readouterr()

    assert main(["user", "disable", "alice"]) == 0
    assert "status=DISABLED" in capsys.readouterr().out

    assert main(["user", "enable", "alice"]) == 0
    assert "status=ACTIVE" in capsys.readouterr().out

    assert main(["user", "revoke-sessions", "alice"]) == 0
    assert "revoked_sessions=0" in capsys.readouterr().out


def test_user_reset_password_outputs_reset_url(
    cli_env: Path, capsys: pytest.CaptureFixture
) -> None:
    assert main(["user", "create", "alice"]) == 0
    capsys.readouterr()
    assert main(["user", "reset-password", "alice"]) == 0
    output = capsys.readouterr().out
    match = re.search(r"reset_url=(\S+)", output)
    assert match
    assert "sessions=revoked" in output
    assert "credential=RESET_REQUIRED" not in output  # PENDING: no credential yet
    assert "purpose=ACTIVATE_ACCOUNT" in output  # still PENDING -> activation
    assert "#token=" in match.group(1)
    assert "purpose=reset" not in match.group(1)


def test_user_reset_password_for_active_user_uses_reset_fragment(
    cli_env: Path, capsys: pytest.CaptureFixture
) -> None:
    """An activated user's reset URL carries purpose=reset in the fragment,
    and the old password stops working immediately after the CLI call."""
    from zglab_rag.auth.audit import AuditLogger
    from zglab_rag.auth.database import AuthDatabase
    from zglab_rag.auth.identity import IdentityService
    from zglab_rag.auth.session import SessionService

    assert main(["user", "create", "alice"]) == 0
    create_output = capsys.readouterr().out
    token = _activation_url_from(create_output).split("#token=", 1)[-1]

    database = AuthDatabase(cli_env)
    connection = database.connect(initialize=True)
    try:
        identity = IdentityService(connection, AuditLogger(connection))
        sessions = SessionService(connection, AuditLogger(connection))
        identity.activate_account(token, "first-long-password-1")
        sessions.login("alice", "first-long-password-1")  # works before reset

        assert main(["user", "reset-password", "alice"]) == 0
        output = capsys.readouterr().out
        match = re.search(r"reset_url=(\S+)", output)
        assert match
        assert match.group(1).startswith("https://ask.zglab.fun/activate#token=")
        assert "&purpose=reset" in match.group(1)
        assert "credential=RESET_REQUIRED" in output

        # Old password lost login ability immediately, token unconsumed.
        from zglab_rag.auth.errors import InvalidCredentialsError

        with pytest.raises(InvalidCredentialsError):
            sessions.login("alice", "first-long-password-1")

        # Consuming the reset URL restores login with the new password.
        reset_token = match.group(1).split("#token=", 1)[1].split("&")[0]
        identity.reset_password_with_token(reset_token, "second-long-password-2")
        sessions.login("alice", "second-long-password-2")  # must not raise
    finally:
        connection.close()


def test_user_commands_fail_for_unknown_user(
    cli_env: Path, capsys: pytest.CaptureFixture
) -> None:
    assert main(["user", "disable", "ghost"]) == 1
    assert "UserNotFoundError" in capsys.readouterr().err


def test_backup_auth_database(cli_env: Path, capsys: pytest.CaptureFixture) -> None:
    assert main(["auth", "init"]) == 0
    capsys.readouterr()
    assert main(["backup", "--auth"]) == 0
    output = capsys.readouterr().out
    match = re.search(r"backup=(\S+)", output)
    assert match
    backup_path = Path(match.group(1))
    assert backup_path.is_file()
    assert backup_path.name.startswith("auth-")
