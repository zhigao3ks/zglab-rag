"""Protocol-layer security helpers for the Phase 11 authenticated API.

This module wires the framework-independent auth package into FastAPI:
client identity resolution, Origin/Referer validation, the auth runtime
(handle to auth.db + login throttle) and cookie construction. It never
implements identity logic itself; all state changes go through the auth
services.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from urllib.parse import urlparse

from fastapi import Request

from zglab_rag.auth.database import AuthDatabase
from zglab_rag.auth.errors import OriginError
from zglab_rag.auth.identity import IdentityConfig, IdentityService
from zglab_rag.auth.quota import QuotaConfig
from zglab_rag.auth.session import SessionConfig, SessionService
from zglab_rag.auth.throttle import LoginThrottle, LoginThrottleConfig
from zglab_rag.config import Settings


def get_client_id(request: Request) -> str:
    """Extract client identity for throttling / audit hints.

    ``X-Forwarded-For`` is trusted only when the direct peer is an
    explicitly configured reverse proxy, so public callers cannot choose
    their own identity.
    """
    peer = request.client.host if request.client else None
    if peer and peer in request.app.state.settings.api_trusted_proxy_ips:
        forwarded_for = request.headers.get("x-forwarded-for", "")
        first = forwarded_for.split(",", maxsplit=1)[0].strip()
        if first:
            return first
    if peer:
        return peer
    return "unknown"


def _normalize_origin(value: str) -> str:
    parsed = urlparse(value.strip())
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def allowed_origins(settings: Settings) -> set[str]:
    """Origins allowed for state-changing requests.

    Defaults to the configured public base URL; an explicit allowlist wins.
    """
    configured = settings.auth_allowed_origins or [settings.auth_public_base_url]
    return {_normalize_origin(origin) for origin in configured if _normalize_origin(origin)}


def verify_state_change_origin(request: Request, settings: Settings) -> None:
    """Reject cross-origin state-changing requests (Origin or Referer).

    Missing both headers is a failure: same-origin SPA fetches always send
    at least one, and refusing the ambiguous case is the safe default.
    """
    allowed = allowed_origins(settings)
    origin = request.headers.get("origin")
    if origin:
        if _normalize_origin(origin) in allowed:
            return
        raise OriginError("Request origin rejected")
    referer = request.headers.get("referer")
    if referer and _normalize_origin(referer) in allowed:
        return
    raise OriginError("Request origin rejected")


@dataclass
class AuthRuntime:
    """App-scoped auth components: the auth.db handle and login throttle.

    Connections are short-lived and opened per operation; the throttle is
    process-local state shared across requests.
    """

    database: AuthDatabase
    throttle: LoginThrottle = field(
        default_factory=lambda: LoginThrottle(LoginThrottleConfig())
    )

    @classmethod
    def from_settings(cls, settings: Settings) -> AuthRuntime:
        return cls(
            database=AuthDatabase(settings.auth_database_path),
            throttle=LoginThrottle(
                LoginThrottleConfig(
                    per_ip_attempts=settings.auth_login_per_ip_attempts,
                    per_ip_window_seconds=settings.auth_login_per_ip_window_seconds,
                    per_username_attempts=settings.auth_login_per_username_attempts,
                    per_username_window_seconds=settings.auth_login_per_username_window_seconds,
                )
            ),
        )

    @contextmanager
    def connection(self):
        connection: sqlite3.Connection | None = None
        try:
            connection = self.database.connect(initialize=True)
            yield connection
        finally:
            if connection is not None:
                connection.close()

    def verify_ready(self) -> None:
        """Fail-fast readiness probe: auth.db must open with a valid schema."""
        with self.connection() as connection:
            AuthDatabase.schema_version(connection)

    @staticmethod
    def identity_config(settings: Settings) -> IdentityConfig:
        return IdentityConfig(
            password_min_length=settings.auth_password_min_length,
            password_max_length=settings.auth_password_max_length,
            activation_token_ttl=timedelta(hours=settings.auth_activation_token_hours),
            reset_token_ttl=timedelta(hours=settings.auth_reset_token_hours),
        )

    @staticmethod
    def session_config(settings: Settings) -> SessionConfig:
        return SessionConfig(
            idle_timeout=timedelta(hours=settings.auth_session_idle_timeout_hours),
            absolute_timeout=timedelta(hours=settings.auth_session_absolute_timeout_hours),
        )

    @staticmethod
    def quota_config(settings: Settings) -> QuotaConfig:
        return QuotaConfig(
            requests_per_minute=settings.auth_user_requests_per_minute,
            requests_per_day=settings.auth_user_requests_per_day,
        )

    @staticmethod
    def web_quota_config(settings: Settings) -> QuotaConfig:
        """Phase 12D: independent cost bucket for web research requests."""
        return QuotaConfig(
            requests_per_minute=settings.web_research_requests_per_minute,
            requests_per_day=settings.web_research_requests_per_day,
        )

    @staticmethod
    def agent_quota_config(settings: Settings) -> QuotaConfig:
        return QuotaConfig(
            requests_per_minute=settings.agent_requests_per_minute,
            requests_per_day=settings.agent_requests_per_day,
        )

    def identity_service(self, connection: sqlite3.Connection, settings: Settings):
        from zglab_rag.auth.audit import AuditLogger

        return IdentityService(
            connection, AuditLogger(connection), self.identity_config(settings)
        )

    def session_service(self, connection: sqlite3.Connection, settings: Settings) -> SessionService:
        from zglab_rag.auth.audit import AuditLogger

        return SessionService(
            connection, AuditLogger(connection), self.session_config(settings)
        )


def session_cookie_kwargs(settings: Settings) -> dict:
    """Cookie attributes shared by every place the session cookie is written.

    Host-only semantics: no Domain attribute, Path=/, HttpOnly always,
    SameSite=Lax and Secure in production. The plaintext session token
    exists only in this cookie and in the browser.
    """
    absolute_seconds = int(settings.auth_session_absolute_timeout_hours * 3600)
    return {
        "max_age": absolute_seconds,
        "httponly": True,
        "secure": settings.auth_cookie_secure,
        "samesite": "lax",
        "path": "/",
    }
