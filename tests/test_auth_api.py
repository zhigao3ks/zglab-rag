"""Phase 11B/11C tests: session authentication, CSRF/Origin, protected API v2.

Covers: login/logout/me, cookie security flags, hash-only session storage,
session expiration and revocation paths (logout / admin revoke / reset /
disable), login throttling (per-IP and per-username), no account
enumeration, CSRF + Origin enforcement including SSE, anonymous rejection,
per-user quota, LLM kill switch and v1 retirement.

All tests use fake generation runtimes; no model download or real LLM call.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.test_public_api import FakeRuntime
from zglab_rag.api.concurrency import ConcurrencyGuard
from zglab_rag.api.main import create_app, validate_production_security_settings
from zglab_rag.api.security import AuthRuntime
from zglab_rag.auth.models import UserRole
from zglab_rag.config import Settings

ORIGIN = "http://testserver"
ACTIVE_PASSWORD = "very-long-password-42"
NEW_PASSWORD = "another-long-password-77"
ASK_URL = "/api/v2/ask"
STREAM_URL = "/api/v2/ask/stream"
LOGIN_URL = "/api/v2/auth/login"
LOGOUT_URL = "/api/v2/auth/logout"
ME_URL = "/api/v2/auth/me"
ACTIVATE_URL = "/api/v2/auth/activate"
RESET_PASSWORD_URL = "/api/v2/auth/reset-password"
CHANGE_PASSWORD_URL = "/api/v2/auth/change-password"


def build_app(tmp_path: Path, **overrides):
    """Build an app with a real auth runtime and a fake generation runtime."""
    defaults = dict(
        auth_database_path=tmp_path / "auth.db",
        # Local HTTP test transport cannot carry Secure cookies; the
        # dev/test combination must use a plain (non-__Host-) cookie name,
        # exactly like local HTTP development.
        auth_cookie_name="zglab_session_test",
        auth_cookie_secure=False,
        auth_public_base_url=ORIGIN,
    )
    defaults.update(overrides)
    settings = Settings(**defaults)
    auth_runtime = AuthRuntime.from_settings(settings)
    runtime = FakeRuntime(settings=settings)
    app = create_app(runtime=runtime, settings=settings, auth_runtime=auth_runtime)
    return app, settings, auth_runtime, runtime


def provision_active_user(
    auth_runtime: AuthRuntime,
    settings: Settings,
    username: str = "alice",
    password: str = ACTIVE_PASSWORD,
    role: UserRole = UserRole.USER,
) -> None:
    with auth_runtime.connection() as connection:
        service = auth_runtime.identity_service(connection, settings)
        provisioned = service.provision_user(username, role=role)
        service.activate_account(provisioned.token, password)


def provision_pending_user(auth_runtime: AuthRuntime, settings: Settings, username: str = "bob"):
    with auth_runtime.connection() as connection:
        service = auth_runtime.identity_service(connection, settings)
        return service.provision_user(username)


def login(
    client: TestClient, username: str = "alice", password: str = ACTIVE_PASSWORD
):
    return client.post(
        LOGIN_URL,
        json={"username": username, "password": password},
        headers={"origin": ORIGIN},
    )


def authed_client(tmp_path: Path, **overrides):
    """Return (client, app tuple parts) with an already logged-in session."""
    app, settings, auth_runtime, runtime = build_app(tmp_path, **overrides)
    provision_active_user(auth_runtime, settings)
    client = TestClient(app)
    response = login(client)
    assert response.status_code == 200
    csrf = response.json()["csrf_token"]
    return client, app, settings, auth_runtime, runtime, csrf


def ask_headers(csrf: str) -> dict[str, str]:
    return {"origin": ORIGIN, "x-csrf-token": csrf}


def ask_once(client: TestClient, csrf: str, question: str) -> int:
    return client.post(
        ASK_URL, json={"question": question}, headers=ask_headers(csrf)
    ).status_code


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


def test_login_success_sets_secure_cookie_and_returns_csrf(tmp_path: Path) -> None:
    app, settings, auth_runtime, _runtime = build_app(tmp_path)
    provision_active_user(auth_runtime, settings)
    client = TestClient(app)

    response = login(client)
    assert response.status_code == 200
    body = response.json()
    assert body["user"]["username"] == "alice"
    assert body["user"]["role"] == "USER"
    assert body["csrf_token"]

    set_cookie = response.headers["set-cookie"]
    cookie_value = client.cookies.get(settings.auth_cookie_name)
    assert cookie_value
    # The plaintext session token only lives in the cookie, never the body.
    assert cookie_value not in response.text
    assert settings.auth_cookie_name in set_cookie
    assert "httponly" in set_cookie.lower()
    assert "samesite=lax" in set_cookie.lower()
    assert "path=/" in set_cookie.lower()
    assert "domain=" not in set_cookie.lower()


def test_login_secure_flag_in_production_mode(tmp_path: Path) -> None:
    app, settings, auth_runtime, _runtime = build_app(tmp_path, auth_cookie_secure=True)
    provision_active_user(auth_runtime, settings)
    client = TestClient(app)
    response = login(client)
    assert response.status_code == 200
    assert "secure" in response.headers["set-cookie"].lower()


def test_login_failure_returns_unified_error(tmp_path: Path) -> None:
    app, settings, auth_runtime, _runtime = build_app(tmp_path)
    provision_active_user(auth_runtime, settings)
    client = TestClient(app)

    wrong_password = login(client, password="totally-wrong-password-1")
    unknown_user = login(client, username="ghost")

    assert wrong_password.status_code == 401
    assert unknown_user.status_code == 401
    # No account enumeration: identical code and message for both cases.
    assert wrong_password.json()["error"] == unknown_user.json()["error"]
    assert wrong_password.json()["error"]["code"] == "INVALID_CREDENTIALS"


def test_login_rejects_pending_and_disabled_accounts_with_same_error(
    tmp_path: Path,
) -> None:
    app, settings, auth_runtime, _runtime = build_app(tmp_path)
    provision_active_user(auth_runtime, settings)
    provision_pending_user(auth_runtime, settings, "pending-user")
    client = TestClient(app)

    pending = login(client, username="pending-user", password="whatever-long-password")
    assert pending.status_code == 401
    assert pending.json()["error"]["code"] == "INVALID_CREDENTIALS"

    with auth_runtime.connection() as connection:
        auth_runtime.identity_service(connection, settings).set_enabled(
            "alice", enabled=False
        )
    disabled = login(client)
    assert disabled.status_code == 401
    assert disabled.json()["error"]["code"] == "INVALID_CREDENTIALS"


def test_session_database_stores_hash_only(tmp_path: Path) -> None:
    client, _app, settings, _auth, _runtime, _csrf = authed_client(tmp_path)
    cookie_value = client.cookies.get(settings.auth_cookie_name)
    raw = sqlite3.connect(settings.auth_database_path)
    dump = "\n".join(raw.iterdump())
    raw.close()
    assert cookie_value not in dump


def test_login_origin_rejected(tmp_path: Path) -> None:
    app, settings, auth_runtime, _runtime = build_app(tmp_path)
    provision_active_user(auth_runtime, settings)
    client = TestClient(app)
    response = client.post(
        LOGIN_URL,
        json={"username": "alice", "password": ACTIVE_PASSWORD},
        headers={"origin": "http://evil.example"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_REJECTED"


# ---------------------------------------------------------------------------
# Login throttling
# ---------------------------------------------------------------------------


def test_per_ip_login_throttling(tmp_path: Path) -> None:
    app, settings, auth_runtime, _runtime = build_app(
        tmp_path, auth_login_per_ip_attempts=3
    )
    provision_active_user(auth_runtime, settings)
    client = TestClient(app)
    for _ in range(3):
        assert login(client, password="wrong-password-long").status_code == 401
    throttled = login(client, password="wrong-password-long")
    assert throttled.status_code == 429
    assert throttled.json()["error"]["code"] == "RATE_LIMITED"
    assert "Retry-After" in throttled.headers


def test_per_username_login_throttling(tmp_path: Path) -> None:
    app, settings, auth_runtime, _runtime = build_app(
        tmp_path,
        auth_login_per_ip_attempts=100,
        auth_login_per_username_attempts=2,
    )
    provision_active_user(auth_runtime, settings)
    client = TestClient(app)
    assert login(client, password="wrong-password-long").status_code == 401
    assert login(client, password="wrong-password-long").status_code == 401
    # Even with the correct password, the username window is exhausted.
    throttled = login(client)
    assert throttled.status_code == 429
    assert throttled.json()["error"]["code"] == "RATE_LIMITED"


# ---------------------------------------------------------------------------
# Session lifecycle: me / logout / revocation / expiration
# ---------------------------------------------------------------------------


def test_me_restores_session_and_anonymous_rejected(tmp_path: Path) -> None:
    client, _app, _settings, _auth, _runtime, _csrf = authed_client(tmp_path)
    me = client.get(ME_URL)
    assert me.status_code == 200
    assert me.json()["user"]["username"] == "alice"
    assert me.json()["csrf_token"]

    anonymous = TestClient(build_app(tmp_path)[0])
    denied = anonymous.get(ME_URL)
    assert denied.status_code == 401
    assert denied.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"


def test_logout_revokes_session_immediately(tmp_path: Path) -> None:
    client, _app, settings, _auth, _runtime, csrf = authed_client(tmp_path)
    old_cookie = client.cookies.get(settings.auth_cookie_name)

    logout = client.post(LOGOUT_URL, headers=ask_headers(csrf))
    assert logout.status_code == 200
    assert logout.json()["result"] == "logged_out"

    # The old cookie must be useless even if restored.
    client.cookies.set(settings.auth_cookie_name, old_cookie)
    assert client.get(ME_URL).status_code == 401


def test_logout_without_session_is_idempotent(tmp_path: Path) -> None:
    app, _settings, _auth, _runtime = build_app(tmp_path)
    client = TestClient(app)
    response = client.post(LOGOUT_URL, headers={"origin": ORIGIN})
    assert response.status_code == 200
    assert response.json()["result"] == "logged_out"


def test_logout_requires_csrf_for_valid_session(tmp_path: Path) -> None:
    client, _app, _settings, _auth, _runtime, _csrf = authed_client(tmp_path)
    response = client.post(LOGOUT_URL, headers={"origin": ORIGIN})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_REJECTED"


def test_admin_revoke_sessions_invalidates_cookie(tmp_path: Path) -> None:
    client, _app, settings, auth_runtime, _runtime, _csrf = authed_client(tmp_path)
    with auth_runtime.connection() as connection:
        auth_runtime.identity_service(connection, settings).revoke_sessions("alice")
    assert client.get(ME_URL).status_code == 401


def test_disable_revokes_sessions_and_blocks_login(tmp_path: Path) -> None:
    client, _app, settings, auth_runtime, _runtime, _csrf = authed_client(tmp_path)
    with auth_runtime.connection() as connection:
        auth_runtime.identity_service(connection, settings).set_enabled(
            "alice", enabled=False
        )
    assert client.get(ME_URL).status_code == 401
    assert login(client).status_code == 401


def test_expired_session_rejected(tmp_path: Path) -> None:
    client, _app, settings, _auth, _runtime, _csrf = authed_client(tmp_path)
    raw = sqlite3.connect(settings.auth_database_path)
    raw.execute("UPDATE sessions SET idle_expires_at='2000-01-01T00:00:00+00:00'")
    raw.commit()
    raw.close()
    assert client.get(ME_URL).status_code == 401


def test_password_reset_revokes_sessions(tmp_path: Path) -> None:
    client, _app, settings, auth_runtime, _runtime, _csrf = authed_client(tmp_path)
    with auth_runtime.connection() as connection:
        service = auth_runtime.identity_service(connection, settings)
        reset = service.admin_reset_password("alice")

    # The old password must already be dead before the token is consumed.
    assert login(client).status_code == 401

    consumed = client.post(
        RESET_PASSWORD_URL,
        json={"token": reset.token, "password": NEW_PASSWORD},
        headers={"origin": ORIGIN},
    )
    assert consumed.status_code == 200
    assert consumed.json()["result"] == "password_updated"

    # Old session is gone; new password works.
    assert client.get(ME_URL).status_code == 401
    assert login(client, password=NEW_PASSWORD).status_code == 200


def test_reset_endpoint_rejects_activation_token(tmp_path: Path) -> None:
    """Purpose boundary: /reset-password consumes RESET_PASSWORD only."""
    app, settings, auth_runtime, _runtime = build_app(tmp_path)
    provisioned = provision_pending_user(auth_runtime, settings)
    client = TestClient(app)
    response = client.post(
        RESET_PASSWORD_URL,
        json={"token": provisioned.token, "password": ACTIVE_PASSWORD},
        headers={"origin": ORIGIN},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"
    # The token was NOT consumed by the rejected cross-purpose attempt.
    activate = client.post(
        ACTIVATE_URL,
        json={"token": provisioned.token, "password": ACTIVE_PASSWORD},
        headers={"origin": ORIGIN},
    )
    assert activate.status_code == 200


def test_activate_endpoint_rejects_reset_token(tmp_path: Path) -> None:
    """Purpose boundary: /activate consumes ACTIVATE_ACCOUNT only."""
    client, _app, settings, auth_runtime, _runtime, _csrf = authed_client(tmp_path)
    with auth_runtime.connection() as connection:
        service = auth_runtime.identity_service(connection, settings)
        reset = service.admin_reset_password("alice")
    response = client.post(
        ACTIVATE_URL,
        json={"token": reset.token, "password": NEW_PASSWORD},
        headers={"origin": ORIGIN},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"
    # The reset token still works on its own purpose-pinned endpoint.
    ok = client.post(
        RESET_PASSWORD_URL,
        json={"token": reset.token, "password": NEW_PASSWORD},
        headers={"origin": ORIGIN},
    )
    assert ok.status_code == 200


# ---------------------------------------------------------------------------
# Activation endpoint
# ---------------------------------------------------------------------------


def test_activation_via_api(tmp_path: Path) -> None:
    app, settings, auth_runtime, _runtime = build_app(tmp_path)
    provisioned = provision_pending_user(auth_runtime, settings)
    client = TestClient(app)

    response = client.post(
        ACTIVATE_URL,
        json={"token": provisioned.token, "password": ACTIVE_PASSWORD},
        headers={"origin": ORIGIN},
    )
    assert response.status_code == 200
    assert response.json()["result"] == "account_activated"
    assert login(client, username="bob").status_code == 200

    # Single-use token.
    replay = client.post(
        ACTIVATE_URL,
        json={"token": provisioned.token, "password": ACTIVE_PASSWORD},
        headers={"origin": ORIGIN},
    )
    assert replay.status_code == 400
    assert replay.json()["error"]["code"] == "INVALID_REQUEST"


def test_activation_rejects_short_password(tmp_path: Path) -> None:
    app, settings, auth_runtime, _runtime = build_app(tmp_path)
    provisioned = provision_pending_user(auth_runtime, settings)
    client = TestClient(app)
    response = client.post(
        ACTIVATE_URL,
        json={"token": provisioned.token, "password": "short"},
        headers={"origin": ORIGIN},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


# ---------------------------------------------------------------------------
# Password change
# ---------------------------------------------------------------------------


def test_change_password_revokes_other_sessions(tmp_path: Path) -> None:
    client, _app, _settings, _auth, _runtime, csrf = authed_client(tmp_path)
    other = TestClient(client.app)  # type: ignore[attr-defined]
    assert login(other).status_code == 200

    response = client.post(
        CHANGE_PASSWORD_URL,
        json={"current_password": ACTIVE_PASSWORD, "new_password": NEW_PASSWORD},
        headers=ask_headers(csrf),
    )
    assert response.status_code == 200
    assert response.json()["result"] == "password_changed"

    # Current session survives; the other session is revoked.
    assert client.get(ME_URL).status_code == 200
    assert other.get(ME_URL).status_code == 401
    assert login(client, password=NEW_PASSWORD).status_code == 200


def test_change_password_requires_current_password(tmp_path: Path) -> None:
    client, _app, _settings, _auth, _runtime, csrf = authed_client(tmp_path)
    response = client.post(
        CHANGE_PASSWORD_URL,
        json={"current_password": "wrong-current-password", "new_password": NEW_PASSWORD},
        headers=ask_headers(csrf),
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_CREDENTIALS"


def test_change_password_requires_authentication(tmp_path: Path) -> None:
    app, _settings, _auth, _runtime = build_app(tmp_path)
    client = TestClient(app)
    response = client.post(
        CHANGE_PASSWORD_URL,
        json={"current_password": ACTIVE_PASSWORD, "new_password": NEW_PASSWORD},
        headers={"origin": ORIGIN},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"


# ---------------------------------------------------------------------------
# Protected API v2: ask + SSE
# ---------------------------------------------------------------------------


def test_anonymous_v2_ask_rejected(tmp_path: Path) -> None:
    app, _settings, _auth, _runtime = build_app(tmp_path)
    client = TestClient(app)
    response = client.post(
        ASK_URL, json={"question": "你好"}, headers={"origin": ORIGIN}
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"


def test_anonymous_v2_sse_rejected_before_stream(tmp_path: Path) -> None:
    app, _settings, _auth, _runtime = build_app(tmp_path)
    client = TestClient(app)
    response = client.post(
        STREAM_URL, json={"question": "你好"}, headers={"origin": ORIGIN}
    )
    assert response.status_code == 401
    assert "application/json" in response.headers["content-type"]
    assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"


def test_authenticated_v2_ask_works(tmp_path: Path) -> None:
    client, _app, _settings, _auth, runtime, csrf = authed_client(tmp_path)
    response = client.post(
        ASK_URL, json={"question": "介绍一下这个项目"}, headers=ask_headers(csrf)
    )
    assert response.status_code == 200
    assert response.json()["status"] == "answered"
    assert runtime.service.call_count == 1


def test_authenticated_v2_sse_completes(tmp_path: Path) -> None:
    client, _app, _settings, _auth, runtime, csrf = authed_client(tmp_path)
    response = client.post(
        STREAM_URL, json={"question": "介绍一下这个项目"}, headers=ask_headers(csrf)
    )
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    assert "event: completed" in response.text
    assert runtime.service.call_count == 1


def test_sse_cannot_bypass_csrf(tmp_path: Path) -> None:
    client, _app, _settings, _auth, runtime, _csrf = authed_client(tmp_path)
    response = client.post(
        STREAM_URL,
        json={"question": "介绍一下这个项目"},
        headers={"origin": ORIGIN},  # no X-CSRF-Token
    )
    assert response.status_code == 403
    assert "application/json" in response.headers["content-type"]
    assert response.json()["error"]["code"] == "CSRF_REJECTED"
    assert runtime.service.call_count == 0


def test_ask_rejects_wrong_csrf_token(tmp_path: Path) -> None:
    client, _app, _settings, _auth, runtime, _csrf = authed_client(tmp_path)
    response = client.post(
        ASK_URL,
        json={"question": "介绍一下这个项目"},
        headers={"origin": ORIGIN, "x-csrf-token": "0" * 64},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_REJECTED"
    assert runtime.service.call_count == 0


def test_ask_rejects_cross_origin(tmp_path: Path) -> None:
    client, _app, _settings, _auth, runtime, csrf = authed_client(tmp_path)
    response = client.post(
        ASK_URL,
        json={"question": "介绍一下这个项目"},
        headers={"origin": "http://evil.example", "x-csrf-token": csrf},
    )
    assert response.status_code == 403
    # Missing Origin/Referer entirely is rejected as well.
    no_origin = client.post(ASK_URL, json={"question": "介绍"}, headers={"x-csrf-token": csrf})
    assert no_origin.status_code == 403
    assert runtime.service.call_count == 0


# ---------------------------------------------------------------------------
# Quota / kill switch / v1 retirement
# ---------------------------------------------------------------------------


def test_user_minute_rate_limit(tmp_path: Path) -> None:
    client, _app, _settings, _auth, _runtime, csrf = authed_client(
        tmp_path, auth_user_requests_per_minute=2
    )
    assert ask_once(client, csrf, "问题一") == 200
    assert ask_once(client, csrf, "问题二") == 200
    denied = client.post(ASK_URL, json={"question": "问题三"}, headers=ask_headers(csrf))
    assert denied.status_code == 429
    assert denied.json()["error"]["code"] == "QUOTA_EXCEEDED"
    assert "Retry-After" in denied.headers


def test_user_daily_quota(tmp_path: Path) -> None:
    client, _app, settings, auth_runtime, _runtime, csrf = authed_client(
        tmp_path, auth_user_requests_per_minute=100, auth_user_requests_per_day=2
    )
    assert ask_once(client, csrf, "问题一") == 200
    assert ask_once(client, csrf, "问题二") == 200
    denied = client.post(ASK_URL, json={"question": "问题三"}, headers=ask_headers(csrf))
    assert denied.status_code == 429
    assert denied.json()["error"]["code"] == "QUOTA_EXCEEDED"

    # The denial is audited.
    raw = sqlite3.connect(settings.auth_database_path)
    events = [row[0] for row in raw.execute("SELECT event FROM audit_events")]
    raw.close()
    assert "quota_exceeded" in events


def test_llm_kill_switch_blocks_ask_but_not_auth(tmp_path: Path) -> None:
    client, _app, _settings, _auth, runtime, csrf = authed_client(tmp_path, llm_enabled=False)
    denied = client.post(ASK_URL, json={"question": "你好"}, headers=ask_headers(csrf))
    assert denied.status_code == 503
    assert denied.json()["error"]["code"] == "SERVICE_DISABLED"
    denied_stream = client.post(
        STREAM_URL, json={"question": "你好"}, headers=ask_headers(csrf)
    )
    assert denied_stream.status_code == 503
    assert runtime.service.call_count == 0
    # Auth surfaces keep working.
    assert client.get(ME_URL).status_code == 200
    assert client.post(LOGOUT_URL, headers=ask_headers(csrf)).status_code == 200


def test_v1_retirement_returns_410(tmp_path: Path) -> None:
    app, settings, auth_runtime, runtime = build_app(tmp_path, api_v1_retired=True)
    provision_active_user(auth_runtime, settings)
    client = TestClient(app)

    ask = client.post("/api/v1/ask", json={"question": "你好"})
    assert ask.status_code == 410
    assert ask.json()["error"]["code"] == "API_RETIRED"

    stream = client.post("/api/v1/ask/stream", json={"question": "你好"})
    assert stream.status_code == 410
    assert stream.json()["error"]["code"] == "API_RETIRED"
    assert runtime.service.call_count == 0


def test_v1_still_serves_when_not_retired(tmp_path: Path) -> None:
    app, _settings, _auth, runtime = build_app(tmp_path)
    client = TestClient(app)
    response = client.post("/api/v1/ask", json={"question": "你好"})
    assert response.status_code == 200
    assert runtime.service.call_count == 1


def test_audit_log_contains_no_secrets(tmp_path: Path) -> None:
    client, _app, settings, auth_runtime, _runtime, csrf = authed_client(
        tmp_path, auth_user_requests_per_minute=1
    )
    client.post(ASK_URL, json={"question": "问题一"}, headers=ask_headers(csrf))
    client.post(ASK_URL, json={"question": "问题二"}, headers=ask_headers(csrf))
    raw = sqlite3.connect(settings.auth_database_path)
    dump = "\n".join(raw.iterdump())
    raw.close()
    assert ACTIVE_PASSWORD not in dump
    assert client.cookies.get(settings.auth_cookie_name) not in dump


# ---------------------------------------------------------------------------
# Hardening review: gate precedence, quota accounting, config fail-fast
# ---------------------------------------------------------------------------


def test_anonymous_gets_401_even_when_llm_disabled(tmp_path: Path) -> None:
    """Security boundary precedes capability policy: anonymous callers must
    never learn the kill-switch state; they always get 401 first."""
    app, _settings, _auth, runtime = build_app(tmp_path, llm_enabled=False)
    client = TestClient(app)
    response = client.post(ASK_URL, json={"question": "你好"}, headers={"origin": ORIGIN})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"
    stream = client.post(
        STREAM_URL, json={"question": "你好"}, headers={"origin": ORIGIN}
    )
    assert stream.status_code == 401
    assert stream.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"
    assert runtime.service.call_count == 0


def _usage_total(settings: Settings) -> int:
    raw = sqlite3.connect(settings.auth_database_path)
    total = raw.execute("SELECT COALESCE(SUM(requests), 0) FROM usage").fetchone()[0]
    raw.close()
    return int(total)


def test_rejected_requests_do_not_consume_quota(tmp_path: Path) -> None:
    """Only requests entering the cost-bearing workflow count: auth
    failures and CSRF failures must not touch the usage counters."""
    client, _app, settings, _auth, runtime, csrf = authed_client(
        tmp_path, auth_user_requests_per_minute=100
    )
    anonymous = TestClient(build_app(tmp_path)[0])
    assert anonymous.post(
        ASK_URL, json={"question": "你好"}, headers={"origin": ORIGIN}
    ).status_code == 401
    assert client.post(
        ASK_URL, json={"question": "你好"}, headers={"origin": ORIGIN}  # no CSRF
    ).status_code == 403
    assert ask_once(client, csrf, "正常问题") == 200
    assert _usage_total(settings) == 1  # only the real request counted
    assert runtime.service.call_count == 1


def test_service_busy_does_not_consume_quota(tmp_path: Path) -> None:
    """SERVICE_BUSY is rejected before quota accounting."""
    settings = Settings(
        auth_database_path=tmp_path / "auth.db",
        auth_cookie_name="zglab_session_test",
        auth_cookie_secure=False,
        auth_public_base_url=ORIGIN,
    )
    auth_runtime = AuthRuntime.from_settings(settings)
    runtime = FakeRuntime(settings=settings)
    app = create_app(
        runtime=runtime,
        settings=settings,
        auth_runtime=auth_runtime,
        concurrency_guard=ConcurrencyGuard(max_concurrent=0),  # always busy
    )
    provision_active_user(auth_runtime, settings)
    client = TestClient(app)
    response = login(client)
    csrf = response.json()["csrf_token"]
    busy = client.post(ASK_URL, json={"question": "你好"}, headers=ask_headers(csrf))
    assert busy.status_code == 503
    assert busy.json()["error"]["code"] == "SERVICE_BUSY"
    assert _usage_total(settings) == 0
    assert runtime.service.call_count == 0


def test_quota_exceeded_request_does_not_count_itself(tmp_path: Path) -> None:
    client, _app, settings, _auth, _runtime, csrf = authed_client(
        tmp_path, auth_user_requests_per_minute=1
    )
    assert ask_once(client, csrf, "问题一") == 200
    denied = client.post(ASK_URL, json={"question": "问题二"}, headers=ask_headers(csrf))
    assert denied.status_code == 429
    # The rejected attempt rolled back: usage stays at exactly 1.
    assert _usage_total(settings) == 1


def test_host_cookie_requires_secure_flag() -> None:
    """The weak __Host- + Secure=false combination must fail fast."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(auth_cookie_name="__Host-zglab_session", auth_cookie_secure=False)
    # Production posture stays valid.
    prod = Settings(auth_cookie_name="__Host-zglab_session", auth_cookie_secure=True)
    assert prod.auth_cookie_secure is True
    # Dev-only plain cookie name over HTTP is allowed.
    dev = Settings(auth_cookie_name="zglab_session_dev", auth_cookie_secure=False)
    assert dev.auth_cookie_name == "zglab_session_dev"


def test_production_startup_requires_v1_retirement() -> None:
    """Fail-closed: a production process refuses to serve while the
    anonymous v1 ask endpoints could still consume the LLM."""
    unsafe = Settings(env="production", api_v1_retired=False)
    with pytest.raises(RuntimeError):
        validate_production_security_settings(unsafe)
    safe = Settings(env="production", api_v1_retired=True)
    validate_production_security_settings(safe)  # no raise
    # Local regression keeps the historical v1 behavior.
    development = Settings(env="development", api_v1_retired=False)
    validate_production_security_settings(development)  # no raise
