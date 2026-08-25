"""Argon2id password hashing and the length-first password policy.

Only mature library implementations are used (argon2-cffi); the database
stores the encoded hash string only. Plaintext passwords are never logged
and never leave the request scope.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from zglab_rag.auth.errors import PasswordPolicyError

# Shared hasher instance; argon2-cffi's defaults follow current OWASP
# Argon2id guidance and the encoded hash embeds its own parameters, so
# future parameter upgrades remain verifiable.
_HASHER = PasswordHasher()


def hash_password(password: str) -> str:
    """Return the Argon2id hash string for a policy-valid password."""
    return _HASHER.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """Return True when the password matches the stored Argon2id hash.

    Malformed or foreign hash strings are treated as a verification
    failure instead of an internal error: the caller maps everything to
    the same public InvalidCredentials outcome.
    """
    try:
        return _HASHER.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    except Exception:
        return False


def dummy_verify(password: str) -> None:
    """Burn comparable CPU time when the username does not exist.

    Reduces trivial timing enumeration of valid usernames. The password is
    verified against a fixed, precomputed hash and the result is ignored.
    """
    try:
        _HASHER.verify(_DUMMY_HASH, password)
    except Exception:
        pass


_DUMMY_HASH = _HASHER.hash("zglab-rag-timing-equalization-not-a-password")


def validate_password_policy(
    password: str, *, min_length: int = 12, max_length: int = 128
) -> None:
    """Enforce the length-first password policy.

    No composition rules (uppercase / digit / symbol) on purpose; length
    is the primary entropy lever. The max bound also protects the Argon2id
    hash step from oversized-input DoS.
    """
    if len(password) < min_length:
        raise PasswordPolicyError(f"Password must be at least {min_length} characters")
    if len(password) > max_length:
        raise PasswordPolicyError(f"Password must be at most {max_length} characters")
