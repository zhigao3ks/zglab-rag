"""Server-side session authentication (Phase 11B).

Sessions are random high-entropy tokens stored as SHA-256 digests only;
the plaintext token lives exclusively in the HttpOnly cookie. Each session
carries an idle timeout, an absolute timeout and a CSRF secret bound to
the server-side session record.
"""

from __future__ import annotations

import hashlib
import hmac
import sqlite3
from dataclasses import dataclass
from datetime import timedelta

from zglab_rag.auth.audit import AuditLogger
from zglab_rag.auth.errors import (
    AccountUnavailableError,
    CsrfError,
    InvalidCredentialsError,
    SessionError,
)
from zglab_rag.auth.models import (
    AuditEvent,
    AuthenticatedPrincipal,
    CredentialStatus,
    SessionRecord,
    UserRole,
    UserStatus,
)
from zglab_rag.auth.passwords import (
    dummy_verify,
    hash_password,
    validate_password_policy,
    verify_password,
)
from zglab_rag.auth.repositories import (
    SessionRepository,
    UserRepository,
    utc_now,
)
from zglab_rag.auth.tokens import (
    generate_csrf_secret,
    generate_session_token,
    token_digest,
    token_digests_equal,
)


@dataclass(frozen=True, slots=True)
class SessionConfig:
    idle_timeout: timedelta = timedelta(days=7)
    absolute_timeout: timedelta = timedelta(days=30)


@dataclass(frozen=True, slots=True)
class LoginResult:
    """Outcome of a successful login; the token is returned exactly once."""

    principal: AuthenticatedPrincipal
    session_token: str
    csrf_token: str


def csrf_token_for(session: SessionRecord) -> str:
    """Derive the session-bound CSRF token from the session CSRF secret.

    The secret stays server-side; the derived token is what the SPA sends
    back in the X-CSRF-Token header. Binding to the session id means a
    token from one session is never valid for another.
    """
    message = f"csrf:{session.id}".encode()
    return hmac.new(session.csrf_secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


class SessionService:
    def __init__(
        self,
        connection: sqlite3.Connection,
        audit: AuditLogger,
        config: SessionConfig | None = None,
    ) -> None:
        self.connection = connection
        self.sessions = SessionRepository(connection)
        self.users = UserRepository(connection)
        self.audit = audit
        self.config = config or SessionConfig()

    # -- login / logout ------------------------------------------------------

    def login(
        self,
        raw_username: str,
        password: str,
        *,
        client_hint: str | None = None,
        request_id: str | None = None,
    ) -> LoginResult:
        """Authenticate and create a server-side session.

        All failure paths (unknown user, disabled account, wrong password,
        pending account) raise the same InvalidCredentialsError publicly;
        the exact cause is only visible in audit records.
        """
        username = raw_username.strip().lower()
        user = self.users.get_by_username(username)
        if user is None:
            # Equalize timing with a real verification so username existence
            # is not trivially enumerable.
            dummy_verify(password)
            self.audit.record(
                AuditEvent.LOGIN_FAILURE,
                result="unknown_user",
                request_id=request_id,
                client_hint=client_hint,
            )
            raise InvalidCredentialsError("Invalid username or password")

        password_hash = user.password_hash
        if password_hash:
            verified = verify_password(password_hash, password)
        else:
            dummy_verify(password)
            verified = False
        if not verified:
            self.audit.record(
                AuditEvent.LOGIN_FAILURE,
                result="invalid_password",
                user_id=user.id,
                request_id=request_id,
                client_hint=client_hint,
            )
            raise InvalidCredentialsError("Invalid username or password")
        if user.credential_status != CredentialStatus.VALID:
            # A password reset is in flight: the old password is already
            # dead even though the reset token has not been consumed yet.
            # Public response stays identical to every other failure.
            self.audit.record(
                AuditEvent.LOGIN_FAILURE,
                result="credential_reset_required",
                user_id=user.id,
                request_id=request_id,
                client_hint=client_hint,
            )
            raise InvalidCredentialsError("Invalid username or password")
        if user.status != UserStatus.ACTIVE:
            # Password was correct but the account cannot log in; the public
            # error stays identical to avoid leaking account state.
            self.audit.record(
                AuditEvent.LOGIN_FAILURE,
                result=f"account_{user.status.value.lower()}",
                user_id=user.id,
                request_id=request_id,
                client_hint=client_hint,
            )
            raise InvalidCredentialsError("Invalid username or password")

        now = utc_now()
        session_token = generate_session_token()
        session = self.sessions.create(
            SessionRecord(
                id=0,
                user_id=user.id,
                session_hash=token_digest(session_token),
                csrf_secret=generate_csrf_secret(),
                created_at=now,
                last_seen_at=now,
                idle_expires_at=now + self.config.idle_timeout,
                absolute_expires_at=now + self.config.absolute_timeout,
                revoked_at=None,
                client_hint=client_hint,
            )
        )
        self.audit.record(
            AuditEvent.LOGIN_SUCCESS,
            result="ok",
            user_id=user.id,
            request_id=request_id,
            client_hint=client_hint,
        )
        principal = AuthenticatedPrincipal(
            user_id=user.id, username=user.username, role=user.role, session_id=session.id
        )
        return LoginResult(
            principal=principal, session_token=session_token, csrf_token=csrf_token_for(session)
        )

    def logout(
        self, session_token: str, *, request_id: str | None = None
    ) -> None:
        """Revoke the session immediately; the old cookie becomes useless."""
        session = self._find_valid_session(session_token)
        if session is None:
            # Already gone: logout is idempotent.
            return
        self.sessions.revoke(session.id)
        self.audit.record(
            AuditEvent.LOGOUT, result="ok", user_id=session.user_id, request_id=request_id
        )

    # -- resolution -------------------------------------------------------------

    def resolve_session(
        self, session_token: str | None
    ) -> tuple[AuthenticatedPrincipal, SessionRecord]:
        """Validate a bearer token and return the principal + session.

        Raises SessionError when the token is missing, unknown, revoked or
        past either timeout; the user must also still be ACTIVE, so admin
        disable takes effect on the very next request.
        """
        if not session_token:
            raise SessionError("Authentication required")
        session = self._find_valid_session(session_token)
        if session is None:
            raise SessionError("Authentication required")
        user = self.users.get_by_id(session.user_id)
        if user is None or user.status != UserStatus.ACTIVE:
            raise SessionError("Authentication required")

        now = utc_now()
        self.sessions.touch(session.id, now + self.config.idle_timeout, now=now)
        principal = AuthenticatedPrincipal(
            user_id=user.id, username=user.username, role=user.role, session_id=session.id
        )
        return principal, session

    def _find_valid_session(self, session_token: str) -> SessionRecord | None:
        if not session_token or len(session_token) > 256:
            return None
        session = self.sessions.find_by_hash(token_digest(session_token))
        if session is None or session.revoked_at is not None:
            return None
        now = utc_now()
        if now >= session.idle_expires_at or now >= session.absolute_expires_at:
            return None
        return session

    # -- CSRF -----------------------------------------------------------------

    @staticmethod
    def verify_csrf(session: SessionRecord, presented_token: str | None) -> None:
        """Validate the session-bound CSRF token for a state-changing request."""
        if not presented_token:
            raise CsrfError("Missing CSRF token")
        expected = csrf_token_for(session)
        if not token_digests_equal(expected, presented_token):
            raise CsrfError("Invalid CSRF token")

    # -- password change ----------------------------------------------------------

    def change_password(
        self,
        session_token: str,
        current_password: str,
        new_password: str,
        *,
        min_length: int = 12,
        max_length: int = 128,
        request_id: str | None = None,
    ) -> AuthenticatedPrincipal:
        """Change the password of the authenticated user.

        The current session survives (the user is actively authenticated);
        every other session is revoked immediately.
        """
        principal, session = self.resolve_session(session_token)
        user = self.users.get_by_id(principal.user_id)
        if user is None or not user.password_hash:
            raise SessionError("Authentication required")
        if not verify_password(user.password_hash, current_password):
            self.audit.record(
                AuditEvent.LOGIN_FAILURE,
                result="change_password_invalid_current",
                user_id=user.id,
                request_id=request_id,
            )
            raise InvalidCredentialsError("Current password is incorrect")
        validate_password_policy(new_password, min_length=min_length, max_length=max_length)
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self.users.set_password(user.id, hash_password(new_password))
            self.sessions.revoke_all_for_user(user.id, except_session_id=session.id)
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        self.audit.record(
            AuditEvent.PASSWORD_CHANGED,
            result="ok_self",
            user_id=user.id,
            request_id=request_id,
        )
        return principal


def require_authenticated_role(principal: AuthenticatedPrincipal, *roles: UserRole) -> None:
    """Default-deny authorization helper for future role-gated endpoints."""
    if principal.role not in roles:
        raise AccountUnavailableError("Insufficient permissions")
