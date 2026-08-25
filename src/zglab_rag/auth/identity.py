"""Identity lifecycle service (Phase 11A).

Admin provisioning, single-use activation, admin-initiated password reset
and account state transitions. There is no public registration: every user
starts as PENDING from an admin CLI action and becomes ACTIVE only through
a one-time activation URL. The admin never learns the user's final password.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import timedelta

from zglab_rag.auth.audit import AuditLogger
from zglab_rag.auth.errors import (
    AccountUnavailableError,
    DuplicateUsernameError,
    TokenExpiredError,
    TokenInvalidError,
    UsernamePolicyError,
    UserNotFoundError,
)
from zglab_rag.auth.models import (
    AuditEvent,
    CredentialStatus,
    CredentialTokenRecord,
    TokenPurpose,
    UserRecord,
    UserRole,
    UserStatus,
)
from zglab_rag.auth.passwords import hash_password, validate_password_policy
from zglab_rag.auth.repositories import (
    CredentialTokenRepository,
    SessionRepository,
    UserRepository,
    format_timestamp,
    utc_now,
)
from zglab_rag.auth.tokens import generate_credential_token, token_digest

# Normalized usernames: lowercase ASCII letters/digits with . _ - allowed,
# 2-32 chars, starting with an alphanumeric. Case-folding makes `Admin`,
# `admin` and `ADMIN` a single account.
USERNAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{1,31}$")


def normalize_username(raw_username: str) -> str:
    """Normalize and validate a username; raises UsernamePolicyError."""
    normalized = raw_username.strip().lower()
    if not USERNAME_PATTERN.fullmatch(normalized):
        raise UsernamePolicyError(
            "Username must be 2-32 chars of a-z, 0-9, '.', '_' or '-', "
            "starting with a letter or digit"
        )
    return normalized


@dataclass(frozen=True, slots=True)
class IdentityConfig:
    password_min_length: int = 12
    password_max_length: int = 128
    activation_token_ttl: timedelta = timedelta(hours=24)
    reset_token_ttl: timedelta = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class ProvisionedUser:
    """Result of admin provisioning; the plaintext token is returned once."""

    user: UserRecord
    token: str
    purpose: TokenPurpose


class IdentityService:
    """All identity mutations run on one caller-owned auth.db connection."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        audit: AuditLogger,
        config: IdentityConfig | None = None,
    ) -> None:
        self.connection = connection
        self.users = UserRepository(connection)
        self.tokens = CredentialTokenRepository(connection)
        self.sessions = SessionRepository(connection)
        self.audit = audit
        self.config = config or IdentityConfig()

    # -- provisioning ------------------------------------------------------

    def provision_user(
        self,
        raw_username: str,
        *,
        role: UserRole = UserRole.USER,
        created_by: str = "cli",
        request_id: str | None = None,
    ) -> ProvisionedUser:
        """Create a PENDING user and a single-use activation token."""
        username = normalize_username(raw_username)
        now = utc_now()
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            if self.users.get_by_username(username) is not None:
                raise DuplicateUsernameError(f"Username '{username}' already exists")
            user = self.users.create(username=username, role=role, created_by=created_by, now=now)
            token = self._issue_token(user.id, TokenPurpose.ACTIVATE_ACCOUNT, now=now)
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        self.audit.record(
            AuditEvent.ACCOUNT_CREATED,
            result="ok",
            user_id=user.id,
            request_id=request_id,
            client_hint=created_by,
        )
        return ProvisionedUser(user=user, token=token, purpose=TokenPurpose.ACTIVATE_ACCOUNT)

    # -- token consumption ---------------------------------------------------

    def _lookup_token(
        self, token: str, purpose: TokenPurpose | None = None
    ) -> CredentialTokenRecord:
        if not token or len(token) > 256:
            raise TokenInvalidError("Invalid credential token")
        record = self.tokens.find_by_hash(token_digest(token))
        if record is None or record.revoked_at is not None or record.consumed_at is not None:
            raise TokenInvalidError("Invalid credential token")
        # Purpose isolation: an activation token can never be used as a
        # password reset token and vice versa.
        if purpose is not None and record.purpose != purpose:
            raise TokenInvalidError("Invalid credential token")
        if record.expires_at <= utc_now():
            raise TokenExpiredError("Credential token has expired")
        return record

    def _consume_token_atomic(self, token_id: int) -> None:
        """Mark a token consumed exactly once; a lost race is a hard failure."""
        cursor = self.connection.execute(
            "UPDATE credential_tokens SET consumed_at=? WHERE id=? AND consumed_at IS NULL",
            (format_timestamp(utc_now()), token_id),
        )
        if cursor.rowcount != 1:
            raise TokenInvalidError("Credential token has already been used")

    def activate_account(
        self,
        token: str,
        password: str,
        *,
        request_id: str | None = None,
    ) -> UserRecord:
        """Consume an ACTIVATE_ACCOUNT token and set the first password.

        Purpose boundary: a RESET_PASSWORD token is rejected here; the
        reset flow has its own method and API endpoint.
        """
        validate_password_policy(
            password,
            min_length=self.config.password_min_length,
            max_length=self.config.password_max_length,
        )
        record = self._lookup_token(token, TokenPurpose.ACTIVATE_ACCOUNT)
        password_hash = hash_password(password)
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self._consume_token_atomic(record.id)
            user = self.users.get_by_id(record.user_id)
            if user is None:
                raise TokenInvalidError("Invalid credential token")
            if user.status not in (UserStatus.PENDING, UserStatus.DISABLED):
                raise TokenInvalidError("Invalid credential token")
            self.users.set_password(user.id, password_hash)
            self.users.set_credential_status(user.id, CredentialStatus.VALID)
            self.users.mark_activated(user.id)
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        self.audit.record(
            AuditEvent.ACCOUNT_ACTIVATED, result="ok", user_id=user.id, request_id=request_id
        )
        return self.users.get_by_id(user.id)  # type: ignore[return-value]

    def reset_password_with_token(
        self,
        token: str,
        password: str,
        *,
        request_id: str | None = None,
    ) -> UserRecord:
        """Consume a RESET_PASSWORD token, set a new password, revoke sessions.

        Purpose boundary: an ACTIVATE_ACCOUNT token is rejected here.
        Completing the reset clears RESET_REQUIRED; until then the old
        password cannot authenticate (set when the token was issued).
        """
        validate_password_policy(
            password,
            min_length=self.config.password_min_length,
            max_length=self.config.password_max_length,
        )
        record = self._lookup_token(token, TokenPurpose.RESET_PASSWORD)
        password_hash = hash_password(password)
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self._consume_token_atomic(record.id)
            user = self.users.get_by_id(record.user_id)
            if user is None:
                raise TokenInvalidError("Invalid credential token")
            if user.status != UserStatus.ACTIVE:
                raise AccountUnavailableError("Account is not active")
            self.users.set_password(user.id, password_hash)
            self.users.set_credential_status(user.id, CredentialStatus.VALID)
            self.sessions.revoke_all_for_user(user.id)
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        self.audit.record(
            AuditEvent.PASSWORD_CHANGED,
            result="ok_via_reset_token",
            user_id=user.id,
            request_id=request_id,
        )
        return self.users.get_by_id(user.id)  # type: ignore[return-value]

    # -- admin operations ----------------------------------------------------

    def admin_reset_password(
        self,
        raw_username: str,
        *,
        request_id: str | None = None,
    ) -> ProvisionedUser:
        """Issue a one-time reset token; all sessions are revoked immediately.

        Security-critical: the account's credential is marked
        RESET_REQUIRED in the same transaction, so the OLD password stops
        authenticating right now — not only when the token is consumed.
        The account becomes able to log in again only after the reset
        token is used (fail-closed if the token expires unconsumed).

        For a still-PENDING user this re-issues an activation token instead,
        since there is no existing password to reset.
        """
        username = normalize_username(raw_username)
        user = self.users.get_by_username(username)
        if user is None:
            raise UserNotFoundError(f"Username '{username}' does not exist")
        now = utc_now()
        purpose = (
            TokenPurpose.ACTIVATE_ACCOUNT
            if user.status == UserStatus.PENDING
            else TokenPurpose.RESET_PASSWORD
        )
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self.sessions.revoke_all_for_user(user.id)
            if purpose == TokenPurpose.RESET_PASSWORD:
                self.users.set_credential_status(
                    user.id, CredentialStatus.RESET_REQUIRED
                )
            token = self._issue_token(user.id, purpose, now=now)
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        self.audit.record(
            AuditEvent.PASSWORD_RESET_REQUESTED_BY_ADMIN,
            result="ok",
            user_id=user.id,
            request_id=request_id,
        )
        return ProvisionedUser(user=user, token=token, purpose=purpose)

    def set_enabled(
        self,
        raw_username: str,
        enabled: bool,
        *,
        request_id: str | None = None,
    ) -> UserRecord:
        username = normalize_username(raw_username)
        user = self.users.get_by_username(username)
        if user is None:
            raise UserNotFoundError(f"Username '{username}' does not exist")
        if enabled:
            if user.status != UserStatus.DISABLED:
                raise AccountUnavailableError(
                    "Only DISABLED accounts can be enabled"
                )
            self.users.set_status(user.id, UserStatus.ACTIVE)
            self.audit.record(
                AuditEvent.ACCOUNT_ENABLED, result="ok", user_id=user.id, request_id=request_id
            )
        else:
            self.users.set_status(user.id, UserStatus.DISABLED)
            revoked = self.sessions.revoke_all_for_user(user.id)
            self.audit.record(
                AuditEvent.ACCOUNT_DISABLED, result="ok", user_id=user.id, request_id=request_id
            )
            if revoked:
                self.audit.record(
                    AuditEvent.SESSION_REVOKED,
                    result=f"ok_count={revoked}",
                    user_id=user.id,
                    request_id=request_id,
                )
        return self.users.get_by_id(user.id)  # type: ignore[return-value]

    def revoke_sessions(self, raw_username: str, *, request_id: str | None = None) -> int:
        username = normalize_username(raw_username)
        user = self.users.get_by_username(username)
        if user is None:
            raise UserNotFoundError(f"Username '{username}' does not exist")
        revoked = self.sessions.revoke_all_for_user(user.id)
        self.audit.record(
            AuditEvent.SESSION_REVOKED,
            result=f"ok_count={revoked}",
            user_id=user.id,
            request_id=request_id,
        )
        return revoked

    # -- helpers ---------------------------------------------------------------

    def _issue_token(
        self, user_id: int, purpose: TokenPurpose, *, now=None
    ) -> str:
        """Create a new single-use token, superseding previous ones.

        Returns the plaintext token exactly once; only its SHA-256 digest
        is persisted.
        """
        moment = now or utc_now()
        ttl = (
            self.config.activation_token_ttl
            if purpose == TokenPurpose.ACTIVATE_ACCOUNT
            else self.config.reset_token_ttl
        )
        self.tokens.revoke_superseded(user_id, purpose)
        plaintext = generate_credential_token()
        self.tokens.create(
            CredentialTokenRecord(
                id=0,
                user_id=user_id,
                purpose=purpose,
                token_hash=token_digest(plaintext),
                created_at=moment,
                expires_at=moment + ttl,
                consumed_at=None,
                revoked_at=None,
            )
        )
        return plaintext
